import base64
import csv
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from dataclasses import asdict, dataclass
from pathlib import Path

import pynetbox
import requests
import urllib3
from tkinter import (
    BOTH,
    BooleanVar,
    Checkbutton,
    END,
    LEFT,
    RIGHT,
    X,
    Button,
    Entry,
    Frame,
    Label,
    LabelFrame,
    Listbox,
    Menu,
    StringVar,
    Tk,
    Toplevel,
    filedialog,
    messagebox,
    simpledialog,
    ttk,
)


APP_NAME = "Chirag Putty Launcher"
APP_VERSION = "1.0.0"
GITHUB_REPOSITORY = "chiragbhatn/Chirag-Putty-Launcher"
DEFAULT_PUTTY_PATH = r"C:\Program Files\PuTTY\putty.exe"
DEFAULT_THEME = "Slate"
APP_THEMES = {
    "Slate": {
        "bg": "#eef2f7", "surface": "#ffffff", "surface_2": "#e7edf6", "header": "#14213d",
        "text": "#24334d", "muted": "#63718a", "line": "#d5deea", "accent": "#2463d4", "accent_active": "#194ca8",
        "selection": "#d9e7ff", "header_text": "#f8fbff", "header_muted": "#b8c9e6",
    },
    "Midnight": {
        "bg": "#101827", "surface": "#172235", "surface_2": "#1e2c43", "header": "#0b1324",
        "text": "#ecf3ff", "muted": "#a9b8d0", "line": "#31445f", "accent": "#6ba4ff", "accent_active": "#4b83dc",
        "selection": "#244a83", "header_text": "#f4f8ff", "header_muted": "#a9bfe2",
    },
    "Ocean": {
        "bg": "#e9f3f5", "surface": "#fbfefd", "surface_2": "#d9e8ed", "header": "#0c3b4b",
        "text": "#18343c", "muted": "#58737c", "line": "#c4d8de", "accent": "#087f8c", "accent_active": "#05636e",
        "selection": "#cbe9ed", "header_text": "#f4ffff", "header_muted": "#b9d8de",
    },
    "Graphite": {
        "bg": "#24272d", "surface": "#30343b", "surface_2": "#3b4048", "header": "#181a1f",
        "text": "#f0f1f4", "muted": "#bdc2ca", "line": "#505660", "accent": "#d38a3b", "accent_active": "#ae6c28",
        "selection": "#5b4c3a", "header_text": "#ffffff", "header_muted": "#d4d7dc",
    },
}


def app_data_dir() -> Path:
    base = os.getenv("APPDATA") or str(Path.home())
    path = Path(base) / "ChiragPuttyLauncher"
    path.mkdir(parents=True, exist_ok=True)
    return path


CONFIG_PATH = app_data_dir() / "config.json"
DEVICES_PATH = app_data_dir() / "devices.csv"
DEFAULT_LOGS_PATH = app_data_dir() / "Logs"
NETBOX_ROLE_ACCESS = "access"
NETBOX_ROLE_CORE = "core"
NETBOX_ROLE_DISTRIBUTION = "distribution"
NETBOX_ROLES = [NETBOX_ROLE_ACCESS, NETBOX_ROLE_CORE, NETBOX_ROLE_DISTRIBUTION]
NETBOX_MANUFACTURERS = ["huawei", "juniper"]
NETBOX_IGNORED_MODELS = {"QFX5120-32C-AFO"}

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


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

    @property
    def protocol(self) -> str:
        return "telnet" if self.port.strip() == "23" else "ssh"


@dataclass
class ConnectionProfile:
    id: str
    name: str
    username: str
    password: str
    putty_path: str
    save_output: bool
    log_directory: str


@dataclass
class NetBoxConnection:
    id: str
    name: str
    url: str
    api_key: str


@dataclass(frozen=True)
class NetBoxDevice:
    connection_id: str
    device_id: str
    sysname: str
    ip: str
    bucket: str


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
        in_blob = DATA_BLOB(
            len(encrypted),
            ctypes.cast(ctypes.create_string_buffer(encrypted), ctypes.POINTER(ctypes.c_char)),
        )
        out_blob = DATA_BLOB()
        if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
            raise OSError("CryptUnprotectData failed")
        try:
            plain = ctypes.string_at(out_blob.pbData, out_blob.cbData)
            return plain.decode("utf-8")
        finally:
            kernel32.LocalFree(out_blob.pbData)
    except Exception:
        return ""


class ProfileStore:
    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path
        self.profiles: list[ConnectionProfile] = []
        self.netbox_connections: list[NetBoxConnection] = []
        self.recent_devices: list[dict] = []
        self.ui_theme = "light"
        self.default_profile_id = ""
        self.active_profile_id = ""
        self.load()

    def load(self) -> None:
        raw = {}
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}

        if isinstance(raw.get("profiles"), list):
            self.profiles = [self.profile_from_dict(item) for item in raw["profiles"] if isinstance(item, dict)]
            self.default_profile_id = raw.get("default_profile_id", "")
            self.active_profile_id = raw.get("active_profile_id", "")
        elif raw:
            # Migrate the original single-profile configuration without exposing its password.
            self.profiles = [
                ConnectionProfile(
                    id=str(uuid.uuid4()),
                    name="Default Profile",
                    username=raw.get("username", ""),
                    password=unprotect_secret(raw.get("password", "")),
                    putty_path=raw.get("putty_path", DEFAULT_PUTTY_PATH),
                    save_output=False,
                    log_directory=str(DEFAULT_LOGS_PATH),
                )
            ]

        if isinstance(raw.get("netbox_connections"), list):
            self.netbox_connections = [
                self.netbox_connection_from_dict(item)
                for item in raw["netbox_connections"]
                if isinstance(item, dict)
            ]

        legacy_theme = raw.get("ui_theme")
        theme_migration = {"light": "Slate", "dark": "Midnight"}
        self.ui_theme = theme_migration.get(legacy_theme, legacy_theme)
        if self.ui_theme not in APP_THEMES:
            self.ui_theme = DEFAULT_THEME
        if isinstance(raw.get("recent_devices"), list):
            self.recent_devices = [
                item
                for item in raw["recent_devices"]
                if isinstance(item, dict) and item.get("ip") and item.get("port")
            ][:8]

        if not self.profiles:
            self.profiles = [self.new_profile("Default Profile")]
        if not self.get(self.default_profile_id):
            self.default_profile_id = self.profiles[0].id
        if not self.get(self.active_profile_id):
            self.active_profile_id = self.default_profile_id
        self.save()

    def profile_from_dict(self, raw: dict) -> ConnectionProfile:
        return ConnectionProfile(
            id=raw.get("id") or str(uuid.uuid4()),
            name=raw.get("name") or "Unnamed Profile",
            username=raw.get("username", ""),
            password=unprotect_secret(raw.get("password", "")),
            putty_path=raw.get("putty_path", DEFAULT_PUTTY_PATH),
            save_output=bool(raw.get("save_output", False)),
            log_directory=raw.get("log_directory") or str(DEFAULT_LOGS_PATH),
        )

    def new_profile(self, name: str) -> ConnectionProfile:
        return ConnectionProfile(
            id=str(uuid.uuid4()),
            name=name,
            username="",
            password="",
            putty_path=DEFAULT_PUTTY_PATH,
            save_output=False,
            log_directory=str(DEFAULT_LOGS_PATH),
        )

    def netbox_connection_from_dict(self, raw: dict) -> NetBoxConnection:
        return NetBoxConnection(
            id=raw.get("id") or str(uuid.uuid4()),
            name=raw.get("name") or "Unnamed NetBox",
            url=(raw.get("url") or "").rstrip("/"),
            api_key=unprotect_secret(raw.get("api_key", "")),
        )

    def new_netbox_connection(self, name: str) -> NetBoxConnection:
        return NetBoxConnection(id=str(uuid.uuid4()), name=name, url="", api_key="")

    def suggested_netbox_connection_name(self) -> str:
        existing = {connection.name.casefold() for connection in self.netbox_connections}
        if "new netbox" not in existing:
            return "New NetBox"
        index = 2
        while f"new netbox {index}" in existing:
            index += 1
        return f"New NetBox {index}"

    def suggested_profile_name(self) -> str:
        existing = {profile.name.casefold() for profile in self.profiles}
        if "new profile" not in existing:
            return "New Profile"
        index = 2
        while f"new profile {index}" in existing:
            index += 1
        return f"New Profile {index}"

    def save(self) -> None:
        raw = {
            "version": 2,
            "default_profile_id": self.default_profile_id,
            "active_profile_id": self.active_profile_id,
            "ui_theme": self.ui_theme,
            "profiles": [
                {
                    "id": profile.id,
                    "name": profile.name,
                    "username": profile.username,
                    "password": protect_secret(profile.password),
                    "putty_path": profile.putty_path,
                    "save_output": profile.save_output,
                    "log_directory": profile.log_directory,
                }
                for profile in self.profiles
            ],
            "netbox_connections": [
                {
                    "id": connection.id,
                    "name": connection.name,
                    "url": connection.url,
                    "api_key": protect_secret(connection.api_key),
                }
                for connection in self.netbox_connections
            ],
            "recent_devices": self.recent_devices,
        }
        self.path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    def get(self, profile_id: str) -> ConnectionProfile | None:
        return next((profile for profile in self.profiles if profile.id == profile_id), None)

    def active_profile(self) -> ConnectionProfile:
        return self.get(self.active_profile_id) or self.profiles[0]

    def save_profile(self, profile: ConnectionProfile) -> None:
        if any(item.id != profile.id and item.name.casefold() == profile.name.casefold() for item in self.profiles):
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
        self.profiles = [profile for profile in self.profiles if profile.id != profile_id]
        if self.default_profile_id == profile_id:
            self.default_profile_id = self.profiles[0].id
        if self.active_profile_id == profile_id:
            self.active_profile_id = self.default_profile_id
        self.save()

    def save_netbox_connection(self, connection: NetBoxConnection) -> None:
        if any(
            item.id != connection.id and item.name.casefold() == connection.name.casefold()
            for item in self.netbox_connections
        ):
            raise ValueError("NetBox connection names must be unique.")
        for index, current in enumerate(self.netbox_connections):
            if current.id == connection.id:
                self.netbox_connections[index] = connection
                self.save()
                return
        self.netbox_connections.append(connection)
        self.save()

    def delete_netbox_connection(self, connection_id: str) -> None:
        self.netbox_connections = [
            connection for connection in self.netbox_connections if connection.id != connection_id
        ]
        self.save()

    def export_backup(self, path: Path) -> None:
        if not self.path.exists():
            self.save()
        Path(path).write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")

    def restore_backup(self, path: Path) -> None:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("profiles"), list):
            raise ValueError("Backup does not contain launcher profiles.")
        self.path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        self.profiles = []
        self.netbox_connections = []
        self.recent_devices = []
        self.load()

    def set_ui_theme(self, theme: str) -> None:
        self.ui_theme = theme if theme in APP_THEMES else DEFAULT_THEME
        self.save()

    def record_recent_device(self, device: Device) -> None:
        record = {
            "ip": device.ip,
            "sysname": device.sysname,
            "port": device.port,
            "last_opened_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.recent_devices = [
            item
            for item in self.recent_devices
            if not (item.get("ip") == device.ip and str(item.get("port")) == str(device.port))
        ]
        self.recent_devices.insert(0, record)
        self.recent_devices = self.recent_devices[:8]
        self.save()


class DeviceStore:
    def __init__(self, path: Path = DEVICES_PATH):
        self.path = path
        self.devices: list[Device] = []
        self.load()

    def load(self) -> None:
        self.devices = []
        if not self.path.exists():
            return
        self.import_csv(self.path, replace=True, save=False)

    def save(self) -> None:
        with self.path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=list(Device.__dataclass_fields__),
            )
            writer.writeheader()
            for device in self.devices:
                writer.writerow(asdict(device))

    def preview_csv(self, path: Path) -> tuple[int, list[str]]:
        """Validate an import without changing the inventory."""
        errors: list[str] = []
        valid = 0
        with Path(path).open(newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)
            if not reader.fieldnames:
                return 0, ["CSV has no header row."]
            fields = {field.lower(): field for field in reader.fieldnames}
            ip_col, sysname_col, port_col = fields.get("ip"), fields.get("sysname") or fields.get("hostname"), fields.get("port")
            if not ip_col or not sysname_col or not port_col:
                return 0, ["CSV must have ip, sysname (or hostname), and port columns."]
            for row_number, row in enumerate(reader, start=2):
                ip, port = (row.get(ip_col) or "").strip(), (row.get(port_col) or "").strip()
                if not ip or not port:
                    errors.append(f"Row {row_number}: IP and port are required.")
                elif not port.isdigit() or not 1 <= int(port) <= 65535:
                    errors.append(f"Row {row_number}: port must be 1–65535.")
                else:
                    valid += 1
        return valid, errors

    def import_csv(self, path: Path, replace: bool = False, save: bool = True) -> None:
        valid, errors = self.preview_csv(path)
        if errors:
            raise ValueError("CSV validation failed:\n" + "\n".join(errors[:10]))
        imported: list[Device] = []
        with Path(path).open(newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)
            if not reader.fieldnames:
                raise ValueError("CSV has no header row.")
            fields = {field.lower(): field for field in reader.fieldnames}
            ip_col = fields.get("ip")
            sysname_col = fields.get("sysname") or fields.get("hostname")
            port_col = fields.get("port")
            if not ip_col or not sysname_col or not port_col:
                raise ValueError("CSV must have ip, sysname, port columns.")

            for row in reader:
                ip = (row.get(ip_col) or "").strip()
                sysname = (row.get(sysname_col) or "").strip()
                port = (row.get(port_col) or "").strip()
                if ip and port:
                    imported.append(
                        Device(
                            ip=ip,
                            sysname=sysname or ip,
                            port=port,
                            source=(row.get(fields.get("source", "")) or "manual").strip() or "manual",
                            netbox_connection_id=(row.get(fields.get("netbox_connection_id", "")) or "").strip(),
                            netbox_device_id=(row.get(fields.get("netbox_device_id", "")) or "").strip(),
                            group=(row.get(fields.get("group", "")) or "").strip(),
                            tags=(row.get(fields.get("tags", "")) or "").strip(),
                            notes=(row.get(fields.get("notes", "")) or "").strip(),
                            favorite_profiles=(row.get(fields.get("favorite_profiles", "")) or "").strip(),
                            putty_session=(row.get(fields.get("putty_session", "")) or "").strip(),
                            last_success=(row.get(fields.get("last_success", "")) or "").strip(),
                            reachability=(row.get(fields.get("reachability", "")) or "unknown").strip() or "unknown",
                        )
                    )

        if replace:
            self.devices = imported
        else:
            existing = {(device.ip, device.port) for device in self.devices}
            self.devices.extend(device for device in imported if (device.ip, device.port) not in existing)
        if save:
            self.save()

    def add_or_update(self, device: Device) -> None:
        for index, current in enumerate(self.devices):
            if current.ip == device.ip and current.port == device.port:
                self.devices[index] = Device(
                    ip=device.ip,
                    sysname=device.sysname,
                    port=device.port,
                    source=current.source,
                    netbox_connection_id=current.netbox_connection_id,
                    netbox_device_id=current.netbox_device_id,
                    group=device.group or current.group,
                    tags=device.tags or current.tags,
                    notes=device.notes or current.notes,
                    favorite_profiles=device.favorite_profiles or current.favorite_profiles,
                    putty_session=device.putty_session or current.putty_session,
                    last_success=current.last_success,
                    reachability=current.reachability,
                )
                self.save()
                return
        self.devices.append(device)
        self.save()

    def find_by_ip(self, ip: str) -> Device | None:
        normalized_ip = ip.strip()
        return next((device for device in self.devices if device.ip == normalized_ip), None)

    def sync_netbox_devices(
        self,
        records_by_connection: dict[str, list[NetBoxDevice]],
    ) -> tuple[int, int, int]:
        """Upsert successful NetBox results and remove stale NetBox-owned rows only.

        Returns (added, updated, removed). Manual, CSV, and Quick Launch rows
        are deliberately never changed into deletion-eligible NetBox rows.
        """
        added = updated = removed = 0
        seen_by_connection: dict[str, set[str]] = {}

        for connection_id, records in records_by_connection.items():
            seen_ids = {record.device_id for record in records}
            seen_by_connection[connection_id] = seen_ids
            for record in records:
                managed = next(
                    (
                        device
                        for device in self.devices
                        if device.source == "netbox"
                        and device.netbox_connection_id == connection_id
                        and device.netbox_device_id == record.device_id
                    ),
                    None,
                )
                if managed:
                    if managed.ip != record.ip or managed.sysname != record.sysname:
                        managed.ip = record.ip
                        managed.sysname = record.sysname
                        updated += 1
                    continue

                matching_name = next(
                    (device for device in self.devices if device.sysname.casefold() == record.sysname.casefold()),
                    None,
                )
                if matching_name:
                    if matching_name.ip != record.ip:
                        matching_name.ip = record.ip
                        updated += 1
                    continue

                unnamed_ip = next(
                    (
                        device
                        for device in self.devices
                        if device.ip == record.ip
                        and (not device.sysname.strip() or device.sysname.strip() == device.ip)
                    ),
                    None,
                )
                if unnamed_ip:
                    unnamed_ip.sysname = record.sysname
                    updated += 1
                    continue

                self.devices.append(
                    Device(
                        ip=record.ip,
                        sysname=record.sysname or record.ip,
                        port="22",
                        source="netbox",
                        netbox_connection_id=connection_id,
                        netbox_device_id=record.device_id,
                    )
                )
                added += 1

        retained: list[Device] = []
        for device in self.devices:
            successful_ids = seen_by_connection.get(device.netbox_connection_id)
            if (
                device.source == "netbox"
                and successful_ids is not None
                and device.netbox_device_id not in successful_ids
            ):
                removed += 1
                continue
            retained.append(device)
        self.devices = retained
        self.save()
        return added, updated, removed

    def update_device(self, old: Device, new: Device) -> None:
        for index, current in enumerate(self.devices):
            if current.ip == old.ip and current.port == old.port:
                self.devices[index] = Device(
                    ip=new.ip,
                    sysname=new.sysname,
                    port=new.port,
                    source=current.source,
                    netbox_connection_id=current.netbox_connection_id,
                    netbox_device_id=current.netbox_device_id,
                    group=new.group or current.group,
                    tags=new.tags or current.tags,
                    notes=new.notes or current.notes,
                    favorite_profiles=new.favorite_profiles or current.favorite_profiles,
                    putty_session=new.putty_session or current.putty_session,
                    last_success=current.last_success,
                    reachability=current.reachability,
                )
                self.save()
                return
        self.devices.append(new)
        self.save()

    def delete(self, device: Device) -> None:
        self.devices = [item for item in self.devices if not (item.ip == device.ip and item.port == device.port)]
        self.save()


class NetBoxFetchError(RuntimeError):
    pass


class NetBoxClient:
    """Fetch active Huawei/Juniper access, distribution, and core devices."""

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

    def fetch(self, connection: NetBoxConnection) -> list[NetBoxDevice]:
        if not connection.url or not connection.api_key:
            raise NetBoxFetchError("URL or API key is missing.")

        try:
            netbox = pynetbox.api(connection.url.rstrip("/"), token=connection.api_key)
            session = requests.Session()
            session.verify = False
            netbox.http_session = session
            devices = netbox.dcim.devices.filter(
                status="active",
                manufacturer=NETBOX_MANUFACTURERS,
            )

            fetched: list[NetBoxDevice] = []
            for device in devices:
                device_type = getattr(device, "device_type", None)
                if str(getattr(device_type, "model", "") or "") in NETBOX_IGNORED_MODELS:
                    continue
                primary_ip = getattr(device, "primary_ip", None)
                if not primary_ip:
                    continue
                bucket = self.bucket_for(device)
                if not bucket:
                    continue
                ip = str(getattr(primary_ip, "address", "") or "").split("/", 1)[0].strip()
                if not ip:
                    continue
                sysname = str(getattr(device, "name", "") or "").strip() or ip
                device_id = str(getattr(device, "id", "") or "")
                if not device_id:
                    continue
                fetched.append(
                    NetBoxDevice(
                        connection_id=connection.id,
                        device_id=device_id,
                        sysname=sysname,
                        ip=ip,
                        bucket=bucket,
                    )
                )
            return fetched
        except Exception as exc:
            raise NetBoxFetchError(str(exc)) from exc


class ProfileDialog(Toplevel):
    def __init__(self, parent, profile: ConnectionProfile):
        super().__init__(parent)
        self.title("Connection Profile")
        self.resizable(False, False)
        self.result: ConnectionProfile | None = None
        self.profile = profile

        self.name = StringVar(value=profile.name)
        self.username = StringVar(value=profile.username)
        self.password = StringVar(value=profile.password)
        self.putty_path = StringVar(value=profile.putty_path)
        self.save_output = BooleanVar(value=profile.save_output)
        self.log_directory = StringVar(value=profile.log_directory)

        frame = Frame(self, padx=16, pady=16)
        frame.pack(fill=BOTH, expand=True)

        Label(frame, text="Profile Name").grid(row=0, column=0, sticky="w", pady=(0, 6))
        Entry(frame, textvariable=self.name, width=48).grid(row=0, column=1, pady=(0, 6))

        Label(frame, text="TACACS Username").grid(row=1, column=0, sticky="w", pady=(0, 6))
        Entry(frame, textvariable=self.username, width=48).grid(row=1, column=1, pady=(0, 6))

        Label(frame, text="TACACS Password").grid(row=2, column=0, sticky="w", pady=(0, 6))
        Entry(frame, textvariable=self.password, show="*", width=48).grid(row=2, column=1, pady=(0, 6))

        Label(frame, text="PuTTY Path").grid(row=3, column=0, sticky="w", pady=(0, 6))
        path_row = Frame(frame)
        path_row.grid(row=3, column=1, sticky="ew", pady=(0, 6))
        Entry(path_row, textvariable=self.putty_path, width=39).pack(side=LEFT)
        Button(path_row, text="Browse", command=self.browse_putty).pack(side=RIGHT, padx=(8, 0))

        Checkbutton(frame, text="Save PuTTY session output", variable=self.save_output).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(4, 6)
        )

        Label(frame, text="Log Folder").grid(row=5, column=0, sticky="w", pady=(0, 6))
        log_row = Frame(frame)
        log_row.grid(row=5, column=1, sticky="ew", pady=(0, 6))
        Entry(log_row, textvariable=self.log_directory, width=39).pack(side=LEFT)
        Button(log_row, text="Browse", command=self.browse_log_directory).pack(side=RIGHT, padx=(8, 0))

        actions = Frame(frame)
        actions.grid(row=6, column=0, columnspan=2, sticky="e", pady=(12, 0))
        Button(actions, text="Cancel", command=self.destroy).pack(side=RIGHT, padx=(8, 0))
        Button(actions, text="Save", command=self.save).pack(side=RIGHT)

        self.grab_set()
        self.transient(parent)
        self.wait_window(self)

    def browse_putty(self) -> None:
        path = filedialog.askopenfilename(
            title="Select putty.exe",
            filetypes=[("PuTTY", "putty.exe"), ("Executables", "*.exe"), ("All files", "*.*")],
        )
        if path:
            self.putty_path.set(path)

    def browse_log_directory(self) -> None:
        path = filedialog.askdirectory(title="Select session-log folder", initialdir=self.log_directory.get() or str(DEFAULT_LOGS_PATH))
        if path:
            self.log_directory.set(path)

    def save(self) -> None:
        name = self.name.get().strip()
        putty_path = self.putty_path.get().strip()
        log_directory = self.log_directory.get().strip() or str(DEFAULT_LOGS_PATH)
        if not name:
            messagebox.showerror(APP_NAME, "Profile name is required.")
            return
        if not Path(putty_path).exists():
            messagebox.showerror(APP_NAME, "PuTTY path does not exist.")
            return
        try:
            Path(log_directory).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not create the log folder:\n{exc}")
            return
        self.result = ConnectionProfile(
            id=self.profile.id,
            name=name,
            username=self.username.get().strip(),
            password=self.password.get(),
            putty_path=putty_path,
            save_output=self.save_output.get(),
            log_directory=log_directory,
        )
        self.destroy()


class ProfileManagerDialog(Toplevel):
    def __init__(self, parent, store: ProfileStore):
        super().__init__(parent)
        self.title("Manage Connection Profiles")
        self.geometry("460x330")
        self.minsize(400, 280)
        self.store = store

        frame = Frame(self, padx=14, pady=14)
        frame.pack(fill=BOTH, expand=True)
        Label(frame, text="Profiles").pack(anchor="w")
        self.listbox = Listbox(frame, exportselection=False)
        self.listbox.pack(fill=BOTH, expand=True, pady=(4, 10))

        actions = Frame(frame)
        actions.pack(fill=X)
        Button(actions, text="Add", command=self.add_profile).pack(side=LEFT)
        Button(actions, text="Edit", command=self.edit_profile).pack(side=LEFT, padx=(8, 0))
        Button(actions, text="Delete", command=self.delete_profile).pack(side=LEFT, padx=(8, 0))
        Button(actions, text="Set Default", command=self.set_default).pack(side=LEFT, padx=(8, 0))
        Button(actions, text="Close", command=self.destroy).pack(side=RIGHT)

        self.refresh()
        self.grab_set()
        self.transient(parent)
        self.wait_window(self)

    def refresh(self, selected_id: str | None = None) -> None:
        self.listbox.delete(0, END)
        for index, profile in enumerate(self.store.profiles):
            marker = " (default)" if profile.id == self.store.default_profile_id else ""
            self.listbox.insert(END, profile.name + marker)
            if profile.id == (selected_id or self.store.active_profile_id):
                self.listbox.selection_set(index)

    def selected_profile(self) -> ConnectionProfile | None:
        selection = self.listbox.curselection()
        if not selection:
            return None
        return self.store.profiles[selection[0]]

    def add_profile(self) -> None:
        dialog = ProfileDialog(self, self.store.new_profile(self.store.suggested_profile_name()))
        if dialog.result:
            try:
                self.store.save_profile(dialog.result)
                self.refresh(dialog.result.id)
            except ValueError as exc:
                messagebox.showerror(APP_NAME, str(exc))

    def edit_profile(self) -> None:
        profile = self.selected_profile()
        if not profile:
            messagebox.showwarning(APP_NAME, "Select a profile first.")
            return
        dialog = ProfileDialog(self, profile)
        if dialog.result:
            try:
                self.store.save_profile(dialog.result)
                self.refresh(dialog.result.id)
            except ValueError as exc:
                messagebox.showerror(APP_NAME, str(exc))

    def delete_profile(self) -> None:
        profile = self.selected_profile()
        if not profile:
            messagebox.showwarning(APP_NAME, "Select a profile first.")
            return
        if not messagebox.askyesno(APP_NAME, f"Delete profile '{profile.name}'?"):
            return
        try:
            self.store.delete_profile(profile.id)
            self.refresh()
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def set_default(self) -> None:
        profile = self.selected_profile()
        if not profile:
            messagebox.showwarning(APP_NAME, "Select a profile first.")
            return
        self.store.set_default(profile.id)
        self.refresh(profile.id)


class NetBoxConnectionDialog(Toplevel):
    def __init__(self, parent, connection: NetBoxConnection):
        super().__init__(parent)
        self.title("NetBox Connection")
        self.resizable(False, False)
        self.connection = connection
        self.result: NetBoxConnection | None = None

        self.name = StringVar(value=connection.name)
        self.url = StringVar(value=connection.url)
        self.api_key = StringVar(value=connection.api_key)

        frame = Frame(self, padx=16, pady=16)
        frame.pack(fill=BOTH, expand=True)
        Label(frame, text="Connection Name").grid(row=0, column=0, sticky="w", pady=(0, 6))
        Entry(frame, textvariable=self.name, width=50).grid(row=0, column=1, pady=(0, 6))
        Label(frame, text="NetBox URL").grid(row=1, column=0, sticky="w", pady=(0, 6))
        Entry(frame, textvariable=self.url, width=50).grid(row=1, column=1, pady=(0, 6))
        Label(frame, text="API Key").grid(row=2, column=0, sticky="w", pady=(0, 6))
        Entry(frame, textvariable=self.api_key, show="*", width=50).grid(row=2, column=1, pady=(0, 6))
        Label(frame, text="Internal/self-signed certificates are supported.").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )

        actions = Frame(frame)
        actions.grid(row=4, column=0, columnspan=2, sticky="e", pady=(14, 0))
        Button(actions, text="Cancel", command=self.destroy).pack(side=RIGHT, padx=(8, 0))
        Button(actions, text="Save", command=self.save).pack(side=RIGHT)
        self.grab_set()
        self.transient(parent)
        self.wait_window(self)

    def save(self) -> None:
        name = self.name.get().strip()
        url = self.url.get().strip().rstrip("/")
        api_key = self.api_key.get().strip()
        if not name or not url or not api_key:
            messagebox.showerror(APP_NAME, "Connection name, NetBox URL, and API key are required.")
            return
        if not url.lower().startswith(("http://", "https://")):
            messagebox.showerror(APP_NAME, "NetBox URL must begin with http:// or https://")
            return
        self.result = NetBoxConnection(id=self.connection.id, name=name, url=url, api_key=api_key)
        self.destroy()


class NetBoxConnectionManagerDialog(Toplevel):
    def __init__(self, parent, store: ProfileStore):
        super().__init__(parent)
        self.title("Manage NetBox Connections")
        self.geometry("500x330")
        self.minsize(420, 280)
        self.store = store

        frame = Frame(self, padx=14, pady=14)
        frame.pack(fill=BOTH, expand=True)
        Label(frame, text="NetBox Connections").pack(anchor="w")
        self.listbox = Listbox(frame, exportselection=False)
        self.listbox.pack(fill=BOTH, expand=True, pady=(4, 10))
        actions = Frame(frame)
        actions.pack(fill=X)
        Button(actions, text="Add", command=self.add_connection).pack(side=LEFT)
        Button(actions, text="Edit", command=self.edit_connection).pack(side=LEFT, padx=(8, 0))
        Button(actions, text="Delete", command=self.delete_connection).pack(side=LEFT, padx=(8, 0))
        Button(actions, text="Close", command=self.destroy).pack(side=RIGHT)

        self.refresh()
        self.grab_set()
        self.transient(parent)
        self.wait_window(self)

    def refresh(self, selected_id: str | None = None) -> None:
        self.listbox.delete(0, END)
        for index, connection in enumerate(self.store.netbox_connections):
            self.listbox.insert(END, connection.name)
            if connection.id == selected_id:
                self.listbox.selection_set(index)

    def selected_connection(self) -> NetBoxConnection | None:
        selection = self.listbox.curselection()
        if not selection:
            return None
        return self.store.netbox_connections[selection[0]]

    def add_connection(self) -> None:
        dialog = NetBoxConnectionDialog(
            self,
            self.store.new_netbox_connection(self.store.suggested_netbox_connection_name()),
        )
        if dialog.result:
            try:
                self.store.save_netbox_connection(dialog.result)
                self.refresh(dialog.result.id)
            except ValueError as exc:
                messagebox.showerror(APP_NAME, str(exc))

    def edit_connection(self) -> None:
        connection = self.selected_connection()
        if not connection:
            messagebox.showwarning(APP_NAME, "Select a NetBox connection first.")
            return
        dialog = NetBoxConnectionDialog(self, connection)
        if dialog.result:
            try:
                self.store.save_netbox_connection(dialog.result)
                self.refresh(dialog.result.id)
            except ValueError as exc:
                messagebox.showerror(APP_NAME, str(exc))

    def delete_connection(self) -> None:
        connection = self.selected_connection()
        if not connection:
            messagebox.showwarning(APP_NAME, "Select a NetBox connection first.")
            return
        if messagebox.askyesno(APP_NAME, f"Delete NetBox connection '{connection.name}'?\n\nSynced devices will stay in the inventory."):
            self.store.delete_netbox_connection(connection.id)
            self.refresh()


class DeviceFormDialog(Toplevel):
    def __init__(self, parent, title: str, device: "Device | None" = None):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result: Device | None = None

        self.sysname = StringVar(value=device.sysname if device else "")
        self.ip = StringVar(value=device.ip if device else "")
        self.port = StringVar(value=device.port if device else "")
        self.group = StringVar(value=device.group if device else "")
        self.tags = StringVar(value=device.tags if device else "")
        self.notes = StringVar(value=device.notes if device else "")
        self.putty_session = StringVar(value=device.putty_session if device else "")

        frame = Frame(self, padx=16, pady=16)
        frame.pack(fill=BOTH, expand=True)

        Label(frame, text="Sysname").grid(row=0, column=0, sticky="w", pady=(0, 6))
        Entry(frame, textvariable=self.sysname, width=40).grid(row=0, column=1, pady=(0, 6))

        Label(frame, text="IP").grid(row=1, column=0, sticky="w", pady=(0, 6))
        Entry(frame, textvariable=self.ip, width=40).grid(row=1, column=1, pady=(0, 6))

        Label(frame, text="Port").grid(row=2, column=0, sticky="w", pady=(0, 6))
        Entry(frame, textvariable=self.port, width=40).grid(row=2, column=1, pady=(0, 6))

        for row, label, variable in (
            (3, "Group", self.group), (4, "Tags", self.tags), (5, "Notes", self.notes), (6, "PuTTY session", self.putty_session)
        ):
            Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=(0, 6))
            Entry(frame, textvariable=variable, width=40).grid(row=row, column=1, pady=(0, 6))

        actions = Frame(frame)
        actions.grid(row=7, column=0, columnspan=2, sticky="e", pady=(12, 0))
        Button(actions, text="Cancel", command=self.destroy).pack(side=RIGHT, padx=(8, 0))
        Button(actions, text="Save", command=self.save).pack(side=RIGHT)

        self.grab_set()
        self.transient(parent)
        self.wait_window(self)

    def save(self) -> None:
        ip = self.ip.get().strip()
        port = self.port.get().strip()
        sysname = self.sysname.get().strip()
        if not ip or not port:
            messagebox.showerror(APP_NAME, "IP and Port are required.")
            return
        self.result = Device(ip=ip, sysname=sysname or ip, port=port, group=self.group.get().strip(), tags=self.tags.get().strip(), notes=self.notes.get().strip(), putty_session=self.putty_session.get().strip())
        self.destroy()


class ChiragPuttyLauncherApp(Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1100x700")
        self.minsize(900, 580)
        self.profile_store = ProfileStore()
        self.store = DeviceStore()
        self.theme_var = StringVar(value=self.profile_store.ui_theme)
        self.configure(bg=APP_THEMES[self.profile_store.ui_theme]["bg"])
        self.setup_styles()
        self.search_var = StringVar()
        self.protocol_filter_var = StringVar(value="All protocols")
        self.source_filter_var = StringVar(value="All sources")
        self.group_filter_var = StringVar(value="All groups")
        self.tag_filter_var = StringVar(value="All tags")
        self.sort_column = "sysname"
        self.sort_reverse = False
        self.quick_ip_var = StringVar()
        self.quick_default_var = BooleanVar(value=True)
        self.quick_name_var = StringVar()
        self.quick_port_var = StringVar(value="22")
        self.profile_var = StringVar()
        self.settings_profile_var = StringVar()
        self.settings_netbox_var = StringVar()
        self.settings_theme_var = StringVar()
        self.sync_state_var = StringVar(value="NetBox is ready")
        self.sync_in_progress = False

        self.create_menu()
        self.create_widgets()
        self.refresh_table()

    def setup_styles(self) -> None:
        """Apply the selected light or dark operations-console theme."""
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except ttk.TclError:
            pass

        palette = APP_THEMES.get(self.profile_store.ui_theme, APP_THEMES[DEFAULT_THEME])
        self.configure(bg=palette["bg"])
        style.configure("Workspace.TFrame", background=palette["bg"])
        style.configure("Header.TFrame", background=palette["header"])
        style.configure("Header.TLabel", background=palette["header"], foreground=palette["header_text"], font=("Segoe UI", 20, "bold"))
        style.configure("HeaderSub.TLabel", background=palette["header"], foreground=palette["header_muted"], font=("Segoe UI", 10))
        style.configure("Header.TButton", background=palette["header"], foreground=palette["header_text"], padding=(12, 7), font=("Segoe UI", 9, "bold"))
        style.map("Header.TButton", background=[("active", palette["surface_2"])])
        style.configure("HeaderDanger.TButton", background="#a63c47", foreground="#ffffff", padding=(12, 7), font=("Segoe UI", 9, "bold"))
        style.map("HeaderDanger.TButton", background=[("active", "#7e2931")])
        style.configure("App.TLabel", background=palette["bg"], foreground=palette["text"], font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=palette["bg"], foreground=palette["muted"], font=("Segoe UI", 9))
        style.configure("Card.TLabel", background=palette["surface"], foreground=palette["text"], font=("Segoe UI", 10))
        style.configure("Card.TLabelframe", background=palette["surface"], bordercolor=palette["line"], relief="solid")
        style.configure("Card.TLabelframe.Label", background=palette["surface"], foreground=palette["text"], font=("Segoe UI", 10, "bold"))
        style.configure("CardBody.TFrame", background=palette["surface"])
        style.configure("Toolbar.TButton", padding=(12, 7), font=("Segoe UI", 9, "bold"))
        style.configure("Accent.TButton", background=palette["accent"], foreground="#ffffff", padding=(14, 8), font=("Segoe UI", 9, "bold"))
        style.map("Accent.TButton", background=[("active", palette["accent_active"]), ("disabled", palette["line"])])
        style.configure("Danger.TButton", background="#b74343", foreground="#ffffff", padding=(12, 7), font=("Segoe UI", 9, "bold"))
        style.map("Danger.TButton", background=[("active", "#8e3030"), ("disabled", palette["line"])])
        style.configure("Treeview", background=palette["surface"], fieldbackground=palette["surface"], foreground=palette["text"], rowheight=35, borderwidth=0, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", background=palette["surface_2"], foreground=palette["muted"], relief="flat", padding=(10, 9), font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", palette["selection"])], foreground=[("selected", palette["header_text"])])
        style.configure("App.TNotebook", background=palette["bg"], borderwidth=0)
        style.configure("App.TNotebook.Tab", background=palette["surface_2"], foreground=palette["muted"], padding=(18, 9), font=("Segoe UI", 9, "bold"))
        style.map("App.TNotebook.Tab", background=[("selected", palette["surface"])], foreground=[("selected", palette["text"])])
        style.configure("Horizontal.TProgressbar", troughcolor=palette["surface_2"], background=palette["accent"], bordercolor=palette["surface_2"], lightcolor=palette["accent"], darkcolor=palette["accent"])

    def apply_selected_theme(self, _event=None) -> None:
        selected = self.theme_var.get()
        self.profile_store.set_ui_theme(selected)
        self.theme_var.set(self.profile_store.ui_theme)
        self.setup_styles()
        self.refresh_settings_summary()

    def check_for_updates(self) -> None:
        self.status.config(text="Checking GitHub Releases…")
        threading.Thread(target=self._check_for_updates_worker, daemon=True).start()

    def _check_for_updates_worker(self) -> None:
        try:
            response = requests.get(f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest", timeout=10)
            if response.status_code == 404:
                message = "No GitHub Release has been published yet."
            else:
                response.raise_for_status()
                release = response.json()
                version = str(release.get("tag_name", "")).lstrip("v")
                url = release.get("html_url", f"https://github.com/{GITHUB_REPOSITORY}/releases")
                message = f"Version {version} is available: {url}" if version and version != APP_VERSION else f"You are up to date ({APP_VERSION})."
        except requests.RequestException as exc:
            message = f"Could not check for updates: {exc}"
        self.after(0, lambda: (self.status.config(text=message), messagebox.showinfo(APP_NAME, message)))

    def create_menu(self) -> None:
        menu_bar = Menu(self)
        settings_menu = Menu(menu_bar, tearoff=0)
        settings_menu.add_command(label="Manage Connection Profiles", command=self.open_profile_manager)
        settings_menu.add_command(label="Manage NetBox Connections", command=self.open_netbox_connection_manager)
        menu_bar.add_cascade(label="Settings", menu=settings_menu)

        devices_menu = Menu(menu_bar, tearoff=0)
        devices_menu.add_command(label="Import CSV", command=self.import_csv)
        devices_menu.add_command(label="Export Current Devices", command=self.export_csv)
        devices_menu.add_separator()
        devices_menu.add_command(label="Close All PuTTY Sessions", command=self.close_all_putty_sessions)
        menu_bar.add_cascade(label="Devices", menu=devices_menu)
        self.config(menu=menu_bar)

    def create_widgets(self) -> None:
        header = ttk.Frame(self, padding=(26, 18), style="Header.TFrame")
        header.pack(fill=X)
        header_actions = ttk.Frame(header, style="Header.TFrame")
        header_actions.pack(side=RIGHT, anchor="n")
        ttk.Button(
            header_actions,
            text="Close all PuTTY",
            command=self.close_all_putty_sessions,
            style="HeaderDanger.TButton",
        ).pack(side=RIGHT, padx=(0, 8))
        ttk.Label(header, text="Network inventory", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Launch trusted sessions and keep your device list aligned with NetBox.",
            style="HeaderSub.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        root = ttk.Frame(self, padding=22, style="Workspace.TFrame")
        root.pack(fill=BOTH, expand=True)

        profile_bar = ttk.LabelFrame(root, text="  Connection profile  ", padding=(14, 4), style="Card.TLabelframe")
        profile_bar.pack(fill=X, pady=(0, 12))
        ttk.Label(profile_bar, text="Use profile", style="Card.TLabel").pack(side=LEFT, padx=(2, 8), pady=8)
        self.profile_selector = ttk.Combobox(profile_bar, textvariable=self.profile_var, state="readonly", width=34)
        self.profile_selector.pack(side=LEFT, pady=8)
        self.profile_selector.bind("<<ComboboxSelected>>", self.select_profile)
        ttk.Button(profile_bar, text="Add profile", command=self.add_profile, style="Toolbar.TButton").pack(side=LEFT, padx=(10, 0), pady=5)
        ttk.Button(profile_bar, text="Manage profiles", command=self.open_profile_manager, style="Toolbar.TButton").pack(side=LEFT, padx=(8, 0), pady=5)

        quick = ttk.LabelFrame(root, text="  Quick launch  ", padding=(14, 4), style="Card.TLabelframe")
        quick.pack(fill=X, pady=(0, 12))
        quick_top = ttk.Frame(quick, style="CardBody.TFrame")
        quick_top.pack(fill=X)
        ttk.Label(quick_top, text="IP address", style="Card.TLabel").pack(side=LEFT, padx=(2, 8), pady=9)
        quick_entry = ttk.Entry(quick_top, textvariable=self.quick_ip_var, width=36)
        quick_entry.pack(side=LEFT, padx=(0, 9), pady=9)
        quick_entry.bind("<Return>", self.quick_launch)
        ttk.Checkbutton(
            quick_top,
            text="Default (sysname = IP, port = 22)",
            variable=self.quick_default_var,
            command=self.toggle_quick_launch_fields,
        ).pack(side=LEFT, padx=(0, 10), pady=9)
        ttk.Button(quick_top, text="Launch", command=self.quick_launch, style="Accent.TButton").pack(side=LEFT, pady=6)

        self.quick_details = ttk.Frame(quick, style="CardBody.TFrame")
        ttk.Label(self.quick_details, text="Sysname", style="Card.TLabel").pack(side=LEFT, padx=(2, 8), pady=(0, 9))
        quick_name = ttk.Entry(self.quick_details, textvariable=self.quick_name_var, width=26)
        quick_name.pack(side=LEFT, padx=(0, 12), pady=(0, 10))
        ttk.Label(self.quick_details, text="Port", style="Card.TLabel").pack(side=LEFT, padx=(0, 8), pady=(0, 9))
        quick_port = ttk.Entry(self.quick_details, textvariable=self.quick_port_var, width=10)
        quick_port.pack(side=LEFT, padx=(0, 8), pady=(0, 10))
        quick_name.bind("<Return>", self.quick_launch)
        quick_port.bind("<Return>", self.quick_launch)

        workspace_tabs = ttk.Notebook(root, style="App.TNotebook")
        workspace_tabs.pack(fill=BOTH, expand=True)
        inventory_tab = ttk.Frame(workspace_tabs, padding=(0, 14, 0, 0), style="Workspace.TFrame")
        recent_tab = ttk.Frame(workspace_tabs, padding=(0, 16, 0, 0), style="Workspace.TFrame")
        settings_tab = ttk.Frame(workspace_tabs, padding=(0, 16, 0, 0), style="Workspace.TFrame")
        workspace_tabs.add(inventory_tab, text="Devices")
        workspace_tabs.add(recent_tab, text="Recently opened")
        workspace_tabs.add(settings_tab, text="Settings")

        toolbar = ttk.Frame(inventory_tab, style="Workspace.TFrame")
        toolbar.pack(fill=X, pady=(0, 10))
        ttk.Label(toolbar, text="Search devices", style="App.TLabel").pack(side=LEFT, padx=(0, 8))
        search = ttk.Entry(toolbar, textvariable=self.search_var, width=42)
        search.pack(side=LEFT)
        search.bind("<KeyRelease>", lambda _event: self.refresh_table())
        for variable, values, width in (
            (self.protocol_filter_var, ("All protocols", "SSH", "Telnet"), 13),
            (self.source_filter_var, ("All sources", "manual", "netbox"), 12),
            (self.group_filter_var, ("All groups",), 14),
            (self.tag_filter_var, ("All tags",), 14),
        ):
            selector = ttk.Combobox(toolbar, textvariable=variable, values=values, state="readonly", width=width)
            selector.pack(side=LEFT, padx=(8, 0))
            selector.bind("<<ComboboxSelected>>", lambda _event: self.refresh_table())
        ttk.Button(toolbar, text="Import CSV", command=self.import_csv, style="Toolbar.TButton").pack(side=RIGHT)
        self.sync_button = ttk.Button(toolbar, text="Sync NetBox", command=self.sync_netbox, style="Accent.TButton", width=16)
        self.sync_button.pack(side=RIGHT, padx=(0, 8))
        ttk.Button(toolbar, text="Add device", command=self.add_device_dialog, style="Toolbar.TButton").pack(side=RIGHT, padx=(0, 8))
        self.sync_progress = ttk.Progressbar(toolbar, mode="indeterminate", length=112, style="Horizontal.TProgressbar")
        self.sync_progress.pack(side=RIGHT, padx=(0, 10))
        ttk.Label(toolbar, textvariable=self.sync_state_var, style="Muted.TLabel").pack(side=RIGHT, padx=(0, 10))

        columns = ("sysname", "ip", "port", "protocol")
        self.tree = ttk.Treeview(inventory_tab, columns=columns, show="headings", selectmode="extended")
        self.tree.heading("sysname", text="Sysname", command=lambda: self.set_sort("sysname"))
        self.tree.heading("ip", text="IP", command=lambda: self.set_sort("ip"))
        self.tree.heading("port", text="Port", command=lambda: self.set_sort("port"))
        self.tree.heading("protocol", text="Protocol", command=lambda: self.set_sort("protocol"))
        self.tree.column("sysname", width=300)
        self.tree.column("ip", width=180)
        self.tree.column("port", width=90)
        self.tree.column("protocol", width=100)
        self.tree.pack(fill=BOTH, expand=True)
        self.tree.bind("<Double-1>", lambda _event: self.launch_selected())

        self.row_menu = Menu(self, tearoff=0)
        self.row_menu.add_command(label="Edit", command=self.edit_selected)
        self.row_menu.add_command(label="Delete", command=self.delete_selected)
        self.tree.bind("<Button-3>", self.show_row_menu)

        actions = ttk.Frame(inventory_tab, style="Workspace.TFrame")
        actions.pack(fill=X, pady=(10, 0))
        ttk.Button(actions, text="Launch PuTTY", command=self.launch_selected, style="Accent.TButton").pack(side=LEFT)
        ttk.Button(actions, text="Launch selected", command=self.launch_all_selected, style="Toolbar.TButton").pack(side=LEFT, padx=(8, 0))
        ttk.Button(actions, text="Toggle favorite", command=self.toggle_selected_favorite, style="Toolbar.TButton").pack(side=LEFT, padx=(8, 0))
        ttk.Button(actions, text="Delete device", command=self.delete_selected, style="Toolbar.TButton").pack(side=LEFT, padx=(8, 0))
        ttk.Button(actions, text="Close all PuTTY", command=self.close_all_putty_sessions, style="Danger.TButton").pack(side=LEFT, padx=(8, 0))
        self.status = ttk.Label(actions, text=self.status_text(), anchor="e", style="Muted.TLabel")
        self.status.pack(side=RIGHT)

        recent_heading = ttk.Frame(recent_tab, style="Workspace.TFrame")
        recent_heading.pack(fill=X, pady=(0, 10))
        ttk.Label(recent_heading, text="Continue a recent session", style="App.TLabel", font=("Segoe UI", 12, "bold")).pack(side=LEFT)
        ttk.Label(
            recent_heading,
            text="Double-click a device or select it and choose Launch.",
            style="Muted.TLabel",
        ).pack(side=LEFT, padx=(12, 0), pady=(2, 0))

        recent_columns = ("sysname", "ip", "port", "protocol", "last_opened")
        self.recent_tree = ttk.Treeview(recent_tab, columns=recent_columns, show="headings", selectmode="browse")
        self.recent_tree.heading("sysname", text="Sysname")
        self.recent_tree.heading("ip", text="IP")
        self.recent_tree.heading("port", text="Port")
        self.recent_tree.heading("protocol", text="Protocol")
        self.recent_tree.heading("last_opened", text="Last opened")
        self.recent_tree.column("sysname", width=300)
        self.recent_tree.column("ip", width=180)
        self.recent_tree.column("port", width=90)
        self.recent_tree.column("protocol", width=100)
        self.recent_tree.column("last_opened", width=170)
        self.recent_tree.pack(fill=BOTH, expand=True)
        self.recent_tree.bind("<Double-1>", lambda _event: self.launch_recent_device())

        recent_actions = ttk.Frame(recent_tab, style="Workspace.TFrame")
        recent_actions.pack(fill=X, pady=(10, 0))
        ttk.Button(recent_actions, text="Launch selected device", command=self.launch_recent_device, style="Accent.TButton").pack(side=LEFT)
        self.recent_empty_label = ttk.Label(recent_actions, text="", style="Muted.TLabel")
        self.recent_empty_label.pack(side=RIGHT)

        settings_heading = ttk.Frame(settings_tab, style="Workspace.TFrame")
        settings_heading.pack(fill=X, pady=(0, 10))
        ttk.Label(settings_heading, text="Workspace settings", style="App.TLabel", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(
            settings_heading,
            text="Manage the profile, inventory source, and local display preferences for this launcher.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        profile_settings = ttk.LabelFrame(settings_tab, text="  Connection profile  ", padding=(14, 9), style="Card.TLabelframe")
        profile_settings.pack(fill=X, pady=(0, 10))
        ttk.Label(profile_settings, textvariable=self.settings_profile_var, style="Card.TLabel").pack(side=LEFT)
        ttk.Button(profile_settings, text="Manage profiles", command=self.open_profile_manager, style="Toolbar.TButton").pack(side=RIGHT)
        ttk.Button(profile_settings, text="Restore backup", command=self.restore_settings_backup, style="Toolbar.TButton").pack(side=RIGHT, padx=(0, 8))
        ttk.Button(profile_settings, text="Backup settings", command=self.backup_settings, style="Toolbar.TButton").pack(side=RIGHT, padx=(0, 8))

        netbox_settings = ttk.LabelFrame(settings_tab, text="  NetBox inventory  ", padding=(14, 9), style="Card.TLabelframe")
        netbox_settings.pack(fill=X, pady=(0, 10))
        ttk.Label(netbox_settings, textvariable=self.settings_netbox_var, style="Card.TLabel").pack(side=LEFT)
        ttk.Button(netbox_settings, text="Manage NetBox", command=self.open_netbox_connection_manager, style="Toolbar.TButton").pack(side=RIGHT)

        data_settings = ttk.LabelFrame(settings_tab, text="  Device data  ", padding=(14, 9), style="Card.TLabelframe")
        data_settings.pack(fill=X, pady=(0, 10))
        ttk.Label(data_settings, text="Import or export the local device inventory as CSV.", style="Card.TLabel").pack(side=LEFT)
        ttk.Button(data_settings, text="Export CSV", command=self.export_csv, style="Toolbar.TButton").pack(side=RIGHT)
        ttk.Button(data_settings, text="Import CSV", command=self.import_csv, style="Toolbar.TButton").pack(side=RIGHT, padx=(0, 8))

        appearance_settings = ttk.LabelFrame(settings_tab, text="  Themes  ", padding=(14, 9), style="Card.TLabelframe")
        appearance_settings.pack(fill=X)
        ttk.Label(appearance_settings, textvariable=self.settings_theme_var, style="Card.TLabel").pack(side=LEFT)
        self.theme_selector = ttk.Combobox(
            appearance_settings,
            textvariable=self.theme_var,
            values=list(APP_THEMES),
            state="readonly",
            width=16,
        )
        self.theme_selector.pack(side=RIGHT)
        self.theme_selector.bind("<<ComboboxSelected>>", self.apply_selected_theme)
        ttk.Button(appearance_settings, text="Check updates", command=self.check_for_updates, style="Toolbar.TButton").pack(side=RIGHT, padx=(0, 8))

        self.refresh_profiles()

    def toggle_quick_launch_fields(self) -> None:
        if self.quick_default_var.get():
            self.quick_details.pack_forget()
            self.quick_name_var.set("")
            self.quick_port_var.set("22")
        else:
            self.quick_details.pack(fill=X)

    def status_text(self) -> str:
        return f"Devices: {len(self.store.devices)} | Profile: {self.current_profile().name}"

    def refresh_profiles(self, selected_id: str | None = None) -> None:
        active = self.profile_store.get(selected_id or self.profile_store.active_profile_id)
        if not active:
            active = self.profile_store.active_profile()
        self.profile_selector["values"] = [profile.name for profile in self.profile_store.profiles]
        self.profile_var.set(active.name)
        self.profile_store.set_active(active.id)
        self.refresh_settings_summary()
        if hasattr(self, "status"):
            self.status.config(text=self.status_text())

    def refresh_settings_summary(self) -> None:
        if not hasattr(self, "settings_profile_var"):
            return
        profile = self.current_profile()
        self.settings_profile_var.set(f"Active profile: {profile.name}")
        count = len(self.profile_store.netbox_connections)
        self.settings_netbox_var.set(
            "No NetBox connections configured." if not count else f"{count} NetBox connection{'s' if count != 1 else ''} configured."
        )
        self.theme_var.set(self.profile_store.ui_theme)
        self.settings_theme_var.set(f"Current theme: {self.profile_store.ui_theme}")

    def current_profile(self) -> ConnectionProfile:
        return self.profile_store.active_profile()

    def select_profile(self, _event=None) -> None:
        selected = next(
            (profile for profile in self.profile_store.profiles if profile.name == self.profile_var.get()),
            None,
        )
        if selected:
            self.profile_store.set_active(selected.id)
            self.status.config(text=self.status_text())

    def add_profile(self) -> None:
        dialog = ProfileDialog(self, self.profile_store.new_profile(self.profile_store.suggested_profile_name()))
        if dialog.result:
            try:
                self.profile_store.save_profile(dialog.result)
                self.refresh_profiles(dialog.result.id)
            except ValueError as exc:
                messagebox.showerror(APP_NAME, str(exc))

    def open_profile_manager(self) -> None:
        ProfileManagerDialog(self, self.profile_store)
        self.refresh_profiles()

    def backup_settings(self) -> None:
        path = filedialog.asksaveasfilename(title="Back up launcher settings", defaultextension=".json", filetypes=[("JSON files", "*.json")])
        if not path:
            return
        try:
            self.profile_store.export_backup(Path(path))
            messagebox.showinfo(APP_NAME, "Settings backup created.")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not create backup:\n{exc}")

    def restore_settings_backup(self) -> None:
        path = filedialog.askopenfilename(title="Restore launcher settings", filetypes=[("JSON files", "*.json")])
        if not path or not messagebox.askyesno(APP_NAME, "Replace current launcher settings with this backup?"):
            return
        try:
            self.profile_store.restore_backup(Path(path))
            self.theme_var.set(self.profile_store.ui_theme)
            self.setup_styles()
            self.refresh_profiles()
            messagebox.showinfo(APP_NAME, "Settings restored.")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror(APP_NAME, f"Could not restore backup:\n{exc}")

    def open_netbox_connection_manager(self) -> None:
        NetBoxConnectionManagerDialog(self, self.profile_store)
        self.refresh_settings_summary()

    def set_sync_error(self, has_error: bool) -> None:
        self.sync_button.config(text="Sync NetBox !" if has_error else "Sync NetBox")

    def sync_netbox(self) -> None:
        if self.sync_in_progress:
            return
        connections = self.profile_store.netbox_connections
        if not connections:
            messagebox.showinfo(APP_NAME, "Add a NetBox URL and API key under Settings before syncing.")
            self.open_netbox_connection_manager()
            return

        self.sync_in_progress = True
        self.sync_button.config(state="disabled", text="Syncing…")
        self.sync_state_var.set("Syncing NetBox")
        self.sync_progress.start(12)
        self.status.config(text="Synchronizing NetBox devices...")
        threading.Thread(target=self.sync_netbox_worker, args=(list(connections),), daemon=True).start()

    def sync_netbox_worker(self, connections: list[NetBoxConnection]) -> None:
        """Fetch remote inventory without blocking the Tk event loop."""
        client = NetBoxClient()
        fetched: dict[str, list[NetBoxDevice]] = {}
        errors: list[str] = []

        for connection in connections:
            try:
                fetched[connection.id] = client.fetch(connection)
            except NetBoxFetchError as exc:
                errors.append(f"{connection.name}: {exc}")

        added = updated = removed = 0
        if fetched:
            try:
                added, updated, removed = self.store.sync_netbox_devices(fetched)
            except Exception as exc:
                errors.append(f"Could not save synced devices: {exc}")

        self.after(0, self.finish_netbox_sync, added, updated, removed, errors)

    def finish_netbox_sync(self, added: int, updated: int, removed: int, errors: list[str]) -> None:
        self.sync_in_progress = False
        self.sync_progress.stop()
        self.sync_button.config(state="normal")
        self.set_sync_error(bool(errors))
        summary = f"NetBox sync: {added} added, {updated} updated, {removed} removed."
        self.refresh_table()
        if errors:
            self.sync_state_var.set(f"{len(errors)} connection error(s)")
            self.status.config(text=summary + f" {len(errors)} connection error(s).")
            messagebox.showwarning(
                APP_NAME,
                summary + "\n\nFailed connections were not changed or cleaned up:\n" + "\n".join(errors),
            )
        else:
            self.sync_state_var.set("Sync complete")
            self.status.config(text=summary)
            messagebox.showinfo(APP_NAME, summary)

    def edit_current_profile(self) -> None:
        profile = self.current_profile()
        dialog = ProfileDialog(self, profile)
        if dialog.result:
            try:
                self.profile_store.save_profile(dialog.result)
                self.refresh_profiles(dialog.result.id)
            except ValueError as exc:
                messagebox.showerror(APP_NAME, str(exc))

    def ensure_current_profile(self, require_credentials: bool) -> ConnectionProfile | None:
        profile = self.current_profile()
        complete = Path(profile.putty_path).exists()
        if require_credentials:
            complete = complete and bool(profile.username and profile.password)
        if not complete:
            self.edit_current_profile()
            profile = self.current_profile()
            complete = Path(profile.putty_path).exists()
            if require_credentials:
                complete = complete and bool(profile.username and profile.password)
        return profile if complete else None

    def filtered_devices(self) -> list[Device]:
        query = self.search_var.get().strip().lower()
        protocol = self.protocol_filter_var.get().lower()
        source = self.source_filter_var.get().lower()
        group = self.group_filter_var.get()
        tag = self.tag_filter_var.get()
        devices = [
            device for device in self.store.devices
            if (not query or any(query in value.lower() for value in (device.ip, device.sysname, device.port, device.group, device.tags, device.notes)))
            and (protocol == "all protocols" or device.protocol == protocol)
            and (source == "all sources" or device.source == source)
            and (group == "All groups" or device.group == group)
            and (tag == "All tags" or tag in {item.strip() for item in device.tags.split(",")})
        ]
        return sorted(devices, key=lambda device: str(getattr(device, self.sort_column)).lower(), reverse=self.sort_reverse)

    def set_sort(self, column: str) -> None:
        self.sort_reverse = not self.sort_reverse if self.sort_column == column else False
        self.sort_column = column
        self.refresh_table()

    def refresh_table(self) -> None:
        groups = sorted({device.group for device in self.store.devices if device.group})
        tags = sorted({tag.strip() for device in self.store.devices for tag in device.tags.split(",") if tag.strip()})
        self.group_filter_var.set(self.group_filter_var.get() if self.group_filter_var.get() in groups else "All groups")
        self.tag_filter_var.set(self.tag_filter_var.get() if self.tag_filter_var.get() in tags else "All tags")
        for widget in self.tree.master.winfo_children():
            if isinstance(widget, ttk.Frame):
                for child in widget.winfo_children():
                    if isinstance(child, ttk.Combobox):
                        if str(child.cget("textvariable")) == str(self.group_filter_var): child["values"] = ("All groups", *groups)
                        if str(child.cget("textvariable")) == str(self.tag_filter_var): child["values"] = ("All tags", *tags)
        self.tree.delete(*self.tree.get_children())
        for index, device in enumerate(self.filtered_devices()):
            self.tree.insert("", END, iid=str(index), values=(device.sysname, device.ip, device.port, device.protocol.upper()))
        self.refresh_recent_table()
        self.status.config(text=self.status_text()) if hasattr(self, "status") else None

    def refresh_recent_table(self) -> None:
        if not hasattr(self, "recent_tree"):
            return
        self.recent_tree.delete(*self.recent_tree.get_children())
        for index, record in enumerate(self.profile_store.recent_devices):
            device = Device(
                ip=str(record.get("ip", "")),
                sysname=str(record.get("sysname") or record.get("ip", "")),
                port=str(record.get("port", "22")),
            )
            timestamp = str(record.get("last_opened_at", ""))
            try:
                timestamp = datetime.fromisoformat(timestamp).strftime("%d %b %Y, %H:%M")
            except ValueError:
                timestamp = "Previously"
            self.recent_tree.insert(
                "",
                END,
                iid=str(index),
                values=(device.sysname, device.ip, device.port, device.protocol.upper(), timestamp),
            )
        message = "No devices opened yet. Launch a device to keep it here." if not self.profile_store.recent_devices else ""
        self.recent_empty_label.config(text=message)

    def selected_device(self) -> Device | None:
        devices = self.selected_devices()
        return devices[0] if devices else None

    def selected_devices(self) -> list[Device]:
        selected = self.tree.selection()
        devices: list[Device] = []
        for item in selected:
            values = self.tree.item(item, "values")
            match = next((device for device in self.store.devices if device.ip == values[1] and device.port == values[2]), None)
            if match:
                devices.append(match)
        return devices

    def selected_recent_device(self) -> Device | None:
        selected = self.recent_tree.selection()
        if not selected:
            return None
        values = self.recent_tree.item(selected[0], "values")
        return Device(ip=values[1], sysname=values[0], port=values[2])

    def active_putty_pids(self) -> list[str]:
        """Return the process IDs for every interactive PuTTY session."""
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq putty.exe", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not check active PuTTY sessions:\n{exc}")
            return []

        sessions: list[str] = []
        for row in csv.reader(result.stdout.splitlines()):
            if len(row) >= 2 and row[0].casefold() == "putty.exe":
                sessions.append(row[1])
        return sessions

    def close_all_putty_sessions(self) -> None:
        pids = self.active_putty_pids()
        if not pids:
            messagebox.showinfo(APP_NAME, "No open PuTTY sessions were found.")
            return

        count = len(pids)
        if not messagebox.askyesno(
            APP_NAME,
            f"Close all {count} open PuTTY session{'s' if count != 1 else ''}?\n\n"
            "This ends every running PuTTY window immediately. Unsaved terminal work will be lost.",
            icon="warning",
        ):
            return

        closed = 0
        failures: list[str] = []
        for pid in pids:
            try:
                result = subprocess.run(
                    ["taskkill", "/PID", pid, "/F"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0:
                    closed += 1
                else:
                    failures.append(pid)
            except OSError:
                failures.append(pid)

        if failures:
            self.status.config(text=f"Closed {closed} PuTTY session(s); could not close {len(failures)}.")
            messagebox.showwarning(APP_NAME, f"Closed {closed} session(s). Could not close: {', '.join(failures)}.")
        else:
            self.status.config(text=f"Closed {closed} PuTTY session(s).")

    def launch_device(self, device: Device) -> None:
        profile = self.ensure_current_profile(require_credentials=device.protocol == "ssh")
        if not profile:
            return

        if device.putty_session:
            command = [profile.putty_path, "-load", device.putty_session]
        elif device.protocol == "telnet":
            # Port 23 -> plain Telnet. Auto login is not sent because it is
            # unreliable against Telnet sessions; the user logs in manually.
            command = [profile.putty_path, "-telnet", device.ip, "-P", str(device.port)]
        else:
            command = [
                profile.putty_path,
                "-ssh",
                device.ip,
                "-P",
                str(device.port),
                "-l",
                profile.username,
                "-pw",
                profile.password,
            ]

        log_path = self.session_log_path(profile, device)
        if profile.save_output and not log_path:
            return
        if log_path:
            command.extend(["-sessionlog", str(log_path), "-logoverwrite"])

        try:
            subprocess.Popen(command, shell=False)
            device.last_success = datetime.now().isoformat(timespec="seconds")
            self.store.save()
            self.profile_store.record_recent_device(device)
            self.refresh_recent_table()
            suffix = f" | Log: {log_path.name}" if log_path else ""
            self.status.config(text=f"Launched {device.sysname} via {profile.name}{suffix}")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not launch PuTTY:\n{exc}")

    def session_log_path(self, profile: ConnectionProfile, device: Device) -> Path | None:
        if not profile.save_output:
            return None
        try:
            directory = Path(profile.log_directory)
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not create the session-log folder:\n{exc}")
            return None

        def safe_filename(value: str) -> str:
            return re.sub(r'[^A-Za-z0-9._-]+', "_", value).strip("._") or "device"

        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        filename = "_".join(
            [
                timestamp,
                safe_filename(profile.name),
                safe_filename(device.sysname),
                safe_filename(device.ip),
                safe_filename(device.port),
            ]
        )
        return directory / f"{filename}.log"

    def launch_selected(self) -> None:
        device = self.selected_device()
        if not device:
            messagebox.showwarning(APP_NAME, "Select a device first.")
            return
        self.launch_device(device)

    def launch_all_selected(self) -> None:
        devices = self.selected_devices()
        if not devices:
            messagebox.showwarning(APP_NAME, "Select one or more devices first.")
            return
        for device in devices:
            self.launch_device(device)

    def toggle_selected_favorite(self) -> None:
        device = self.selected_device()
        if not device:
            messagebox.showwarning(APP_NAME, "Select a device first.")
            return
        favorite_ids = {item for item in device.favorite_profiles.split(",") if item}
        profile_id = self.current_profile().id
        if profile_id in favorite_ids:
            favorite_ids.remove(profile_id)
        else:
            favorite_ids.add(profile_id)
        device.favorite_profiles = ",".join(sorted(favorite_ids))
        self.store.save()
        self.status.config(text=f"{'Added' if profile_id in favorite_ids else 'Removed'} {device.sysname} {'to' if profile_id in favorite_ids else 'from'} favorites.")

    def launch_recent_device(self) -> None:
        device = self.selected_recent_device()
        if not device:
            messagebox.showwarning(APP_NAME, "Select a recent device first.")
            return
        self.launch_device(device)

    def quick_launch(self, event=None) -> None:
        ip = self.quick_ip_var.get().strip()
        if not ip:
            messagebox.showwarning(APP_NAME, "Enter an IP address first.")
            return
        existing = self.store.find_by_ip(ip)
        if existing:
            self.quick_ip_var.set("")
            self.refresh_table()
            self.launch_device(existing)
            return
        if self.quick_default_var.get():
            device = Device(ip=ip, sysname=ip, port="22")
        else:
            port = self.quick_port_var.get().strip()
            sysname = self.quick_name_var.get().strip()
            if not port:
                messagebox.showwarning(APP_NAME, "Enter a port for this device.")
                return
            device = Device(ip=ip, sysname=sysname or ip, port=port)
        self.store.add_or_update(device)
        self.quick_ip_var.set("")
        if not self.quick_default_var.get():
            self.quick_name_var.set("")
            self.quick_port_var.set("")
        self.refresh_table()
        self.launch_device(device)

    def add_device_dialog(self) -> None:
        dialog = DeviceFormDialog(self, "Add Device")
        if dialog.result:
            self.store.add_or_update(dialog.result)
            self.refresh_table()

    def show_row_menu(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        self.row_menu.tk_popup(event.x_root, event.y_root)

    def edit_selected(self) -> None:
        original = self.selected_device()
        if not original:
            messagebox.showwarning(APP_NAME, "Select a device first.")
            return
        dialog = DeviceFormDialog(self, "Edit Device", original)
        if dialog.result:
            self.store.update_device(original, dialog.result)
            self.refresh_table()

    def delete_selected(self) -> None:
        device = self.selected_device()
        if not device:
            messagebox.showwarning(APP_NAME, "Select a device first.")
            return
        if messagebox.askyesno(APP_NAME, f"Delete {device.sysname}?"):
            self.store.delete(device)
            self.refresh_table()

    def import_csv(self) -> None:
        path = filedialog.askopenfilename(
            title="Import device CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            valid, errors = self.store.preview_csv(Path(path))
            if errors:
                messagebox.showerror(APP_NAME, "Import preview found errors:\n\n" + "\n".join(errors[:10]))
                return
            if not valid:
                messagebox.showwarning(APP_NAME, "Import preview found no valid device rows.")
                return
            if not messagebox.askyesno(APP_NAME, f"Import {valid} validated device{'s' if valid != 1 else ''}?"):
                return
            self.store.import_csv(Path(path), replace=False)
            self.refresh_table()
            messagebox.showinfo(APP_NAME, "CSV imported.")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not import CSV:\n{exc}")

    def export_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Export device CSV",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        with Path(path).open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=["ip", "sysname", "port"])
            writer.writeheader()
            for device in self.store.devices:
                writer.writerow(asdict(device))
        messagebox.showinfo(APP_NAME, "CSV exported.")


if __name__ == "__main__":
    app = ChiragPuttyLauncherApp()
    app.mainloop()
