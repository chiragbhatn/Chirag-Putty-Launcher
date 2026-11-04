"""Offline smoke tests for launcher persistence and import safety."""
from pathlib import Path
from tempfile import TemporaryDirectory

from device_launcher_app import APP_THEMES, Device, DeviceStore, ProfileStore


def run() -> None:
    with TemporaryDirectory() as folder:
        root = Path(folder)
        devices_path = root / "devices.csv"
        config_path = root / "config.json"

        devices = DeviceStore(devices_path)
        devices.add_or_update(Device("10.0.0.1", "core-1", "22", group="Core", tags="prod,edge", notes="Primary", putty_session="core-1"))
        restored = DeviceStore(devices_path).devices[0]
        assert restored.group == "Core" and restored.tags == "prod,edge" and restored.putty_session == "core-1"

        sample = root / "import.csv"
        sample.write_text("ip,sysname,port\n10.0.0.2,access-1,22\n10.0.0.3,bad,70000\n", encoding="utf-8")
        valid, errors = devices.preview_csv(sample)
        assert valid == 1 and errors and "Row 3" in errors[0]

        profiles = ProfileStore(config_path)
        for theme in APP_THEMES:
            profiles.set_ui_theme(theme)
            assert ProfileStore(config_path).ui_theme == theme
        backup = root / "backup.json"
        profiles.export_backup(backup)
        restored_profiles = ProfileStore(root / "restored.json")
        restored_profiles.restore_backup(backup)
        assert restored_profiles.ui_theme == profiles.ui_theme


if __name__ == "__main__":
    run()
    print("Launcher smoke tests passed.")
