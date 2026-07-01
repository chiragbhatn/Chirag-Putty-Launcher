import base64
import csv
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import (
    BOTH,
    END,
    LEFT,
    RIGHT,
    X,
    Button,
    Entry,
    Frame,
    Label,
    LabelFrame,
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
DEFAULT_PUTTY_PATH = r"C:\Program Files\PuTTY\putty.exe"


def app_data_dir() -> Path:
    base = os.getenv("APPDATA") or str(Path.home())
    path = Path(base) / "ChiragPuttyLauncher"
    path.mkdir(parents=True, exist_ok=True)
    return path


CONFIG_PATH = app_data_dir() / "config.json"
DEVICES_PATH = app_data_dir() / "devices.csv"


@dataclass
class Device:
    ip: str
    sysname: str
    port: str

    @property
    def protocol(self) -> str:
        return "telnet" if self.port.strip() == "23" else "ssh"


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


class SettingsStore:
    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path
        self.data = {
            "username": "",
            "password": "",
            "putty_path": DEFAULT_PUTTY_PATH,
        }
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.data["username"] = raw.get("username", "")
            self.data["password"] = unprotect_secret(raw.get("password", ""))
            self.data["putty_path"] = raw.get("putty_path", DEFAULT_PUTTY_PATH)
        except Exception:
            pass

    def save(self, username: str, password: str, putty_path: str) -> None:
        self.data = {"username": username, "password": password, "putty_path": putty_path}
        raw = {
            "username": username,
            "password": protect_secret(password),
            "putty_path": putty_path,
        }
        self.path.write_text(json.dumps(raw, indent=2), encoding="utf-8")


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
            writer = csv.DictWriter(csv_file, fieldnames=["ip", "sysname", "port"])
            writer.writeheader()
            for device in self.devices:
                writer.writerow(asdict(device))

    def import_csv(self, path: Path, replace: bool = False, save: bool = True) -> None:
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
                    imported.append(Device(ip=ip, sysname=sysname or ip, port=port))

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
                self.devices[index] = device
                self.save()
                return
        self.devices.append(device)
        self.save()

    def update_device(self, old: Device, new: Device) -> None:
        for index, current in enumerate(self.devices):
            if current.ip == old.ip and current.port == old.port:
                self.devices[index] = new
                self.save()
                return
        self.devices.append(new)
        self.save()

    def delete(self, device: Device) -> None:
        self.devices = [item for item in self.devices if not (item.ip == device.ip and item.port == device.port)]
        self.save()


class SettingsDialog(Toplevel):
    def __init__(self, parent, settings: SettingsStore):
        super().__init__(parent)
        self.title("TACACS and PuTTY Settings")
        self.resizable(False, False)
        self.result = None

        self.username = StringVar(value=settings.data["username"])
        self.password = StringVar(value=settings.data["password"])
        self.putty_path = StringVar(value=settings.data["putty_path"])

        frame = Frame(self, padx=16, pady=16)
        frame.pack(fill=BOTH, expand=True)

        Label(frame, text="TACACS Username").grid(row=0, column=0, sticky="w", pady=(0, 6))
        Entry(frame, textvariable=self.username, width=48).grid(row=0, column=1, pady=(0, 6))

        Label(frame, text="TACACS Password").grid(row=1, column=0, sticky="w", pady=(0, 6))
        Entry(frame, textvariable=self.password, show="*", width=48).grid(row=1, column=1, pady=(0, 6))

        Label(frame, text="PuTTY Path").grid(row=2, column=0, sticky="w", pady=(0, 6))
        path_row = Frame(frame)
        path_row.grid(row=2, column=1, sticky="ew", pady=(0, 6))
        Entry(path_row, textvariable=self.putty_path, width=39).pack(side=LEFT)
        Button(path_row, text="Browse", command=self.browse_putty).pack(side=RIGHT, padx=(8, 0))

        actions = Frame(frame)
        actions.grid(row=3, column=0, columnspan=2, sticky="e", pady=(12, 0))
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

    def save(self) -> None:
        if not self.username.get().strip() or not self.password.get():
            messagebox.showerror(APP_NAME, "TACACS username and password are required.")
            return
        if not Path(self.putty_path.get()).exists():
            messagebox.showerror(APP_NAME, "PuTTY path does not exist.")
            return
        self.result = (self.username.get().strip(), self.password.get(), self.putty_path.get())
        self.destroy()


class DeviceFormDialog(Toplevel):
    def __init__(self, parent, title: str, device: "Device | None" = None):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.result: Device | None = None

        self.sysname = StringVar(value=device.sysname if device else "")
        self.ip = StringVar(value=device.ip if device else "")
        self.port = StringVar(value=device.port if device else "")

        frame = Frame(self, padx=16, pady=16)
        frame.pack(fill=BOTH, expand=True)

        Label(frame, text="Sysname").grid(row=0, column=0, sticky="w", pady=(0, 6))
        Entry(frame, textvariable=self.sysname, width=40).grid(row=0, column=1, pady=(0, 6))

        Label(frame, text="IP").grid(row=1, column=0, sticky="w", pady=(0, 6))
        Entry(frame, textvariable=self.ip, width=40).grid(row=1, column=1, pady=(0, 6))

        Label(frame, text="Port").grid(row=2, column=0, sticky="w", pady=(0, 6))
        Entry(frame, textvariable=self.port, width=40).grid(row=2, column=1, pady=(0, 6))

        actions = Frame(frame)
        actions.grid(row=3, column=0, columnspan=2, sticky="e", pady=(12, 0))
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
        self.result = Device(ip=ip, sysname=sysname or ip, port=port)
        self.destroy()


class ChiragPuttyLauncherApp(Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("980x620")
        self.minsize(820, 500)
        self.settings = SettingsStore()
        self.store = DeviceStore()
        self.search_var = StringVar()
        self.quick_ip_var = StringVar()

        self.create_menu()
        self.create_widgets()
        self.refresh_table()

    def create_menu(self) -> None:
        menu_bar = Menu(self)
        settings_menu = Menu(menu_bar, tearoff=0)
        settings_menu.add_command(label="Add / Edit TACACS and PuTTY Path", command=self.open_settings)
        menu_bar.add_cascade(label="Settings", menu=settings_menu)

        devices_menu = Menu(menu_bar, tearoff=0)
        devices_menu.add_command(label="Import CSV", command=self.import_csv)
        devices_menu.add_command(label="Export Current Devices", command=self.export_csv)
        menu_bar.add_cascade(label="Devices", menu=devices_menu)
        self.config(menu=menu_bar)

    def create_widgets(self) -> None:
        root = Frame(self, padx=14, pady=14)
        root.pack(fill=BOTH, expand=True)

        quick = LabelFrame(root, text="Quick Launch")
        quick.pack(fill=X, pady=(0, 10))
        Label(quick, text="Paste IP and press Enter").pack(side=LEFT, padx=(10, 8), pady=10)
        quick_entry = Entry(quick, textvariable=self.quick_ip_var, width=36)
        quick_entry.pack(side=LEFT, padx=(0, 8), pady=10)
        quick_entry.bind("<Return>", self.quick_launch)
        Button(quick, text="Launch", command=self.quick_launch).pack(side=LEFT, pady=10)

        toolbar = Frame(root)
        toolbar.pack(fill=X, pady=(0, 10))
        Label(toolbar, text="Search").pack(side=LEFT, padx=(0, 8))
        search = Entry(toolbar, textvariable=self.search_var, width=42)
        search.pack(side=LEFT)
        search.bind("<KeyRelease>", lambda _event: self.refresh_table())
        Button(toolbar, text="Import CSV", command=self.import_csv).pack(side=RIGHT)
        Button(toolbar, text="Add Device", command=self.add_device_dialog).pack(side=RIGHT, padx=(0, 8))

        columns = ("sysname", "ip", "port", "protocol")
        self.tree = ttk.Treeview(root, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("sysname", text="Sysname")
        self.tree.heading("ip", text="IP")
        self.tree.heading("port", text="Port")
        self.tree.heading("protocol", text="Protocol")
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

        actions = Frame(root)
        actions.pack(fill=X, pady=(10, 0))
        Button(actions, text="Launch PuTTY", command=self.launch_selected).pack(side=LEFT)
        Button(actions, text="Delete Device", command=self.delete_selected).pack(side=LEFT, padx=(8, 0))
        self.status = Label(actions, text=self.status_text(), anchor="e")
        self.status.pack(side=RIGHT)

    def status_text(self) -> str:
        return f"Devices: {len(self.store.devices)} | Config: {CONFIG_PATH}"

    def open_settings(self) -> None:
        dialog = SettingsDialog(self, self.settings)
        if dialog.result:
            self.settings.save(*dialog.result)
            self.status.config(text=self.status_text())
            messagebox.showinfo(APP_NAME, "Settings saved.")

    def ensure_settings(self) -> bool:
        if self.settings.data["username"] and self.settings.data["password"] and Path(self.settings.data["putty_path"]).exists():
            return True
        self.open_settings()
        return bool(
            self.settings.data["username"]
            and self.settings.data["password"]
            and Path(self.settings.data["putty_path"]).exists()
        )

    def ensure_putty(self) -> bool:
        if Path(self.settings.data["putty_path"]).exists():
            return True
        self.open_settings()
        return Path(self.settings.data["putty_path"]).exists()

    def filtered_devices(self) -> list[Device]:
        query = self.search_var.get().strip().lower()
        if not query:
            return self.store.devices
        return [
            device
            for device in self.store.devices
            if query in device.ip.lower() or query in device.sysname.lower() or query in device.port.lower()
        ]

    def refresh_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, device in enumerate(self.filtered_devices()):
            self.tree.insert("", END, iid=str(index), values=(device.sysname, device.ip, device.port, device.protocol.upper()))
        self.status.config(text=self.status_text()) if hasattr(self, "status") else None

    def selected_device(self) -> Device | None:
        selected = self.tree.selection()
        if not selected:
            return None
        values = self.tree.item(selected[0], "values")
        return Device(ip=values[1], sysname=values[0], port=values[2])

    def launch_device(self, device: Device) -> None:
        if device.protocol == "telnet":
            # Port 23 -> plain Telnet. Auto login is not sent because it is
            # unreliable against Telnet sessions; the user logs in manually.
            if not self.ensure_putty():
                return
            command = [self.settings.data["putty_path"], "-telnet", device.ip, "-P", str(device.port)]
        else:
            if not self.ensure_settings():
                return
            command = [
                self.settings.data["putty_path"],
                "-ssh",
                device.ip,
                "-P",
                str(device.port),
                "-l",
                self.settings.data["username"],
                "-pw",
                self.settings.data["password"],
            ]

        try:
            subprocess.Popen(command, shell=False)
            self.status.config(text=f"Launched {device.sysname} ({device.ip}:{device.port}) via {device.protocol.upper()}")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not launch PuTTY:\n{exc}")

    def launch_selected(self) -> None:
        device = self.selected_device()
        if not device:
            messagebox.showwarning(APP_NAME, "Select a device first.")
            return
        self.launch_device(device)

    def quick_launch(self, event=None) -> None:
        ip = self.quick_ip_var.get().strip()
        if not ip:
            return
        port = simpledialog.askstring(APP_NAME, f"Enter port for {ip}")
        if not port or not port.strip():
            return
        sysname = simpledialog.askstring(APP_NAME, "Enter sysname", initialvalue=ip) or ip
        device = Device(ip=ip, sysname=sysname.strip(), port=port.strip())
        self.store.add_or_update(device)
        self.quick_ip_var.set("")
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
