# Chirag Putty Launcher

This is a standalone Windows desktop app for launching PuTTY sessions. It is separate from the Django app.

## What It Does

- Saves TACACS username/password and PuTTY path.
- Imports CSV files with `hostname,ip,port` (the `hostname`/`sysname` column and column order are both accepted).
- Searches devices.
- The port from the CSV/entry is always used as-is (no forced port 23).
- Protocol is chosen from the port: port `23` launches **Telnet**, any other port launches **SSH**.
- For SSH, uses PuTTY's native username/password arguments for auto-login.
- For Telnet, PuTTY is launched to the device and you log in manually (auto id/password is not reliable over Telnet).
- Right-click a device for **Edit** (a single 3-row form for sysname / IP / port) or **Delete**.
- Quick launch: paste an IP, press Enter, enter the port, and launch.

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
