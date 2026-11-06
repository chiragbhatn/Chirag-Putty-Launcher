"""Chirag Putty Launcher: a native window around the web UI in launcher_ui/.

The LauncherApi class is the bridge the UI calls. Every public method returns
JSON-friendly data; expected problems come back as {"__error": "..."} so the UI
can show them as messages instead of crashing.
"""
from __future__ import annotations

import functools
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Callable

import launcher_core as core
from launcher_core import (  # re-exported for tests and older imports
    APP_NAME,
    APP_THEMES,
    APP_VERSION,
    ConnectionProfile,
    Device,
    DeviceStore,
    NetBoxClient,
    NetBoxConnection,
    NetBoxFetchError,
    ProfileStore,
)

EXPECTED_ERRORS = (ValueError, OSError, NetBoxFetchError, json.JSONDecodeError)


def resource_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "launcher_ui"


def friendly(method: Callable) -> Callable:
    """Turn expected exceptions into {"__error": message} for the UI."""

    @functools.wraps(method)
    def wrapper(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except EXPECTED_ERRORS as exc:
            return {"__error": str(exc) or exc.__class__.__name__}

    return wrapper


class LauncherApi:
    def __init__(self, profile_store: ProfileStore | None = None, device_store: DeviceStore | None = None,
                 emit: Callable[[str, dict], None] | None = None, dialogs: Callable | None = None):
        self._lock = threading.RLock()
        self._profiles = profile_store or ProfileStore()
        self._store = device_store or DeviceStore()
        self._emit_fn = emit or (lambda event, payload: None)
        self._dialogs = dialogs  # callable(kind, **options) -> path | None
        self._sync_running = False
        self._check_running = False
        self._check_cancel = threading.Event()
        self._activity: list[dict] = []
        self._activity_seq = 0
        self._scheduler_started = False

    # ------------------------------------------------------------------ plumbing
    def _emit(self, event: str, payload: dict | None = None) -> None:
        try:
            self._emit_fn(event, payload or {})
        except Exception:
            pass

    def _notify(self, level: str, title: str, body: str = "", key: str = "") -> dict:
        with self._lock:
            self._activity_seq += 1
            item = {"id": self._activity_seq, "level": level, "title": title, "body": body, "key": key, "at": core.now_iso()}
            self._activity.insert(0, item)
            self._activity = self._activity[:100]
        self._emit("notify", item)
        return item

    def _pick(self, kind: str, path: str | None = None, **options) -> str | None:
        if path:
            return path
        if not self._dialogs:
            raise ValueError("File dialogs are only available in the desktop window.")
        return self._dialogs(kind, **options)

    def _device_or_error(self, key: str) -> Device:
        device = self._store.find(key)
        if not device:
            raise ValueError("That device is no longer in the list.")
        return device

    def _groups(self) -> list[dict]:
        counts = self._store.groups()
        names = core.split_list(list(self._profiles.groups) + list(counts))
        return [{"name": name, "count": counts.get(name, 0)} for name in sorted(names, key=str.casefold)]

    def _recent(self) -> list[dict]:
        rows = []
        for record in self._profiles.recent_devices:
            device = self._store.find(f"{record.get('ip')}:{record.get('port')}") or Device(
                ip=str(record.get("ip", "")), sysname=str(record.get("sysname") or record.get("ip", "")), port=str(record.get("port", "22"))
            )
            rows.append({**device.to_ui(), "opened_at": record.get("last_opened_at", ""), "opened_with": record.get("profile", ""),
                         "in_inventory": self._store.find(device.key) is not None})
        return rows

    # ------------------------------------------------------------------ state
    @friendly
    def get_state(self) -> dict:
        with self._lock:
            return {
                "app": {"name": APP_NAME, "version": APP_VERSION, "data_dir": str(core.app_data_dir())},
                "theme": self._profiles.ui_theme,
                "themes": [{"name": name, "description": text} for name, text in APP_THEMES.items()],
                "profiles": [p.to_ui() for p in self._profiles.profiles],
                "active_profile_id": self._profiles.active_profile_id,
                "default_profile_id": self._profiles.default_profile_id,
                "devices": [d.to_ui() for d in self._store.devices],
                "groups": self._groups(),
                "recent": self._recent(),
                "netbox": self._netbox_state(),
                "settings": dict(self._profiles.settings),
                "sync_intervals": core.SYNC_INTERVALS,
                "load_errors": self._store.load_errors[:10],
                "activity": list(self._activity),
            }

    @friendly
    def get_devices(self) -> dict:
        with self._lock:
            return {"devices": [d.to_ui() for d in self._store.devices], "groups": self._groups(), "recent": self._recent()}

    def _netbox_state(self) -> dict:
        next_sync = self._profiles.next_sync_at()
        return {
            "connections": [c.to_ui() for c in self._profiles.netbox_connections],
            "history": self._profiles.sync_history,
            "syncing": self._sync_running,
            "last_sync_at": self._profiles.settings.get("last_sync_at", ""),
            "next_sync_at": next_sync.isoformat(timespec="seconds") if next_sync else "",
        }

    # ------------------------------------------------------------------ appearance and settings
    @friendly
    def set_theme(self, name: str) -> dict:
        with self._lock:
            self._profiles.set_ui_theme(name)
            return {"theme": self._profiles.ui_theme}

    @friendly
    def update_settings(self, changes: dict) -> dict:
        with self._lock:
            self._profiles.update_settings(changes or {})
            return {"settings": dict(self._profiles.settings), "netbox": self._netbox_state()}

    @friendly
    def backup_settings(self, path: str | None = None) -> dict:
        target = self._pick("save", path, save_filename="chirag-launcher-settings.json", file_types=("JSON files (*.json)",))
        if not target:
            return {"cancelled": True}
        with self._lock:
            self._profiles.export_backup(Path(target))
        return {"path": target}

    @friendly
    def restore_settings(self, path: str | None = None) -> dict:
        source = self._pick("open", path, file_types=("JSON files (*.json)",))
        if not source:
            return {"cancelled": True}
        with self._lock:
            self._profiles.restore_backup(Path(source))
        return {"path": source}

    # ------------------------------------------------------------------ profiles
    @friendly
    def set_active_profile(self, profile_id: str) -> dict:
        with self._lock:
            self._profiles.set_active(profile_id)
            return {"active_profile_id": self._profiles.active_profile_id}

    @friendly
    def save_profile(self, data: dict) -> dict:
        with self._lock:
            existing = self._profiles.get(data.get("id", "")) if data.get("id") else None
            name = str(data.get("name", "")).strip()
            putty_path = str(data.get("putty_path", "")).strip()
            log_directory = str(data.get("log_directory", "")).strip() or str(core.default_logs_path())
            if not name:
                raise ValueError("Give the profile a name.")
            if not putty_path or not Path(putty_path).exists():
                raise ValueError("PuTTY wasn't found at that path. Choose putty.exe.")
            if data.get("save_output"):
                Path(log_directory).mkdir(parents=True, exist_ok=True)
            password = data.get("password") or ""
            if existing and not password and not data.get("clear_password"):
                password = existing.password
            profile = ConnectionProfile(
                id=existing.id if existing else self._profiles.new_profile(name).id,
                name=name, username=str(data.get("username", "")).strip(), password=password,
                putty_path=putty_path, save_output=bool(data.get("save_output")), log_directory=log_directory,
            )
            self._profiles.save_profile(profile)
            if data.get("make_active"):
                self._profiles.set_active(profile.id)
            return {"profile": profile.to_ui(), "profiles": [p.to_ui() for p in self._profiles.profiles],
                    "active_profile_id": self._profiles.active_profile_id, "default_profile_id": self._profiles.default_profile_id}

    @friendly
    def delete_profile(self, profile_id: str) -> dict:
        with self._lock:
            self._profiles.delete_profile(profile_id)
            return self._profile_payload()

    @friendly
    def set_default_profile(self, profile_id: str) -> dict:
        with self._lock:
            self._profiles.set_default(profile_id)
            return self._profile_payload()

    def _profile_payload(self) -> dict:
        return {"profiles": [p.to_ui() for p in self._profiles.profiles], "active_profile_id": self._profiles.active_profile_id,
                "default_profile_id": self._profiles.default_profile_id}

    @friendly
    def suggest_profile_name(self) -> dict:
        return {"name": self._profiles.suggested_profile_name(), "putty_path": core.DEFAULT_PUTTY_PATH,
                "log_directory": str(core.default_logs_path())}

    @friendly
    def export_profiles(self, profile_ids: list | None = None, include_passwords: bool = False, path: str | None = None) -> dict:
        target = self._pick("save", path, save_filename="launcher-profiles.json", file_types=("JSON files (*.json)",))
        if not target:
            return {"cancelled": True}
        with self._lock:
            count = self._profiles.export_profiles(Path(target), profile_ids or None, bool(include_passwords))
        return {"path": target, "count": count}

    @friendly
    def import_profiles(self, path: str | None = None) -> dict:
        source = self._pick("open", path, file_types=("JSON files (*.json)",))
        if not source:
            return {"cancelled": True}
        with self._lock:
            names = self._profiles.import_profiles(Path(source))
            return {"imported": names, **self._profile_payload()}

    @friendly
    def pick_path(self, kind: str, current: str = "") -> dict:
        if kind == "folder":
            chosen = self._pick("folder", None, directory=current)
        else:
            chosen = self._pick("open", None, directory=str(Path(current).parent) if current else "",
                                file_types=("PuTTY (putty.exe)", "Programs (*.exe)"))
        return {"path": chosen or ""}

    # ------------------------------------------------------------------ devices
    @friendly
    def save_device(self, old_key: str, data: dict) -> dict:
        with self._lock:
            device = self._store.update_device(
                old_key or "", ip=str(data.get("ip", "")), sysname=str(data.get("sysname", "")), port=str(data.get("port", "")),
                group=str(data.get("group", "")), tags=str(data.get("tags", "")), notes=str(data.get("notes", "")),
                putty_session=str(data.get("putty_session", "")),
            )
            if device.group:
                self._profiles.add_group(device.group)
            return {"device": device.to_ui(), **self.get_devices()}

    @friendly
    def update_notes(self, key: str, notes: str) -> dict:
        with self._lock:
            device = self._device_or_error(key)
            device.notes = str(notes).strip()
            self._store.save()
            return {"device": device.to_ui()}

    @friendly
    def delete_devices(self, keys: list) -> dict:
        with self._lock:
            removed = self._store.delete(keys or [])
            return {"removed": removed, **self.get_devices()}

    @friendly
    def set_favorite(self, keys: list, favorite: bool) -> dict:
        with self._lock:
            count = self._store.set_favorite(keys or [], bool(favorite))
            return {"count": count, "devices": [d.to_ui() for d in self._store.find_many(keys or [])]}

    @friendly
    def set_group(self, keys: list, group: str) -> dict:
        with self._lock:
            if group:
                self._profiles.add_group(group)
            count = self._store.set_group(keys or [], group or "")
            return {"count": count, **self.get_devices()}

    @friendly
    def add_tags(self, keys: list, tags: str) -> dict:
        with self._lock:
            count = self._store.add_tags(keys or [], core.split_list(tags))
            return {"count": count, "devices": [d.to_ui() for d in self._store.find_many(keys or [])]}

    @friendly
    def remove_tags(self, keys: list, tags: str) -> dict:
        with self._lock:
            count = self._store.remove_tags(keys or [], core.split_list(tags))
            return {"count": count, "devices": [d.to_ui() for d in self._store.find_many(keys or [])]}

    @friendly
    def create_group(self, name: str) -> dict:
        with self._lock:
            self._profiles.add_group(str(name))
            return {"groups": self._groups()}

    @friendly
    def rename_group(self, old: str, new: str) -> dict:
        new = str(new).strip()
        if not new:
            raise ValueError("Group name is required.")
        with self._lock:
            if new.casefold() != old.casefold() and any(g["name"].casefold() == new.casefold() for g in self._groups()):
                raise ValueError(f"A group called {new} already exists.")
            self._store.rename_group(old, new)
            self._profiles.rename_group(old, new)
            return self.get_devices()

    @friendly
    def delete_group(self, name: str) -> dict:
        with self._lock:
            self._store.rename_group(name, "")
            self._profiles.remove_group(name)
            return self.get_devices()

    @friendly
    def copy_text(self, text: str) -> dict:
        return {"copied": core.copy_to_clipboard(str(text))}

    @friendly
    def putty_sessions(self) -> dict:
        return {"sessions": core.putty_saved_sessions()}

    # ------------------------------------------------------------------ launching
    def _launch(self, device: Device) -> dict:
        profile = self._profiles.active_profile()
        if not profile.putty_path or not Path(profile.putty_path).exists():
            raise ValueError(f"PuTTY wasn't found for the profile {profile.name}. Edit the profile and choose putty.exe.")
        try:
            log_path = core.session_log_path(profile, device)
        except OSError as exc:
            raise ValueError(f"Couldn't create the session-log folder: {exc}") from exc
        command = core.build_putty_command(profile, device, log_path)
        process = subprocess.Popen(command, shell=False)
        with self._lock:
            device.last_success = core.now_iso()
            if self._store.find(device.key):
                self._store.save()
            self._profiles.record_recent_device(device, profile.name)
        threading.Thread(target=self._watch_launch, name="watch-launch", daemon=True,
                         args=(process, device.key, device.sysname, device.ip, device.port, device.putty_session)).start()
        return {"key": device.key, "sysname": device.sysname, "profile": profile.name,
                "log": log_path.name if log_path else "", "prompt_login": device.protocol == "ssh" and not profile.password}

    def _watch_launch(self, *args) -> None:
        try:
            self._watch_launch_inner(*args)
        except Exception as exc:  # a watcher must never take the app down
            self._notify("warn", "Couldn't track a PuTTY session", str(exc))

    def _watch_launch_inner(self, process: subprocess.Popen, key: str, sysname: str, ip: str, port: str, putty_session: str) -> None:
        """Probe the port in parallel with PuTTY and report failures without blocking the launch."""
        failed = False
        if self._profiles.settings.get("preflight_check", True) and not putty_session:
            timeout = float(self._profiles.settings.get("diagnostic_timeout", 3))
            result = core.probe_port(ip, port, timeout=timeout, read_banner=False)
            with self._lock:
                device = self._store.find(key)
                if device:
                    device.reachability = "up" if result["ok"] else "down"
                    device.last_checked = core.now_iso()
                    device.latency_ms = core.latency_value(result["ms"]) if result["ok"] else ""
                    if not result["ok"]:
                        device.last_failure = f"{core.now_iso()} {result['detail']}"
                    self._store.save()
                    self._emit("device_updated", {"device": device.to_ui()})
            if not result["ok"]:
                failed = True
                self._notify("error", f"Couldn't reach {sysname}", f"{result['detail']} ({ip}). PuTTY may show a network error.", key)
        try:
            code = process.wait(timeout=25)
        except subprocess.TimeoutExpired:
            return
        if code not in (0, None) and not failed:
            with self._lock:
                device = self._store.find(key)
                if device:
                    device.last_failure = f"{core.now_iso()} PuTTY exited with code {code}"
                    self._store.save()
                    self._emit("device_updated", {"device": device.to_ui()})
            self._notify("error", f"Session to {sysname} ended with an error",
                         f"PuTTY closed with exit code {code}. The connection may have been refused, timed out, or the login failed.", key)

    @friendly
    def launch(self, keys: list) -> dict:
        launched, errors = [], []
        for key in keys or []:
            device = self._store.find(key)
            if not device:
                errors.append(f"{key} is no longer in the list.")
                continue
            try:
                launched.append(self._launch(device))
            except EXPECTED_ERRORS as exc:
                errors.append(str(exc))
                if "PuTTY wasn't found" in str(exc):
                    break
        if not launched and errors:
            raise ValueError(errors[0])
        return {"launched": launched, "errors": errors, "recent": self._recent(),
                "devices": [d.to_ui() for d in self._store.find_many(keys or [])]}

    @friendly
    def launch_recent(self, ip: str, port: str, sysname: str = "") -> dict:
        device = self._store.find(f"{ip}:{port}") or Device(ip=ip, sysname=sysname or ip, port=str(port))
        return {"launched": [self._launch(device)], "recent": self._recent()}

    @friendly
    def quick_connect(self, address: str, port: str = "22", sysname: str = "") -> dict:
        address, port = str(address).strip(), str(port or "22").strip()
        core.validate_endpoint(address, port)
        with self._lock:
            device = self._store.find(f"{address}:{port}")
            if device is None and not sysname and port == "22":
                device = self._store.find_by_ip(address)
            created = device is None
            if created:
                device = Device(ip=address, sysname=sysname.strip() or address, port=port)
                self._store.add_or_update(device)
        result = self._launch(device)
        return {"launched": [result], "created": created, "device": device.to_ui(), **self.get_devices()}

    # ------------------------------------------------------------------ diagnostics
    @friendly
    def diagnose_device(self, key: str) -> dict:
        device = self._device_or_error(key)
        timeout = float(self._profiles.settings.get("diagnostic_timeout", 3))
        result = core.diagnose(device.ip, device.port, timeout=timeout, full=True)
        with self._lock:
            self._store.record_check(key, result)
            current = self._store.find(key)
        return {"result": result, "device": current.to_ui() if current else device.to_ui()}

    @friendly
    def check_devices(self, keys: list) -> dict:
        if self._check_running:
            raise ValueError("A reachability check is already running.")
        targets = [(d.key, d.ip, d.port) for d in self._store.find_many(keys or [])]
        if not targets:
            raise ValueError("Select devices to check first.")
        self._check_running = True
        self._check_cancel.clear()
        threading.Thread(target=self._check_worker, args=(targets,), daemon=True).start()
        return {"total": len(targets)}

    def _check_worker(self, targets: list[tuple[str, str, str]]) -> None:
        timeout = min(3.0, float(self._profiles.settings.get("diagnostic_timeout", 3)))
        buffer: list[dict] = []
        done = 0
        counts = {"up": 0, "filtered": 0, "down": 0}
        last_flush = time.monotonic()
        total = len(targets)
        self._emit("check_started", {"total": total})

        def on_result(key: str, result: dict) -> None:
            nonlocal done, last_flush
            done += 1
            counts[result.get("status", "down")] = counts.get(result.get("status", "down"), 0) + 1
            with self._lock:
                self._store.record_check(key, result, save=False)
                device = self._store.find(key)
            if device:
                buffer.append(device.to_ui())
            if time.monotonic() - last_flush > 0.35 or done == total:
                self._emit("check_progress", {"done": done, "total": total, "devices": list(buffer)})
                buffer.clear()
                last_flush = time.monotonic()

        try:
            core.check_many(targets, timeout=timeout, workers=64, on_result=on_result, cancel=self._check_cancel)
        finally:
            if buffer:
                self._emit("check_progress", {"done": done, "total": total, "devices": list(buffer)})
            with self._lock:
                try:
                    self._store.save()
                except OSError:
                    pass
            self._check_running = False
            cancelled = self._check_cancel.is_set()
            self._emit("check_done", {"done": done, "total": total, "counts": counts, "cancelled": cancelled})
            if total > 1:
                body = f"{counts.get('up', 0)} reachable, {counts.get('filtered', 0)} answer ping only, {counts.get('down', 0)} unreachable."
                self._notify("warn" if counts.get("down") else "ok",
                             "Reachability check stopped" if cancelled else "Reachability check finished", body)

    @friendly
    def cancel_check(self) -> dict:
        self._check_cancel.set()
        return {"cancelled": True}

    # ------------------------------------------------------------------ CSV
    @friendly
    def preview_import(self, path: str | None = None) -> dict:
        source = self._pick("open", path, file_types=("CSV files (*.csv)", "All files (*.*)"))
        if not source:
            return {"cancelled": True}
        with self._lock:
            return {"path": source, "name": Path(source).name, **self._store.preview_csv_details(Path(source))}

    @friendly
    def import_devices(self, path: str) -> dict:
        with self._lock:
            added = self._store.import_csv(Path(path), replace=False)
            return {"added": added, **self.get_devices()}

    @friendly
    def export_devices(self, keys: list | None = None, path: str | None = None) -> dict:
        target = self._pick("save", path, save_filename="devices.csv", file_types=("CSV files (*.csv)",))
        if not target:
            return {"cancelled": True}
        with self._lock:
            devices = self._store.find_many(keys) if keys else None
            count = self._store.export_csv(Path(target), devices)
        return {"path": target, "count": count}

    # ------------------------------------------------------------------ NetBox
    @friendly
    def suggest_netbox_name(self) -> dict:
        return {"name": self._profiles.suggested_netbox_connection_name(), "defaults": NetBoxConnection(id="", name="", url="", api_key="").to_ui()}

    @friendly
    def save_netbox_connection(self, data: dict) -> dict:
        with self._lock:
            existing = self._profiles.get_netbox_connection(data.get("id", "")) if data.get("id") else None
            name = str(data.get("name", "")).strip()
            url = core.normalize_netbox_url(str(data.get("url", "")))
            api_key = str(data.get("api_key", "")).strip() or (existing.api_key if existing else "")
            if not name or not url or not api_key:
                raise ValueError("Name, NetBox URL and API token are all required.")
            if not url.lower().startswith(("http://", "https://")):
                raise ValueError("The NetBox URL must start with http:// or https://")
            roles = [r for r in core.split_list(data.get("roles", "")) if r in core.NETBOX_ROLES]
            if not roles:
                raise ValueError("Pick at least one device role to sync.")
            connection = NetBoxConnection(
                id=existing.id if existing else self._profiles.new_netbox_connection(name).id, name=name, url=url, api_key=api_key,
                enabled=bool(data.get("enabled", True)), manufacturers=",".join(core.split_list(data.get("manufacturers", ""))),
                roles=",".join(roles), sites=",".join(core.split_list(data.get("sites", ""))),
                status=str(data.get("status") or "active").strip(), ignored_models=",".join(core.split_list(data.get("ignored_models", ""))),
            )
            self._profiles.save_netbox_connection(connection)
            return {"netbox": self._netbox_state()}

    @friendly
    def delete_netbox_connection(self, connection_id: str) -> dict:
        with self._lock:
            self._profiles.delete_netbox_connection(connection_id)
            return {"netbox": self._netbox_state()}

    @friendly
    def set_netbox_enabled(self, connection_id: str, enabled: bool) -> dict:
        with self._lock:
            connection = self._profiles.get_netbox_connection(connection_id)
            if not connection:
                raise ValueError("That NetBox connection no longer exists.")
            connection.enabled = bool(enabled)
            self._profiles.save()
            return {"netbox": self._netbox_state()}

    @friendly
    def test_netbox_connection(self, connection_id: str) -> dict:
        connection = self._profiles.get_netbox_connection(connection_id)
        if not connection:
            raise ValueError("That NetBox connection no longer exists.")
        return NetBoxClient().test(connection)

    @friendly
    def sync_netbox(self, connection_ids: list | None = None, trigger: str = "manual") -> dict:
        if self._sync_running:
            raise ValueError("A NetBox sync is already running.")
        if not self._profiles.netbox_connections:
            raise ValueError("Add a NetBox connection first.")
        ids = connection_ids or [c.id for c in self._profiles.netbox_connections if c.enabled]
        if not ids:
            raise ValueError("Turn on at least one NetBox connection to sync.")
        self._sync_running = True
        threading.Thread(target=self._sync_worker, args=(ids, trigger), daemon=True).start()
        self._emit("sync_started", {"trigger": trigger, "count": len(ids)})
        return {"started": True}

    def _sync_worker(self, ids: list[str], trigger: str) -> None:
        try:
            entry = core.run_netbox_sync(self._profiles, self._store, ids, trigger, lock=self._lock)
        except Exception as exc:  # never leave the UI stuck in "syncing"
            entry = {"connections": [], "added": 0, "updated": 0, "removed": 0, "error": str(exc), "trigger": trigger}
        finally:
            self._sync_running = False
        failed = [c for c in entry.get("connections", []) if not c.get("ok")]
        summary = f"{entry.get('added', 0)} added, {entry.get('updated', 0)} updated, {entry.get('removed', 0)} removed."
        if failed or entry.get("error"):
            names = ", ".join(c["name"] for c in failed)
            self._notify("error", "NetBox sync had problems", (f"{names} failed. " if names else "") + (entry.get("error") or "") + " " + summary)
        else:
            self._notify("ok", "NetBox sync finished" if trigger == "manual" else f"NetBox {trigger} sync finished", summary)
        self._emit("sync_finished", {"entry": entry, "netbox": self._netbox_state(), **self.get_devices()})

    def _scheduler(self) -> None:
        if self._profiles.settings.get("sync_on_startup") and any(c.enabled for c in self._profiles.netbox_connections):
            time.sleep(4)
            if not self._sync_running:
                self.sync_netbox(None, "startup")
        while True:
            time.sleep(30)
            try:
                if not self._sync_running and self._profiles.sync_due():
                    self.sync_netbox(None, "scheduled")
            except Exception:
                pass

    def start_background(self) -> None:
        if not self._scheduler_started:
            self._scheduler_started = True
            threading.Thread(target=self._scheduler, daemon=True).start()

    # ------------------------------------------------------------------ session logs
    @friendly
    def list_logs(self) -> dict:
        return {"logs": core.list_session_logs(self._profiles.profiles),
                "folders": [str(p) for p in core.log_directories(self._profiles.profiles)]}

    @friendly
    def read_log(self, path: str) -> dict:
        resolved = core.resolve_log_path(path, self._profiles.profiles)
        return {"path": str(resolved), **core.read_log_tail(resolved)}

    @friendly
    def open_log(self, path: str) -> dict:
        resolved = core.resolve_log_path(path, self._profiles.profiles)
        subprocess.Popen(["notepad.exe", str(resolved)])
        return {"opened": True}

    @friendly
    def reveal_log(self, path: str) -> dict:
        resolved = core.resolve_log_path(path, self._profiles.profiles)
        core.open_path(resolved, select=True)
        return {"opened": True}

    @friendly
    def open_folder(self, which: str = "logs") -> dict:
        folder = core.app_data_dir() if which == "data" else Path(self._profiles.active_profile().log_directory or core.default_logs_path())
        folder.mkdir(parents=True, exist_ok=True)
        core.open_path(folder)
        return {"opened": str(folder)}

    # ------------------------------------------------------------------ PuTTY windows, recent, activity, updates
    @friendly
    def putty_status(self) -> dict:
        return {"count": len(core.putty_pids())}

    @friendly
    def close_all_putty(self) -> dict:
        pids = core.putty_pids()
        closed, failures = core.close_putty(pids)
        return {"closed": closed, "failed": failures}

    @friendly
    def clear_recent(self) -> dict:
        with self._lock:
            self._profiles.clear_recent()
            return {"recent": []}

    @friendly
    def get_activity(self) -> dict:
        return {"activity": list(self._activity)}

    @friendly
    def clear_activity(self) -> dict:
        with self._lock:
            self._activity = []
        return {"activity": []}

    @friendly
    def check_updates(self) -> dict:
        try:
            return core.check_latest_release()
        except Exception as exc:
            raise ValueError(f"Couldn't reach GitHub to check for updates ({exc.__class__.__name__}).") from exc

    @friendly
    def open_url(self, url: str) -> dict:
        if not str(url).startswith(f"https://github.com/{core.GITHUB_REPOSITORY}"):
            raise ValueError("Only the launcher's GitHub page can be opened from here.")
        webbrowser.open(url)
        return {"opened": True}


# ---------------------------------------------------------------------- desktop window

def run_desktop() -> None:
    try:
        import webview
    except ImportError:
        print("pywebview is required: python -m pip install pywebview", file=sys.stderr)
        raise

    core.backup_before_upgrade()
    holder: dict = {}

    def emit(event: str, payload: dict) -> None:
        window = holder.get("window")
        if window is None:
            return
        script = f"window.bridge && window.bridge.receive({json.dumps({'event': event, 'payload': payload})})"
        runner = getattr(window, "run_js", None) or window.evaluate_js
        runner(script)

    def dialogs(kind: str, directory: str = "", save_filename: str = "", file_types: tuple = ()):
        window = holder["window"]
        dialog_type = {"open": webview.FileDialog.OPEN, "save": webview.FileDialog.SAVE, "folder": webview.FileDialog.FOLDER}[kind]
        result = window.create_file_dialog(dialog_type, directory=directory or "", save_filename=save_filename, file_types=file_types)
        if not result:
            return None
        return result if isinstance(result, str) else result[0]

    api = LauncherApi(emit=emit, dialogs=dialogs)
    selftest = os.getenv("CHIRAG_LAUNCHER_SELFTEST")
    theme_background = {"Slate": "#E8ECF0", "Midnight": "#111922", "Ocean": "#E2EDEC", "Graphite": "#1D1E21"}
    window = webview.create_window(
        APP_NAME,
        url=str(resource_dir() / "index.html"),
        js_api=api,
        width=1380,
        height=880,
        min_size=(1000, 640),
        background_color=theme_background.get(api._profiles.ui_theme, "#E8ECF0"),
        text_select=True,
        hidden=bool(selftest),
    )
    holder["window"] = window
    window.events.loaded += lambda: api.start_background()
    if selftest:
        threading.Thread(target=_desktop_selftest, args=(window, api, Path(selftest)), daemon=True).start()
    icon = resource_dir() / "app.ico"
    webview.start(debug="--debug" in sys.argv, private_mode=True, icon=str(icon) if icon.exists() else None)


def _desktop_selftest(window, api: LauncherApi, report: Path) -> None:
    """CHIRAG_LAUNCHER_SELFTEST=<file>: check the real window's bridge and events, write JSON, then close."""
    result: dict = {"ready": False}
    try:
        deadline = time.time() + 45
        while time.time() < deadline:
            try:
                if window.evaluate_js("typeof S !== 'undefined' && !document.getElementById('boot') && S.devices.length >= 0"):
                    result["ready"] = True
                    break
            except Exception as exc:
                result["last_error"] = str(exc)
            time.sleep(0.5)
        result["state"] = json.loads(window.evaluate_js(
            "JSON.stringify({mode: bridge.mode, devices: S.devices.length, rows: document.querySelectorAll('#rows .row').length,"
            " title: document.querySelector('#list-title').textContent, theme: document.body.dataset.theme,"
            " font: document.fonts.check('15px Bahnschrift'), width: innerWidth, height: innerHeight})"))
        window.evaluate_js("window.__t = performance.now(); bridge.call('get_devices').then(r => { window.__rt = Math.round(performance.now() - window.__t); window.__n = r.devices.length; })")
        time.sleep(4)
        result["get_devices_ms"] = window.evaluate_js("window.__rt")
        result["get_devices_count"] = window.evaluate_js("window.__n")
        api._notify("ok", "Desktop self-test event", "")
        time.sleep(1.5)
        result["event_received"] = window.evaluate_js("S.activity.some(a => a.title === 'Desktop self-test event')")
        window.evaluate_js("document.querySelector('#omni').value = 'core'; document.querySelector('#omni').dispatchEvent(new Event('input'))")
        time.sleep(0.5)
        result["search"] = json.loads(window.evaluate_js("JSON.stringify({shown: S.list.length, selected: [...S.selected], inspector: (document.querySelector('.ins-name') || {}).textContent})"))
    except Exception as exc:
        result["error"] = f"{exc.__class__.__name__}: {exc}"
    report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    window.destroy()


if __name__ == "__main__":
    run_desktop()
