# Chirag Putty Launcher

A Windows desktop app for finding network devices and opening PuTTY sessions to them. Version 2 is a full redesign: a native window with a modern interface, built for inventories of thousands of switches.

## What it does

**Find and open devices**
- One search box finds devices by hostname, IP, group, tag or note. Press Enter to open the top match.
- Type an IP address that isn't in the list (optionally `ip:port`) and press Enter to connect straight away. It is saved as a new device.
- Port 23 opens Telnet (you sign in by hand). Any other port opens SSH and signs in with the active profile's TACACS credentials. Devices can also use a saved PuTTY session.
- Open several selected devices at once, or reopen anything from Recently opened.
- Devices are colour coded by role, after fibre and copper cable jackets: yellow core, aqua distribution, blue access. The role comes from the `-core`, `-dis` or `-acc` hostname suffix, or from NetBox.

**Organise**
- Groups in the sidebar: create, rename and delete them, and drag devices onto a group to move them.
- Favorites (Ctrl+D or the star on each row), tags and notes on every device.
- Filter by role, protocol, reachability, source (added here or NetBox) and tag. Sort by any column.
- Copy IP addresses or hostnames for one or many devices.

**Check connections**
- Check now: port test with the SSH banner, plus ping, for the selected device.
- Check reachability for a whole list, group or selection, run in parallel in the background.
- When a session opens, the launcher tests the port alongside PuTTY. If the device can't be reached, or PuTTY exits with an error, a notification appears and the device is marked.

**Session logs**
- With logging on in a profile, PuTTY writes a dated log of each session. The Session logs page lists them, shows the output with find-in-log, and opens them in Notepad or Explorer.

**NetBox**
- Several NetBox connections, each with its own filters: roles, manufacturers, sites, status and skipped models. Turn a connection off to leave it out of syncs.
- Sync on demand (F5), on a schedule (every 15 minutes to once a day) and when the app opens.
- Sync history records what was added, updated and removed, and why a connection failed.
- Devices you added yourself are never removed by a sync, and connections that fail are left untouched.

**Profiles and settings**
- Several connection profiles; switch the active one from the sidebar (Ctrl+Shift+P).
- Import and export profiles between PCs. Passwords are left out unless you include them.
- Back up and restore all settings. Four themes: Slate, Midnight, Ocean and Graphite.
- Check GitHub for a newer release.

## Keyboard shortcuts

| Action | Keys |
| --- | --- |
| Find a device or type an IP | Ctrl K |
| Open the selected device / every selected device | Enter / Ctrl Enter |
| Move through the list, extend the selection | ↑ ↓, Shift ↑ ↓ |
| Select all shown | Ctrl A |
| Copy IP address / hostname | Ctrl C / Ctrl Shift C |
| Check connection | Ctrl T |
| Favorite | Ctrl D |
| Edit / delete device | F2 / Del |
| Add a device / import a CSV | Ctrl N / Ctrl I |
| Sync NetBox | F5 |
| Switch profile | Ctrl Shift P |
| Views: all, favorites, recent, logs, NetBox, settings | Ctrl 1 to 6 |
| Close all PuTTY windows | Ctrl Shift Q |
| All shortcuts | ? |

## Install

Run `ChiragPuttyLauncher_Setup_2.0.0.exe` from the GitHub release. It upgrades an existing 1.x install in place. The app needs the Microsoft Edge WebView2 Runtime, which Windows 11 already includes; the installer offers the download if it is missing.

## Your data

Everything is stored on your PC in `%APPDATA%\ChiragPuttyLauncher`:

- `config.json`: profiles, NetBox connections, groups, history and preferences. Passwords and API tokens are encrypted with Windows DPAPI, so they only open on your Windows account.
- `devices.csv`: the device list.
- `Logs\`: session logs, unless a profile uses another folder.

The first time version 2 starts, it copies your 1.x files to `config.before-v2.json` and `devices.before-v2.csv`.

## Develop

```powershell
cd desktop_launcher
python -m pip install -r requirements-build.txt
python device_launcher_app.py          # run the app
python test_launcher.py                # offline tests (no PuTTY, no network devices)
```

Code layout:

- `launcher_core.py`: storage, NetBox sync, diagnostics, PuTTY commands and session logs. No UI code.
- `device_launcher_app.py`: the native window (pywebview) and `LauncherApi`, the bridge the UI calls.
- `launcher_ui/`: the interface (HTML, CSS and JavaScript, no external libraries, works offline).
- `dev_server.py`: serves the UI in a normal browser for development. Set `CHIRAG_LAUNCHER_HOME` to a test folder so your real data is untouched.

Build the EXE and installer:

```powershell
pyinstaller --noconfirm ChiragPuttyLauncher_NetBox.spec
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" ChiragPuttyLauncher_NetBox_Setup.iss
```

The EXE is written to `dist\` and the installer to `installer_output\`.
