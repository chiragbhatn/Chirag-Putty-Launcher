# Chirag Putty Launcher

This is a standalone Windows desktop app for launching PuTTY sessions. It is separate from the Django app.

## What It Does

- Saves multiple named connection profiles, each with TACACS username/password, PuTTY path, and logging preference.
- Selects the profile to use from the main window before launching a device.
- Imports CSV files with `hostname,ip,port` (the `hostname`/`sysname` column and column order are both accepted).
- Searches devices.
- The port from the CSV/entry is always used as-is (no forced port 23).
- Protocol is chosen from the port: port `23` launches **Telnet**, any other port launches **SSH**.
- For SSH, uses PuTTY's native username/password arguments for auto-login.
- For Telnet, PuTTY is launched to the device and you log in manually (auto id/password is not reliable over Telnet).
- Optional per-profile session-output logging creates timestamped local `.log` files through PuTTY.
- Supports multiple NetBox URL/API-key connections and a manual `Sync NetBox` action.
- Syncs active Huawei/Juniper devices whose hostname ends in `-acc`, `-dis`, or `-core`; the NetBox `access`, `distribution`, and `core` roles are a fallback when a hostname has no suffix.
- Preserves ports on existing matching devices, uses port `22` for new NetBox devices, and only removes stale devices that were originally created by NetBox sync.
- Shows `Sync NetBox !` when one or more configured NetBox connections fail; those connections are not cleaned up.
- Right-click a device for **Edit** (a single 3-row form for sysname / IP / port) or **Delete**.
- Quick launch defaults to SSH using port `22` and the IP as the sysname. Clear the Default checkbox to enter a custom sysname and port while keeping the IP editable.
- Quick Launch recognizes an IP already in the inventory and launches that saved device rather than adding a duplicate.

## Run From Source

```powershell
cd desktop_launcher
python device_launcher_app.py
```

## Build EXE

```powershell
cd desktop_launcher
python -m pip install -r requirements-build.txt
pyinstaller --noconsole --onefile --name ChiragPuttyLauncher device_launcher_app.py
```

The EXE will be created at:

```text
desktop_launcher\dist\ChiragPuttyLauncher.exe
```

## Local Data

The app stores its local config and imported device list under:

```text
%APPDATA%\ChiragPuttyLauncher
```

The password is protected with Windows DPAPI when running on Windows.

Profiles and imported devices are saved separately, so the same device can be opened with any selected profile. When session logging is enabled for a profile, PuTTY writes a timestamped log file to that profile's configured log folder.
