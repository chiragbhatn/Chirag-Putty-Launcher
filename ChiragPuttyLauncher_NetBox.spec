# -*- mode: python ; coding: utf-8 -*-
# Build: pyinstaller ChiragPuttyLauncher_NetBox.spec  ->  dist\ChiragPuttyLauncher_NetBox.exe

ui_files = ["index.html", "styles.css", "app.js", "app.ico"]

a = Analysis(
    ['device_launcher_app.py'],
    pathex=[],
    binaries=[],
    datas=[(f"launcher_ui/{name}", "launcher_ui") for name in ui_files],
    hiddenimports=["clr", "webview.platforms.winforms", "webview.platforms.edgechromium"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "dev_server", "test_launcher"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ChiragPuttyLauncher_NetBox',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='launcher_ui/app.ico',
)
