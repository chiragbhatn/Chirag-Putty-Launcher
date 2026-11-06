"""Core logic for Chirag Putty Launcher: storage, NetBox sync, diagnostics, logs.

Nothing in this module touches the UI, so it can be tested on its own.
"""
from __future__ import annotations

import base64
import csv
import json
import operator
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable

APP_NAME = "Chirag Putty Launcher"
APP_VERSION = "2.0.0"
GITHUB_REPOSITORY = "chiragbhatn/Chirag-Putty-Launcher"
DEFAULT_PUTTY_PATH = r"C:\Program Files\PuTTY\putty.exe"
DEFAULT_THEME = "Slate"
APP_THEMES = {
    "Slate": "Light, cool grey",
    "Midnight": "Dark blue",
    "Ocean": "Light teal",
    "Graphite": "Dark, warm amber",
}

NETBOX_ROLE_ACCESS = "access"
NETBOX_ROLE_CORE = "core"
NETBOX_ROLE_DISTRIBUTION = "distribution"
NETBOX_ROLES = [NETBOX_ROLE_ACCESS, NETBOX_ROLE_CORE, NETBOX_ROLE_DISTRIBUTION]
NETBOX_MANUFACTURERS = ["huawei", "juniper"]
NETBOX_IGNORED_MODELS = {"QFX5120-32C-AFO"}
ROLE_PATTERN = re.compile(r"-(core|dis|dist|acc)(?=$|[-_.\d])")
ROLE_BY_SUFFIX = {"core": NETBOX_ROLE_CORE, "dis": NETBOX_ROLE_DISTRIBUTION, "dist": NETBOX_ROLE_DISTRIBUTION, "acc": NETBOX_ROLE_ACCESS}

RECENT_LIMIT = 20
SYNC_HISTORY_LIMIT = 40
SYNC_INTERVALS = [0, 15, 30, 60, 240, 720, 1440]
DEFAULT_SETTINGS = {
    "sync_interval_minutes": 0,
    "sync_on_startup": False,
    "last_sync_at": "",
    "last_sync_attempt_at": "",
    "preflight_check": True,
    "diagnostic_timeout": 3,
}
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def app_data_dir() -> Path:
    """Folder for config, devices and default logs. CHIRAG_LAUNCHER_HOME overrides it (tests)."""
    override = os.getenv("CHIRAG_LAUNCHER_HOME")
    path = Path(override) if override else Path(os.getenv("APPDATA") or str(Path.home())) / "ChiragPuttyLauncher"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return app_data_dir() / "config.json"


def devices_path() -> Path:
    return app_data_dir() / "devices.csv"


def default_logs_path() -> Path:
    return app_data_dir() / "Logs"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def split_list(value: str | Iterable[str]) -> list[str]:
    items = value.split(",") if isinstance(value, str) else list(value)
    seen: list[str] = []
    for item in items:
        item = str(item).strip()
        if item and item.casefold() not in {existing.casefold() for existing in seen}:
            seen.append(item)
    return seen


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def backup_before_upgrade() -> list[Path]:
    """Copy v1 data files once before this version rewrites them."""
    created: list[Path] = []
    for source in (config_path(), devices_path()):
        target = source.with_name(source.stem + ".before-v2" + source.suffix)
        if source.exists() and not target.exists():
            try:
                raw = source.read_text(encoding="utf-8", errors="ignore")
                if source.suffix == ".json" and '"version": 3' in raw:
                    continue
                shutil.copy2(source, target)
                created.append(target)
            except OSError:
                pass
    return created


# --------------------------------------------------------------------------- secrets

def protect_secret(secret: str) -> str:
    if not secret:
        return ""
    if sys.platform != "win32":
        return base64.b64encode(secret.encode("utf-8")).decode("ascii")
    try:
        import ctypes
        from ctypes import wintypes

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        data = secret.encode("utf-8")
        in_blob = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
        out_blob = DATA_BLOB()
        if not crypt32.CryptProtectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
            raise OSError("CryptProtectData failed")
        try:
            encrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
            return "dpapi:" + base64.b64encode(encrypted).decode("ascii")
        finally:
            kernel32.LocalFree(out_blob.pbData)
    except Exception:
        return base64.b64encode(secret.encode("utf-8")).decode("ascii")


def unprotect_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith("dpapi:"):
        try:
            return base64.b64decode(value.encode("ascii")).decode("utf-8")
        except Exception:
            return ""
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        encrypted = base64.b64decode(value.removeprefix("dpapi:").encode("ascii"))
        in_blob = DATA_BLOB(len(encrypted), ctypes.cast(ctypes.create_string_buffer(encrypted), ctypes.POINTER(ctypes.c_char)))
        out_blob = DATA_BLOB()
        if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
            raise OSError("CryptUnprotectData failed")
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
        finally:
            kernel32.LocalFree(out_blob.pbData)
    except Exception:
        return ""


# --------------------------------------------------------------------------- models

@dataclass
class Device:
    ip: str
    sysname: str
    port: str
    source: str = "manual"
    netbox_connection_id: str = ""
    netbox_device_id: str = ""
    group: str = ""
    tags: str = ""
    notes: str = ""
    favorite_profiles: str = ""
    putty_session: str = ""
    last_success: str = ""
    reachability: str = "unknown"
    role: str = ""
    last_checked: str = ""
    latency_ms: str = ""
    last_failure: str = ""

    @property
    def protocol(self) -> str:
        return "telnet" if self.port.strip() == "23" else "ssh"

    @property
    def endpoint(self) -> str:
        return f"{self.ip}:{self.port}"

    @property
    def key(self) -> str:
        """Unique id for the UI. Rows sharing an IP and port (kept from 1.x lists) get #2, #3…"""
        duplicate = getattr(self, "_dup", 0)
        return f"{self.endpoint}#{duplicate + 1}" if duplicate else self.endpoint

    @property
    def kind(self) -> str:
        match = ROLE_PATTERN.search(self.sysname.strip().lower())
        if match:
            return ROLE_BY_SUFFIX[match.group(1)]
        return self.role if self.role in NETBOX_ROLES else ""

    @property
    def is_favorite(self) -> bool:
        return bool(self.favorite_profiles.strip())

    @property
    def tag_list(self) -> list[str]:
        return split_list(self.tags)

    def to_ui(self) -> dict:
        """UI payload. Empty optional fields are left out to keep 6,000+ device lists small."""
        data = {
            "key": self.key,
            "ip": self.ip,
            "sysname": self.sysname,
            "port": self.port,
            "protocol": self.protocol,
            "source": self.source,
            "netbox_connection_id": self.netbox_connection_id,
            "group": self.group,
            "tags": self.tag_list,
            "notes": self.notes,
            "favorite": self.is_favorite,
            "putty_session": self.putty_session,
            "last_success": self.last_success,
            "reachability": "" if self.reachability == "unknown" else self.reachability,
            "kind": self.kind,
            "last_checked": self.last_checked,
            "latency_ms": self.latency_ms,
            "last_failure": self.last_failure,
        }
        return {k: v for k, v in data.items() if v or k in UI_REQUIRED_FIELDS}


DEVICE_FIELDS = [item.name for item in fields(Device)]
UI_REQUIRED_FIELDS = {"key", "ip", "sysname", "port", "protocol", "source"}


@dataclass
class ConnectionProfile:
    id: str
    name: str
    username: str
    password: str
    putty_path: str
    save_output: bool
    log_directory: str

    def to_ui(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "username": self.username,
            "has_password": bool(self.password),
            "putty_path": self.putty_path,
            "putty_found": Path(self.putty_path).exists() if self.putty_path else False,
            "save_output": self.save_output,
            "log_directory": self.log_directory,
        }


@dataclass
class NetBoxConnection:
    id: str
    name: str
    url: str
    api_key: str
    enabled: bool = True
    manufacturers: str = ",".join(NETBOX_MANUFACTURERS)
    roles: str = ",".join(NETBOX_ROLES)
    sites: str = ""
    status: str = "active"
    ignored_models: str = ",".join(sorted(NETBOX_IGNORED_MODELS))

    def to_ui(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "has_key": bool(self.api_key),
            "enabled": self.enabled,
            "manufacturers": self.manufacturers,
            "roles": split_list(self.roles),
            "sites": self.sites,
            "status": self.status,
            "ignored_models": self.ignored_models,
        }


@dataclass(frozen=True)
class NetBoxDevice:
    connection_id: str
    device_id: str
    sysname: str
    ip: str
    bucket: str


# --------------------------------------------------------------------------- settings / profiles

class ProfileStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else config_path()
        self.profiles: list[ConnectionProfile] = []
        self.netbox_connections: list[NetBoxConnection] = []
        self.recent_devices: list[dict] = []
        self.sync_history: list[dict] = []
        self.groups: list[str] = []
        self.settings: dict = dict(DEFAULT_SETTINGS)
        self.ui_theme = DEFAULT_THEME
        self.default_profile_id = ""
        self.active_profile_id = ""
        self.load()

    def load(self) -> None:
        raw: dict = {}
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except Exception:
            # Keep the unreadable file so profiles can be recovered by hand, then start fresh.
            try:
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                shutil.copy2(self.path, self.path.with_name(f"{self.path.stem}.unreadable-{stamp}{self.path.suffix}"))
            except OSError:
                pass
            raw = {}
        if not isinstance(raw, dict):
            raw = {}

        if isinstance(raw.get("profiles"), list):
            self.profiles = [self.profile_from_dict(item) for item in raw["profiles"] if isinstance(item, dict)]
            self.default_profile_id = raw.get("default_profile_id", "")
            self.active_profile_id = raw.get("active_profile_id", "")
        elif raw.get("username") or raw.get("putty_path"):
            # Migrate the original single-profile configuration.
            self.profiles = [
                ConnectionProfile(
                    id=str(uuid.uuid4()),
                    name="Default Profile",
                    username=raw.get("username", ""),
                    password=unprotect_secret(raw.get("password", "")),
                    putty_path=raw.get("putty_path", DEFAULT_PUTTY_PATH),
                    save_output=False,
                    log_directory=str(default_logs_path()),
                )
            ]

        if isinstance(raw.get("netbox_connections"), list):
            self.netbox_connections = [
                self.netbox_connection_from_dict(item) for item in raw["netbox_connections"] if isinstance(item, dict)
            ]

        legacy_theme = raw.get("ui_theme")
        self.ui_theme = {"light": "Slate", "dark": "Midnight"}.get(legacy_theme, legacy_theme)
        if self.ui_theme not in APP_THEMES:
            self.ui_theme = DEFAULT_THEME

        if isinstance(raw.get("recent_devices"), list):
            self.recent_devices = [
                item for item in raw["recent_devices"] if isinstance(item, dict) and item.get("ip") and item.get("port")
            ][:RECENT_LIMIT]
        if isinstance(raw.get("sync_history"), list):
            self.sync_history = [item for item in raw["sync_history"] if isinstance(item, dict)][:SYNC_HISTORY_LIMIT]
        if isinstance(raw.get("groups"), list):
            self.groups = split_list(str(item) for item in raw["groups"])
        settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
        self.settings = {**DEFAULT_SETTINGS, **{k: v for k, v in settings.items() if k in DEFAULT_SETTINGS}}

        if not self.profiles:
            self.profiles = [self.new_profile("Default Profile")]
        if not self.get(self.default_profile_id):
            self.default_profile_id = self.profiles[0].id
        if not self.get(self.active_profile_id):
            self.active_profile_id = self.default_profile_id
        self.save()

    # -- conversions
    def profile_from_dict(self, raw: dict) -> ConnectionProfile:
        return ConnectionProfile(
            id=raw.get("id") or str(uuid.uuid4()),
            name=raw.get("name") or "Unnamed Profile",
            username=raw.get("username", ""),
            password=unprotect_secret(raw.get("password", "")),
            putty_path=raw.get("putty_path", DEFAULT_PUTTY_PATH),
            save_output=bool(raw.get("save_output", False)),
            log_directory=raw.get("log_directory") or str(default_logs_path()),
        )

    def new_profile(self, name: str) -> ConnectionProfile:
        return ConnectionProfile(
            id=str(uuid.uuid4()), name=name, username="", password="", putty_path=DEFAULT_PUTTY_PATH,
            save_output=False, log_directory=str(default_logs_path()),
        )

    def netbox_connection_from_dict(self, raw: dict) -> NetBoxConnection:
        defaults = NetBoxConnection(id="", name="", url="", api_key="")
        return NetBoxConnection(
            id=raw.get("id") or str(uuid.uuid4()),
            name=raw.get("name") or "Unnamed NetBox",
            url=(raw.get("url") or "").rstrip("/"),
            api_key=unprotect_secret(raw.get("api_key", "")),
            enabled=bool(raw.get("enabled", True)),
            manufacturers=str(raw.get("manufacturers", defaults.manufacturers)),
            roles=str(raw.get("roles", defaults.roles)),
            sites=str(raw.get("sites", "")),
            status=str(raw.get("status") or "active"),
            ignored_models=str(raw.get("ignored_models", defaults.ignored_models)),
        )

    def new_netbox_connection(self, name: str) -> NetBoxConnection:
        return NetBoxConnection(id=str(uuid.uuid4()), name=name, url="", api_key="")

    def _suggest(self, base: str, existing: Iterable[str]) -> str:
        taken = {name.casefold() for name in existing}
        if base.casefold() not in taken:
            return base
        index = 2
        while f"{base} {index}".casefold() in taken:
            index += 1
        return f"{base} {index}"

    def suggested_netbox_connection_name(self) -> str:
        return self._suggest("New NetBox", (c.name for c in self.netbox_connections))

    def suggested_profile_name(self) -> str:
        return self._suggest("New Profile", (p.name for p in self.profiles))

    def to_dict(self) -> dict:
        return {
            "version": 3,
            "default_profile_id": self.default_profile_id,
            "active_profile_id": self.active_profile_id,
            "ui_theme": self.ui_theme,
            "profiles": [
                {
                    "id": p.id, "name": p.name, "username": p.username, "password": protect_secret(p.password),
                    "putty_path": p.putty_path, "save_output": p.save_output, "log_directory": p.log_directory,
                }
                for p in self.profiles
            ],
            "netbox_connections": [
                {
                    "id": c.id, "name": c.name, "url": c.url, "api_key": protect_secret(c.api_key),
                    "enabled": c.enabled, "manufacturers": c.manufacturers, "roles": c.roles, "sites": c.sites,
                    "status": c.status, "ignored_models": c.ignored_models,
                }
                for c in self.netbox_connections
            ],
            "recent_devices": self.recent_devices,
            "sync_history": self.sync_history,
            "groups": self.groups,
            "settings": self.settings,
        }

    def save(self) -> None:
        atomic_write_text(self.path, json.dumps(self.to_dict(), indent=2))

    # -- profiles
    def get(self, profile_id: str) -> ConnectionProfile | None:
        return next((p for p in self.profiles if p.id == profile_id), None)

    def active_profile(self) -> ConnectionProfile:
        return self.get(self.active_profile_id) or self.profiles[0]

    def save_profile(self, profile: ConnectionProfile) -> None:
        if not profile.name.strip():
            raise ValueError("Profile name is required.")
        if any(p.id != profile.id and p.name.casefold() == profile.name.casefold() for p in self.profiles):
            raise ValueError("Profile names must be unique.")
        for index, current in enumerate(self.profiles):
            if current.id == profile.id:
                self.profiles[index] = profile
                self.save()
                return
        self.profiles.append(profile)
        self.active_profile_id = profile.id
        self.save()

    def set_active(self, profile_id: str) -> None:
        if self.get(profile_id):
            self.active_profile_id = profile_id
            self.save()

    def set_default(self, profile_id: str) -> None:
        if self.get(profile_id):
            self.default_profile_id = profile_id
            self.save()

    def delete_profile(self, profile_id: str) -> None:
        if len(self.profiles) == 1:
            raise ValueError("At least one profile is required.")
        self.profiles = [p for p in self.profiles if p.id != profile_id]
        if self.default_profile_id == profile_id:
            self.default_profile_id = self.profiles[0].id
        if self.active_profile_id == profile_id:
            self.active_profile_id = self.default_profile_id
        self.save()

    def export_profiles(self, path: Path, profile_ids: Iterable[str] | None = None, include_passwords: bool = False) -> int:
        wanted = set(profile_ids or [p.id for p in self.profiles])
        chosen = [p for p in self.profiles if p.id in wanted]
        data = {
            "format": "chirag-putty-launcher-profiles",
            "version": 1,
            "exported_at": now_iso(),
            "profiles": [
                {
                    "name": p.name, "username": p.username, "putty_path": p.putty_path,
                    "save_output": p.save_output, "log_directory": p.log_directory,
                    **({"password_plain": p.password} if include_passwords and p.password else {}),
                }
                for p in chosen
            ],
        }
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
        return len(chosen)

    def import_profiles(self, path: Path) -> list[str]:
        """Add profiles from an export file or a settings backup. Returns the imported names."""
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        items = raw.get("profiles") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            raise ValueError("This file does not contain launcher profiles.")
        imported: list[str] = []
        for item in items:
            if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                continue
            password = item.get("password_plain") or ""
            if not password and str(item.get("password", "")).startswith("dpapi:"):
                password = unprotect_secret(item["password"])
            name = self._suggest(str(item["name"]).strip(), (p.name for p in self.profiles))
            self.profiles.append(
                ConnectionProfile(
                    id=str(uuid.uuid4()), name=name, username=str(item.get("username", "")), password=password,
                    putty_path=str(item.get("putty_path") or DEFAULT_PUTTY_PATH),
                    save_output=bool(item.get("save_output", False)),
                    log_directory=str(item.get("log_directory") or default_logs_path()),
                )
            )
            imported.append(name)
        if not imported:
            raise ValueError("No profiles were found in this file.")
        self.save()
        return imported

    # -- NetBox connections
    def get_netbox_connection(self, connection_id: str) -> NetBoxConnection | None:
        return next((c for c in self.netbox_connections if c.id == connection_id), None)

    def save_netbox_connection(self, connection: NetBoxConnection) -> None:
        if any(c.id != connection.id and c.name.casefold() == connection.name.casefold() for c in self.netbox_connections):
            raise ValueError("NetBox connection names must be unique.")
        for index, current in enumerate(self.netbox_connections):
            if current.id == connection.id:
                self.netbox_connections[index] = connection
                self.save()
                return
        self.netbox_connections.append(connection)
        self.save()

    def delete_netbox_connection(self, connection_id: str) -> None:
        self.netbox_connections = [c for c in self.netbox_connections if c.id != connection_id]
        self.save()

    def record_sync(self, entry: dict) -> None:
        self.sync_history.insert(0, entry)
        self.sync_history = self.sync_history[:SYNC_HISTORY_LIMIT]
        finished = entry.get("finished_at") or now_iso()
        self.settings["last_sync_attempt_at"] = finished
        if entry.get("connections") and any(item.get("ok") for item in entry["connections"]):
            self.settings["last_sync_at"] = finished
        self.save()

    def next_sync_at(self) -> datetime | None:
        minutes = int(self.settings.get("sync_interval_minutes") or 0)
        if minutes <= 0 or not any(c.enabled for c in self.netbox_connections):
            return None
        # Space syncs from the last attempt, so a NetBox that keeps failing isn't retried every 30 seconds.
        last = self.settings.get("last_sync_attempt_at") or self.settings.get("last_sync_at") or ""
        try:
            return datetime.fromisoformat(last) + timedelta(minutes=minutes) if last else datetime.now()
        except ValueError:
            return datetime.now()

    def sync_due(self, now: datetime | None = None) -> bool:
        due = self.next_sync_at()
        return due is not None and (now or datetime.now()) >= due

    # -- settings and misc
    def update_settings(self, changes: dict) -> None:
        for key, value in changes.items():
            if key not in DEFAULT_SETTINGS or key.startswith("last_sync"):
                continue
            if key == "sync_interval_minutes":
                value = int(value)
                if value not in SYNC_INTERVALS:
                    raise ValueError("Choose one of the listed sync intervals.")
            elif key == "diagnostic_timeout":
                value = max(1, min(10, int(value)))
            else:
                value = bool(value)
            self.settings[key] = value
        self.save()

    def export_backup(self, path: Path) -> None:
        self.save()
        Path(path).write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")

    def restore_backup(self, path: Path) -> None:
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict) or not isinstance(raw.get("profiles"), list):
            raise ValueError("Backup does not contain launcher profiles.")
        atomic_write_text(self.path, json.dumps(raw, indent=2))
        self.__init__(self.path)

    def set_ui_theme(self, theme: str) -> None:
        self.ui_theme = theme if theme in APP_THEMES else DEFAULT_THEME
        self.save()

    def record_recent_device(self, device: Device, profile_name: str = "") -> None:
        record = {
            "ip": device.ip, "sysname": device.sysname, "port": device.port,
            "last_opened_at": now_iso(), "profile": profile_name,
        }
        self.recent_devices = [
            item for item in self.recent_devices
            if not (item.get("ip") == device.ip and str(item.get("port")) == str(device.port))
        ]
        self.recent_devices.insert(0, record)
        self.recent_devices = self.recent_devices[:RECENT_LIMIT]
        self.save()

    def clear_recent(self) -> None:
        self.recent_devices = []
        self.save()

    def add_group(self, name: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError("Group name is required.")
        if name.casefold() not in {g.casefold() for g in self.groups}:
            self.groups.append(name)
            self.groups.sort(key=str.casefold)
            self.save()

    def rename_group(self, old: str, new: str) -> None:
        self.groups = split_list([new if g == old else g for g in self.groups] + ([new] if old not in self.groups else []))
        self.groups.sort(key=str.casefold)
        self.save()

    def remove_group(self, name: str) -> None:
        self.groups = [g for g in self.groups if g != name]
        self.save()


# --------------------------------------------------------------------------- devices

class DeviceStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else devices_path()
        self.devices: list[Device] = []
        self.load_errors: list[str] = []
        self.load()

    def load(self) -> None:
        self.devices = []
        self.load_errors = []
        if not self.path.exists():
            return
        try:
            rows, errors = self.read_csv(self.path)
        except (OSError, ValueError) as exc:
            self.load_errors = [str(exc)]
            return
        self.load_errors = errors
        # Keep every row, even ones sharing an IP and port; they are numbered so each stays addressable.
        self.devices = rows
        self.number_duplicates()

    def number_duplicates(self) -> None:
        seen: dict[str, int] = {}
        for device in self.devices:
            count = seen.get(device.endpoint, 0)
            device._dup = count
            seen[device.endpoint] = count + 1

    @staticmethod
    def _dedupe(devices: Iterable[Device]) -> list[Device]:
        seen: set[str] = set()
        result: list[Device] = []
        for device in devices:
            if device.endpoint not in seen:
                seen.add(device.endpoint)
                result.append(device)
        return result

    def save(self) -> None:
        temp = self.path.with_name(self.path.name + ".tmp")
        with temp.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(DEVICE_FIELDS)
            # attrgetter is ~10x faster than dataclasses.asdict for thousands of rows.
            row = operator.attrgetter(*DEVICE_FIELDS)
            writer.writerows(row(device) for device in self.devices)
        os.replace(temp, self.path)

    @staticmethod
    def read_csv(path: Path) -> tuple[list[Device], list[str]]:
        """Parse a device CSV. Returns valid devices and readable per-row errors."""
        errors: list[str] = []
        devices: list[Device] = []
        with Path(path).open(newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)
            if not reader.fieldnames:
                raise ValueError("CSV has no header row.")
            columns = {name.strip().lower(): name for name in reader.fieldnames if name}
            ip_col = columns.get("ip") or columns.get("ip address") or columns.get("address")
            name_col = columns.get("sysname") or columns.get("hostname") or columns.get("name")
            port_col = columns.get("port")
            if not ip_col or not name_col or not port_col:
                raise ValueError("CSV must have ip, sysname (or hostname), and port columns.")

            def cell(row: dict, name: str, default: str = "") -> str:
                column = columns.get(name)
                return (row.get(column) or "").strip() if column else default

            for row_number, row in enumerate(reader, start=2):
                ip = (row.get(ip_col) or "").strip()
                port = (row.get(port_col) or "").strip()
                if not ip and not port and not any((value or "").strip() for value in row.values() if isinstance(value, str)):
                    continue
                if not ip or not port:
                    errors.append(f"Row {row_number}: IP and port are required.")
                    continue
                if not port.isdigit() or not 1 <= int(port) <= 65535:
                    errors.append(f"Row {row_number}: port must be 1–65535.")
                    continue
                values = {name: cell(row, name) for name in DEVICE_FIELDS if name not in {"ip", "sysname", "port"}}
                values["source"] = values["source"] or "manual"
                values["reachability"] = values["reachability"] or "unknown"
                devices.append(Device(ip=ip, sysname=(row.get(name_col) or "").strip() or ip, port=port, **values))
        return devices, errors

    def preview_csv(self, path: Path) -> tuple[int, list[str]]:
        """Validate an import without changing the inventory."""
        try:
            rows, errors = self.read_csv(path)
        except ValueError as exc:
            return 0, [str(exc)]
        return len(rows), errors

    def preview_csv_details(self, path: Path) -> dict:
        try:
            rows, errors = self.read_csv(path)
        except ValueError as exc:
            return {"valid": 0, "new": 0, "existing": 0, "errors": [str(exc)], "sample": []}
        existing_endpoints = {d.endpoint for d in self.devices}
        unique = self._dedupe(rows)
        new = [d for d in unique if d.endpoint not in existing_endpoints]
        return {
            "valid": len(rows),
            "new": len(new),
            "existing": len(unique) - len(new),
            "errors": errors,
            "sample": [d.to_ui() for d in unique[:6]],
        }

    def import_csv(self, path: Path, replace: bool = False, save: bool = True) -> int:
        rows, errors = self.read_csv(path)
        if errors:
            raise ValueError("CSV validation failed:\n" + "\n".join(errors[:10]))
        if replace:
            self.devices = rows
            added = len(self.devices)
        else:
            existing = {d.endpoint for d in self.devices}
            new = [d for d in self._dedupe(rows) if d.endpoint not in existing]
            self.devices.extend(new)
            added = len(new)
        self.number_duplicates()
        if save:
            self.save()
        return added

    def export_csv(self, path: Path, devices: Iterable[Device] | None = None) -> int:
        columns = ["ip", "sysname", "port", "group", "tags", "notes", "putty_session"]
        rows = list(devices if devices is not None else self.devices)
        with Path(path).open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for device in rows:
                writer.writerow(asdict(device))
        return len(rows)

    # -- lookups
    def find(self, key: str) -> Device | None:
        return next((d for d in self.devices if d.key == key), None)

    def find_many(self, keys: Iterable[str]) -> list[Device]:
        wanted = set(keys)
        return [d for d in self.devices if d.key in wanted]

    def find_by_ip(self, ip: str) -> Device | None:
        normalized = ip.strip()
        return next((d for d in self.devices if d.ip == normalized), None)

    def groups(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for device in self.devices:
            if device.group:
                counts[device.group] = counts.get(device.group, 0) + 1
        return counts

    # -- edits
    def add_or_update(self, device: Device) -> None:
        current = self.find(device.key)
        if current:
            current.sysname = device.sysname
            for name in ("group", "tags", "notes", "favorite_profiles", "putty_session"):
                value = getattr(device, name)
                if value:
                    setattr(current, name, value)
        else:
            self.devices.append(device)
        self.save()

    def update_device(self, old_key: str, ip: str, sysname: str, port: str, group: str = "", tags: str = "",
                      notes: str = "", putty_session: str = "") -> Device:
        """Replace a device's editable fields. Metadata such as source and timestamps is kept."""
        validate_endpoint(ip, port)
        current = self.find(old_key) if old_key else None
        endpoint = f"{ip.strip()}:{port.strip()}"
        moved = current is None or current.endpoint != endpoint
        if moved and any(d.endpoint == endpoint for d in self.devices if d is not current):
            raise ValueError(f"Another device already uses {endpoint}.")
        if current is None:
            current = Device(ip=ip.strip(), sysname=sysname.strip() or ip.strip(), port=port.strip())
            self.devices.append(current)
        current.ip, current.port = ip.strip(), port.strip()
        current.sysname = sysname.strip() or current.ip
        current.group = group.strip()
        current.tags = ",".join(split_list(tags))
        current.notes = notes.strip()
        current.putty_session = putty_session.strip()
        if moved:
            self.number_duplicates()
        self.save()
        return current

    def delete(self, device_or_keys: Device | Iterable[str]) -> int:
        keys = {device_or_keys.key} if isinstance(device_or_keys, Device) else set(device_or_keys)
        before = len(self.devices)
        self.devices = [d for d in self.devices if d.key not in keys]
        self.number_duplicates()
        self.save()
        return before - len(self.devices)

    def set_group(self, keys: Iterable[str], group: str) -> int:
        devices = self.find_many(keys)
        for device in devices:
            device.group = group.strip()
        self.save()
        return len(devices)

    def rename_group(self, old: str, new: str) -> int:
        changed = 0
        for device in self.devices:
            if device.group == old:
                device.group = new.strip()
                changed += 1
        self.save()
        return changed

    def add_tags(self, keys: Iterable[str], tags: Iterable[str]) -> int:
        devices = self.find_many(keys)
        for device in devices:
            device.tags = ",".join(split_list(device.tag_list + list(tags)))
        self.save()
        return len(devices)

    def remove_tags(self, keys: Iterable[str], tags: Iterable[str]) -> int:
        drop = {t.casefold() for t in tags}
        devices = self.find_many(keys)
        for device in devices:
            device.tags = ",".join(t for t in device.tag_list if t.casefold() not in drop)
        self.save()
        return len(devices)

    def set_favorite(self, keys: Iterable[str], favorite: bool) -> int:
        devices = self.find_many(keys)
        for device in devices:
            device.favorite_profiles = "*" if favorite else ""
        self.save()
        return len(devices)

    def record_check(self, key: str, result: dict, save: bool = True) -> None:
        device = self.find(key)
        if not device:
            return
        device.reachability = result.get("status", "unknown")
        device.last_checked = result.get("checked_at", now_iso())
        port_ms = (result.get("port") or {}).get("ms")
        device.latency_ms = latency_value(port_ms) if isinstance(port_ms, (int, float)) else ""
        if save:
            self.save()

    def sync_netbox_devices(self, records_by_connection: dict[str, list[NetBoxDevice]]) -> tuple[int, int, int]:
        """Upsert successful NetBox results and remove stale NetBox-owned rows only.

        Returns (added, updated, removed). Manual, CSV and quick-launch rows are never removed.
        """
        added = updated = removed = 0
        seen_by_connection: dict[str, set[str]] = {}
        for connection_id, records in records_by_connection.items():
            seen_by_connection[connection_id] = {r.device_id for r in records}
            for record in records:
                managed = next(
                    (d for d in self.devices if d.source == "netbox" and d.netbox_connection_id == connection_id
                     and d.netbox_device_id == record.device_id),
                    None,
                )
                if managed:
                    if managed.ip != record.ip or managed.sysname != record.sysname:
                        managed.ip, managed.sysname = record.ip, record.sysname
                        updated += 1
                    managed.role = record.bucket
                    continue
                matching_name = next((d for d in self.devices if d.sysname.casefold() == record.sysname.casefold()), None)
                if matching_name:
                    if matching_name.ip != record.ip:
                        matching_name.ip = record.ip
                        updated += 1
                    matching_name.role = matching_name.role or record.bucket
                    continue
                unnamed_ip = next(
                    (d for d in self.devices if d.ip == record.ip and (not d.sysname.strip() or d.sysname.strip() == d.ip)),
                    None,
                )
                if unnamed_ip:
                    unnamed_ip.sysname = record.sysname
                    unnamed_ip.role = unnamed_ip.role or record.bucket
                    updated += 1
                    continue
                self.devices.append(
                    Device(ip=record.ip, sysname=record.sysname or record.ip, port="22", source="netbox",
                           netbox_connection_id=connection_id, netbox_device_id=record.device_id, role=record.bucket)
                )
                added += 1

        retained: list[Device] = []
        for device in self.devices:
            successful_ids = seen_by_connection.get(device.netbox_connection_id)
            if device.source == "netbox" and successful_ids is not None and device.netbox_device_id not in successful_ids:
                removed += 1
                continue
            retained.append(device)
        self.devices = retained
        self.number_duplicates()
        self.save()
        return added, updated, removed


def validate_endpoint(ip: str, port: str) -> None:
    if not ip.strip():
        raise ValueError("IP address or hostname is required.")
    if not re.fullmatch(r"[A-Za-z0-9._:\-\[\]]+", ip.strip()):
        raise ValueError("The address can only contain letters, numbers, dots, dashes and colons.")
    if not port.strip().isdigit() or not 1 <= int(port.strip()) <= 65535:
        raise ValueError("Port must be a number from 1 to 65535.")


# --------------------------------------------------------------------------- NetBox

class NetBoxFetchError(RuntimeError):
    pass


NETBOX_TIMEOUT_S = 30


def normalize_netbox_url(url: str) -> str:
    """pynetbox adds /api itself, so drop a pasted /api suffix and trailing slashes."""
    url = url.strip().rstrip("/")
    return url[:-4].rstrip("/") if url.lower().endswith("/api") else url


def friendly_netbox_error(exc: Exception, url: str) -> str:
    """Turn requests/pynetbox exceptions into a sentence a network engineer can act on."""
    from urllib.parse import urlparse

    host = urlparse(url).hostname or url
    text = str(exc)
    lowered = text.lower()
    response = getattr(exc, "req", None) or getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status in (401, 403):
        return "NetBox rejected the API token. Check that it's correct and allowed to read devices."
    if status == 404:
        return "No NetBox API was found at this URL. Check the address."
    if isinstance(status, int) and status >= 500:
        return f"NetBox had an internal error (HTTP {status}). Try again later."
    if "nameresolution" in lowered or "getaddrinfo" in lowered or "name or service not known" in lowered:
        return f"Couldn't find {host}. Check the URL and that you're on the right network or VPN."
    if "timed out" in lowered or "timeout" in lowered:
        return f"{host} didn't answer within {NETBOX_TIMEOUT_S} seconds. Check the VPN or firewall."
    if "refused" in lowered:
        return f"{host} refused the connection. Check the URL and port."
    if "ssl" in lowered or "certificate" in lowered:
        return f"The secure connection to {host} failed. Check the URL uses the right http or https."
    return text[:240]


class NetBoxClient:
    """Fetch active access, distribution and core devices using a connection's filters."""

    @staticmethod
    def role_slug(device) -> str:
        role = getattr(device, "role", None) or getattr(device, "device_role", None)
        return str(getattr(role, "slug", "") or "").strip().lower()

    @classmethod
    def bucket_for(cls, device) -> str:
        name = str(getattr(device, "name", "") or "").strip().lower()
        for suffix, bucket in (("-acc", NETBOX_ROLE_ACCESS), ("-dis", NETBOX_ROLE_DISTRIBUTION), ("-core", NETBOX_ROLE_CORE)):
            if name.endswith(suffix):
                return bucket
        role = cls.role_slug(device)
        return role if role in NETBOX_ROLES else ""

    @staticmethod
    def api_filters(connection: NetBoxConnection) -> dict:
        filters: dict = {"status": connection.status or "active"}
        manufacturers = [m.lower() for m in split_list(connection.manufacturers)]
        if manufacturers:
            filters["manufacturer"] = manufacturers
        sites = [s.lower() for s in split_list(connection.sites)]
        if sites:
            filters["site"] = sites
        return filters

    @staticmethod
    def _api(connection: NetBoxConnection):
        if not connection.url or not connection.api_key:
            raise NetBoxFetchError("URL or API key is missing.")
        import pynetbox
        import requests
        import urllib3

        class TimeoutSession(requests.Session):
            """requests has no default timeout; a hung NetBox must not stall the sync forever."""

            def request(self, *args, **kwargs):
                kwargs.setdefault("timeout", NETBOX_TIMEOUT_S)
                return super().request(*args, **kwargs)

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        netbox = pynetbox.api(normalize_netbox_url(connection.url), token=connection.api_key)
        session = TimeoutSession()
        session.verify = False
        netbox.http_session = session
        return netbox

    def filter_records(self, connection: NetBoxConnection, devices: Iterable) -> list[NetBoxDevice]:
        roles = set(split_list(connection.roles)) or set(NETBOX_ROLES)
        ignored = {m.casefold() for m in split_list(connection.ignored_models)}
        fetched: list[NetBoxDevice] = []
        for device in devices:
            device_type = getattr(device, "device_type", None)
            if str(getattr(device_type, "model", "") or "").casefold() in ignored:
                continue
            primary_ip = getattr(device, "primary_ip", None)
            if not primary_ip:
                continue
            bucket = self.bucket_for(device)
            if not bucket or bucket not in roles:
                continue
            ip = str(getattr(primary_ip, "address", "") or "").split("/", 1)[0].strip()
            device_id = str(getattr(device, "id", "") or "")
            if not ip or not device_id:
                continue
            sysname = str(getattr(device, "name", "") or "").strip() or ip
            fetched.append(NetBoxDevice(connection.id, device_id, sysname, ip, bucket))
        return fetched

    def fetch(self, connection: NetBoxConnection) -> list[NetBoxDevice]:
        try:
            netbox = self._api(connection)
            return self.filter_records(connection, netbox.dcim.devices.filter(**self.api_filters(connection)))
        except NetBoxFetchError:
            raise
        except Exception as exc:
            raise NetBoxFetchError(friendly_netbox_error(exc, connection.url)) from exc

    def test(self, connection: NetBoxConnection) -> dict:
        started = time.perf_counter()
        try:
            netbox = self._api(connection)
            status = netbox.status()
            count = netbox.dcim.devices.count(**self.api_filters(connection))
        except NetBoxFetchError:
            raise
        except Exception as exc:
            raise NetBoxFetchError(friendly_netbox_error(exc, connection.url)) from exc
        return {
            "version": str((status or {}).get("netbox-version", "")),
            "count": int(count),
            "ms": round((time.perf_counter() - started) * 1000),
        }


def run_netbox_sync(
    profile_store: ProfileStore,
    device_store: DeviceStore,
    connection_ids: Iterable[str] | None,
    trigger: str,
    client: NetBoxClient | None = None,
    lock: threading.RLock | None = None,
) -> dict:
    """Fetch the chosen connections, merge results and record a history entry."""
    client = client or NetBoxClient()
    ids = list(connection_ids) if connection_ids else [c.id for c in profile_store.netbox_connections if c.enabled]
    connections = [c for c in profile_store.netbox_connections if c.id in ids]
    started_at, started = now_iso(), time.perf_counter()
    fetched: dict[str, list[NetBoxDevice]] = {}
    results: list[dict] = []
    for connection in connections:
        try:
            records = client.fetch(connection)
            fetched[connection.id] = records
            results.append({"id": connection.id, "name": connection.name, "ok": True, "count": len(records), "error": ""})
        except NetBoxFetchError as exc:
            results.append({"id": connection.id, "name": connection.name, "ok": False, "count": 0, "error": str(exc)})
    added = updated = removed = 0
    save_error = ""
    if fetched:
        try:
            if lock:
                with lock:
                    added, updated, removed = device_store.sync_netbox_devices(fetched)
            else:
                added, updated, removed = device_store.sync_netbox_devices(fetched)
        except Exception as exc:  # disk full, file locked, etc.
            save_error = f"Could not save synced devices: {exc}"
    entry = {
        "id": str(uuid.uuid4()),
        "trigger": trigger,
        "started_at": started_at,
        "finished_at": now_iso(),
        "duration_s": round(time.perf_counter() - started, 1),
        "connections": results,
        "added": added,
        "updated": updated,
        "removed": removed,
        "error": save_error,
    }
    if lock:
        with lock:
            profile_store.record_sync(entry)
    else:
        profile_store.record_sync(entry)
    return entry


# --------------------------------------------------------------------------- diagnostics

ANSI_RE = re.compile(r"\x1b(\[[0-?]*[ -/]*[@-~]|\][^\x07]*\x07|[()][A-Za-z0-9]|[=>78])")
TELNET_IAC_RE = re.compile(rb"\xff[\xfb-\xfe].|\xff\xfa.*?\xff\xf0|\xff.", re.S)


def latency_text(ms: float | None) -> str:
    """'under 1 ms' reads better than '0 ms' on fast links."""
    if ms is None:
        return ""
    return "under 1 ms" if ms < 1 else f"{ms:.0f} ms"


def latency_value(ms: float | None) -> str:
    """Compact form stored on the device and shown in tooltips."""
    if ms is None:
        return ""
    return "<1" if ms < 1 else f"{ms:.0f}"


def ping(host: str, timeout_ms: int = 1000) -> dict:
    if sys.platform == "win32":
        command = ["ping", "-n", "1", "-w", str(int(timeout_ms)), host]
    else:
        command = ["ping", "-c", "1", "-W", str(max(1, int(timeout_ms / 1000))), host]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout_ms / 1000 + 4,
                                creationflags=NO_WINDOW, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "ms": None, "detail": f"Ping could not run: {exc}"}
    output = result.stdout or ""
    replied = "ttl=" in output.lower()
    match = re.search(r"time([=<])\s*([\d.]+)\s*ms", output, re.I)
    ms = float(match.group(2)) if match else None
    if match and match.group(1) == "<":
        ms = min(ms, 0.5)  # Windows prints time<1ms for sub-millisecond replies
    if replied:
        return {"ok": True, "ms": ms, "detail": f"Reply in {latency_text(ms)}" if ms is not None else "Reply received"}
    lowered = output.lower()
    if "could not find host" in lowered or "unknown host" in lowered:
        detail = "Host name could not be resolved"
    elif "unreachable" in lowered:
        detail = "Destination unreachable"
    else:
        detail = f"No reply within {timeout_ms / 1000:g} s"
    return {"ok": False, "ms": None, "detail": detail}


def clean_banner(data: bytes) -> str:
    text = TELNET_IAC_RE.sub(b"", data).decode("utf-8", errors="replace")
    text = ANSI_RE.sub("", text)
    text = "".join(ch for ch in text if ch.isprintable() or ch in "\n")
    return " ".join(text.split())[:160]


def probe_port(host: str, port: int | str, timeout: float = 3.0, read_banner: bool = True) -> dict:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            ms = (time.perf_counter() - started) * 1000
            banner = ""
            if read_banner:
                sock.settimeout(min(2.0, timeout))
                try:
                    banner = clean_banner(sock.recv(512))
                except (socket.timeout, OSError):
                    banner = ""
            return {"ok": True, "ms": ms, "detail": f"Port {port} open in {latency_text(ms)}", "banner": banner}
    except ConnectionRefusedError:
        detail = f"Port {port} refused the connection"
    except socket.timeout:
        detail = f"No answer on port {port} within {timeout:g} s"
    except socket.gaierror:
        detail = "Host name could not be resolved"
    except OSError as exc:
        detail = exc.strerror or str(exc)
    return {"ok": False, "ms": None, "detail": detail, "banner": ""}


def diagnose(ip: str, port: str, timeout: float = 3.0, full: bool = True) -> dict:
    """Check one device. Full checks always ping and read the banner; quick checks ping only on failure."""
    port_result = probe_port(ip, port, timeout=timeout, read_banner=full)
    ping_result = ping(ip, int(timeout * 1000)) if full or not port_result["ok"] else None
    if port_result["ok"]:
        status = "up"
    elif ping_result and ping_result["ok"]:
        status = "filtered"
    else:
        status = "down"
    return {"status": status, "port": port_result, "ping": ping_result, "checked_at": now_iso()}


def check_many(
    targets: list[tuple[str, str, str]],
    timeout: float = 2.0,
    workers: int = 32,
    on_result: Callable[[str, dict], None] | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, dict]:
    """Quick-check many (key, ip, port) targets in parallel."""
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(targets) or 1))) as pool:
        futures = {}
        for key, ip, port in targets:
            if cancel and cancel.is_set():
                break
            futures[pool.submit(diagnose, ip, port, timeout, False)] = key
        for future in as_completed(futures):
            key = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"status": "unknown", "port": {"ok": False, "detail": str(exc)}, "ping": None, "checked_at": now_iso()}
            results[key] = result
            if on_result:
                on_result(key, result)
    return results


# --------------------------------------------------------------------------- PuTTY

def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "device"


def session_log_path(profile: ConnectionProfile, device: Device, when: datetime | None = None) -> Path | None:
    if not profile.save_output:
        return None
    directory = Path(profile.log_directory or default_logs_path())
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (when or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    parts = [stamp, safe_filename(profile.name), safe_filename(device.sysname), safe_filename(device.ip), safe_filename(device.port)]
    return directory / ("_".join(parts) + ".log")


def build_putty_command(profile: ConnectionProfile, device: Device, log_path: Path | None) -> list[str]:
    if device.putty_session:
        command = [profile.putty_path, "-load", device.putty_session]
    elif device.protocol == "telnet":
        # Telnet auto-login is unreliable, so the user signs in manually.
        command = [profile.putty_path, "-telnet", device.ip, "-P", str(device.port)]
    else:
        command = [profile.putty_path, "-ssh", device.ip, "-P", str(device.port)]
        if profile.username:
            command += ["-l", profile.username]
        if profile.password:
            command += ["-pw", profile.password]
    if log_path:
        command += ["-sessionlog", str(log_path), "-logoverwrite"]
    return command


def putty_pids() -> list[str]:
    if sys.platform != "win32":
        return []
    try:
        result = subprocess.run(["tasklist", "/FI", "IMAGENAME eq putty.exe", "/FO", "CSV", "/NH"],
                                capture_output=True, text=True, check=False, creationflags=NO_WINDOW)
    except OSError:
        return []
    return [row[1] for row in csv.reader(result.stdout.splitlines()) if len(row) >= 2 and row[0].casefold() == "putty.exe"]


def close_putty(pids: Iterable[str]) -> tuple[int, list[str]]:
    closed, failures = 0, []
    for pid in pids:
        try:
            result = subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True,
                                    check=False, creationflags=NO_WINDOW)
            if result.returncode == 0:
                closed += 1
            else:
                failures.append(str(pid))
        except OSError:
            failures.append(str(pid))
    return closed, failures


def putty_saved_sessions() -> list[dict]:
    """Saved PuTTY sessions from the registry, with their host names."""
    if sys.platform != "win32":
        return []
    try:
        import winreg
        from urllib.parse import unquote
    except ImportError:
        return []
    sessions: list[dict] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\SimonTatham\PuTTY\Sessions") as root:
            index = 0
            while True:
                try:
                    raw_name = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                host = ""
                try:
                    with winreg.OpenKey(root, raw_name) as key:
                        host = str(winreg.QueryValueEx(key, "HostName")[0] or "")
                except OSError:
                    pass
                name = unquote(raw_name)
                if name != "Default Settings":
                    sessions.append({"name": name, "host": host})
    except OSError:
        return []
    return sorted(sessions, key=lambda item: item["name"].casefold())


# --------------------------------------------------------------------------- session logs

def log_directories(profiles: Iterable[ConnectionProfile]) -> list[Path]:
    seen: list[Path] = []
    for path in [default_logs_path(), *(Path(p.log_directory) for p in profiles if p.log_directory)]:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved not in seen:
            seen.append(resolved)
    return seen


def parse_log_name(name: str, profile_names: Iterable[str] = ()) -> dict:
    stem = name[:-4] if name.lower().endswith(".log") else name
    match = re.match(r"^(\d{4}-\d{2}-\d{2})_(\d{6})_(.+)$", stem)
    info = {"opened_at": "", "profile": "", "sysname": "", "ip": "", "port": ""}
    if not match:
        info["sysname"] = stem
        return info
    date, clock, rest = match.groups()
    info["opened_at"] = f"{date}T{clock[:2]}:{clock[2:4]}:{clock[4:]}"
    parts = rest.split("_")
    if len(parts) >= 4 and parts[-1].isdigit():
        info["port"], info["ip"] = parts[-1], parts[-2]
        middle = "_".join(parts[:-2])
        for profile in sorted((safe_filename(p) for p in profile_names), key=len, reverse=True):
            if middle.startswith(profile + "_"):
                info["profile"], info["sysname"] = profile, middle[len(profile) + 1:]
                break
        else:
            info["profile"], _, info["sysname"] = middle.partition("_")
    else:
        info["sysname"] = rest
    return info


def list_session_logs(profiles: list[ConnectionProfile], limit: int = 1500) -> list[dict]:
    names = [p.name for p in profiles]
    logs: list[dict] = []
    for directory in log_directories(profiles):
        if not directory.is_dir():
            continue
        try:
            entries = list(directory.glob("*.log"))
        except OSError:
            continue
        for path in entries:
            try:
                stat = path.stat()
            except OSError:
                continue
            logs.append({
                "path": str(path),
                "name": path.name,
                "folder": str(directory),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                **parse_log_name(path.name, names),
            })
    logs.sort(key=lambda item: item["modified"], reverse=True)
    return logs[:limit]


def resolve_log_path(path: str, profiles: list[ConnectionProfile]) -> Path:
    candidate = Path(path).resolve()
    if candidate.suffix.lower() != ".log" or not candidate.is_file():
        raise ValueError("That session log no longer exists.")
    if not any(candidate.parent == folder or folder in candidate.parents for folder in log_directories(profiles)):
        raise ValueError("Only logs inside a profile's log folder can be opened here.")
    return candidate


def read_log_tail(path: Path, max_bytes: int = 400_000) -> dict:
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
        data = handle.read()
    text = data.decode("utf-8", errors="replace")
    text = ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch.isprintable() or ch in "\n\t")
    return {"text": text, "truncated": size > max_bytes, "size": size}


# --------------------------------------------------------------------------- updates

def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", value)[:4]) or (0,)


def check_latest_release(timeout: float = 10) -> dict:
    import requests

    response = requests.get(f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest", timeout=timeout)
    if response.status_code == 404:
        return {"available": False, "message": "No GitHub release has been published yet.", "url": ""}
    response.raise_for_status()
    release = response.json()
    latest = str(release.get("tag_name", "")).lstrip("v")
    url = release.get("html_url") or f"https://github.com/{GITHUB_REPOSITORY}/releases"
    if latest and version_tuple(latest) > version_tuple(APP_VERSION):
        return {"available": True, "message": f"Version {latest} is available.", "url": url, "version": latest}
    return {"available": False, "message": f"You're on the latest version ({APP_VERSION}).", "url": url, "version": latest}


def open_path(path: str | Path, select: bool = False) -> None:
    if sys.platform != "win32":
        return
    if select:
        subprocess.Popen(["explorer", "/select,", str(path)], creationflags=NO_WINDOW)
    else:
        os.startfile(str(path))  # type: ignore[attr-defined]


def copy_to_clipboard(text: str) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    data = (text + "\0").encode("utf-16-le")
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.03)
    else:
        return False
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(0x0002, len(data))
        pointer = kernel32.GlobalLock(handle)
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(handle)
        return bool(user32.SetClipboardData(13, handle))
    finally:
        user32.CloseClipboard()
