"""Offline tests for the launcher core and the UI bridge.

Run:  python test_launcher.py
Nothing here touches your real launcher data or opens PuTTY.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

os.environ["CHIRAG_LAUNCHER_HOME"] = tempfile.mkdtemp(prefix="launcher-test-home-")

import launcher_core as core  # noqa: E402
import device_launcher_app as app  # noqa: E402
from device_launcher_app import APP_THEMES, Device, DeviceStore, LauncherApi, NetBoxConnection, ProfileStore  # noqa: E402

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def fresh(root: Path) -> tuple[ProfileStore, DeviceStore]:
    return ProfileStore(root / "config.json"), DeviceStore(root / "devices.csv")


# --------------------------------------------------------------------------- storage

@test
def device_metadata_round_trip(root: Path):
    devices = DeviceStore(root / "devices.csv")
    devices.add_or_update(Device("10.0.0.1", "core-1", "22", group="Core", tags="prod,edge", notes="Primary", putty_session="core-1"))
    restored = DeviceStore(root / "devices.csv").devices[0]
    assert restored.group == "Core" and restored.tags == "prod,edge" and restored.putty_session == "core-1"


@test
def csv_preview_and_import(root: Path):
    devices = DeviceStore(root / "devices.csv")
    devices.add_or_update(Device("10.0.0.2", "access-1", "22"))
    sample = root / "import.csv"
    sample.write_text("ip,sysname,port\n10.0.0.2,access-1,22\n10.0.0.3,bad,70000\n", encoding="utf-8")
    valid, errors = devices.preview_csv(sample)
    assert valid == 1 and errors and "Row 3" in errors[0]
    try:
        devices.import_csv(sample)
        raise AssertionError("import with errors should fail")
    except ValueError:
        pass
    good = root / "good.csv"
    good.write_text("Hostname,IP,Port,group,tags\nsw-a,10.0.0.2,22,,\nsw-b,10.0.0.4,23,Lab,\"x,y\"\n\n", encoding="utf-8-sig")
    details = devices.preview_csv_details(good)
    assert details["valid"] == 2 and details["new"] == 1 and details["existing"] == 1 and not details["errors"]
    assert devices.import_csv(good) == 1
    added = devices.find("10.0.0.4:23")
    assert added and added.group == "Lab" and added.tag_list == ["x", "y"] and added.protocol == "telnet"
    missing = root / "missing.csv"
    missing.write_text("address,port\n1.1.1.1,22\n", encoding="utf-8")
    assert devices.preview_csv(missing)[1][0].startswith("CSV must have")


@test
def legacy_and_damaged_device_files_load(root: Path):
    path = root / "devices.csv"
    path.write_text("ip,sysname,port,source,netbox_connection_id,netbox_device_id\n"
                    "10.1.1.1,a-acc,22,netbox,c1,7\n10.1.1.2,b,abc,manual,,\n10.1.1.3,c-core-01,22,manual,,\n", encoding="utf-8")
    store = DeviceStore(path)
    assert [d.ip for d in store.devices] == ["10.1.1.1", "10.1.1.3"]
    assert store.load_errors and "Row 3" in store.load_errors[0]
    assert store.devices[0].kind == "access" and store.devices[1].kind == "core"
    store.save()
    header = path.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert header == core.DEVICE_FIELDS


@test
def export_csv_round_trip(root: Path):
    store = DeviceStore(root / "devices.csv")
    store.add_or_update(Device("10.0.0.9", "edge", "2222", group="Edge", tags="a,b", notes="n"))
    out = root / "out.csv"
    assert store.export_csv(out) == 1
    other = DeviceStore(root / "other.csv")
    assert other.import_csv(out) == 1
    device = other.devices[0]
    assert (device.port, device.group, device.tags, device.notes) == ("2222", "Edge", "a,b", "n")


@test
def themes_backup_and_restore(root: Path):
    profiles = ProfileStore(root / "config.json")
    for theme in APP_THEMES:
        profiles.set_ui_theme(theme)
        assert ProfileStore(root / "config.json").ui_theme == theme
    profiles.set_ui_theme("Neon")
    assert profiles.ui_theme == core.DEFAULT_THEME
    profiles.add_group("Lab")
    backup = root / "backup.json"
    profiles.export_backup(backup)
    restored = ProfileStore(root / "restored.json")
    restored.restore_backup(backup)
    assert restored.ui_theme == profiles.ui_theme and restored.groups == ["Lab"]
    bad = root / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    try:
        restored.restore_backup(bad)
        raise AssertionError("restore of non-backup should fail")
    except ValueError:
        pass


@test
def legacy_single_profile_config_migrates(root: Path):
    path = root / "config.json"
    path.write_text(json.dumps({"username": "ops", "password": core.protect_secret("pw"), "putty_path": "C:/p.exe", "ui_theme": "dark"}), encoding="utf-8")
    store = ProfileStore(path)
    assert len(store.profiles) == 1 and store.profiles[0].username == "ops" and store.profiles[0].password == "pw"
    assert store.ui_theme == "Midnight"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 3 and "pw" not in json.dumps(raw["profiles"])


@test
def config_with_bom_or_damage_is_not_lost(root: Path):
    path = root / "config.json"
    ProfileStore(path).save_profile(core.ConnectionProfile("x", "Keep me", "u", "", "p.exe", False, ""))
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert any(p.name == "Keep me" for p in ProfileStore(path).profiles), "a UTF-8 BOM must not wipe profiles"
    path.write_text("{ not json", encoding="utf-8")
    ProfileStore(path)
    kept = list(root.glob("config.unreadable-*.json"))
    assert kept and kept[0].read_text(encoding="utf-8") == "{ not json"


@test
def profile_import_export(root: Path):
    store = ProfileStore(root / "config.json")
    profile = store.active_profile()
    profile.username, profile.password = "noc", "s3cret"
    store.save_profile(profile)
    plain = root / "plain.json"
    with_pw = root / "with.json"
    assert store.export_profiles(plain) == 1
    assert "s3cret" not in plain.read_text(encoding="utf-8")
    store.export_profiles(with_pw, [profile.id], include_passwords=True)
    assert "s3cret" in with_pw.read_text(encoding="utf-8")
    other = ProfileStore(root / "other.json")
    names = other.import_profiles(with_pw)
    assert names == ["Default Profile 2"], names
    imported = next(p for p in other.profiles if p.name == "Default Profile 2")
    assert imported.username == "noc" and imported.password == "s3cret" and imported.id != profile.id
    backup = root / "backup.json"
    store.export_backup(backup)
    third = ProfileStore(root / "third.json")
    third.import_profiles(backup)
    assert any(p.password == "s3cret" for p in third.profiles), "backup import should decrypt on the same account"
    empty = root / "empty.json"
    empty.write_text('{"profiles": []}', encoding="utf-8")
    try:
        third.import_profiles(empty)
        raise AssertionError("empty import should fail")
    except ValueError:
        pass


@test
def device_edits_groups_tags_favorites(root: Path):
    store = DeviceStore(root / "devices.csv")
    store.add_or_update(Device("10.0.0.1", "a", "22", group="G1", tags="x"))
    store.add_or_update(Device("10.0.0.2", "b", "22", group="G1"))
    store.update_device("10.0.0.1:22", "10.0.0.10", "a2", "2200", group="", tags="t1, T1, t2", notes=" n ")
    edited = store.find("10.0.0.10:2200")
    assert edited and edited.group == "" and edited.tags == "t1,t2" and edited.notes == "n"
    try:
        store.update_device("10.0.0.10:2200", "10.0.0.2", "a2", "22")
        raise AssertionError("duplicate endpoint should fail")
    except ValueError:
        pass
    for ip, port in (("", "22"), ("10.0.0.1", "0"), ("bad host!", "22")):
        try:
            core.validate_endpoint(ip, port)
            raise AssertionError(f"{ip}:{port} should be rejected")
        except ValueError:
            pass
    keys = ["10.0.0.10:2200", "10.0.0.2:22"]
    assert store.set_group(keys, "Core") == 2 and store.groups() == {"Core": 2}
    assert store.rename_group("Core", "Backbone") == 2 and store.groups() == {"Backbone": 2}
    store.add_tags(keys, ["prod", "PROD"])
    assert store.find("10.0.0.2:22").tag_list == ["prod"]
    store.remove_tags(keys, ["Prod"])
    assert store.find("10.0.0.2:22").tag_list == []
    store.set_favorite(keys[:1], True)
    reloaded = DeviceStore(root / "devices.csv")
    assert reloaded.find(keys[0]).is_favorite and not reloaded.find(keys[1]).is_favorite
    assert store.delete(keys) == 2 and not store.devices


@test
def role_detection(root: Path):
    assert Device("1", "bld2-acc-01", "22").kind == "access"
    assert Device("1", "dc1-dis", "22").kind == "distribution"
    assert Device("1", "x-core.lab", "22").kind == "core"
    assert Device("1", "accounting", "22").kind == ""
    assert Device("1", "router", "22", role="core").kind == "core"


# --------------------------------------------------------------------------- NetBox

def fake_nb(id_, name, ip, role="", model="EX4300", primary=True):
    return SimpleNamespace(
        id=id_, name=name, role=SimpleNamespace(slug=role), device_type=SimpleNamespace(model=model),
        primary_ip=SimpleNamespace(address=f"{ip}/24") if primary else None,
    )


@test
def netbox_filters(root: Path):
    connection = NetBoxConnection("c1", "NB", "https://nb", "key", roles="core,access", sites="DC1, dc2", manufacturers="Huawei")
    client = core.NetBoxClient()
    filters = client.api_filters(connection)
    assert filters == {"status": "active", "manufacturer": ["huawei"], "site": ["dc1", "dc2"]}
    records = client.filter_records(connection, [
        fake_nb(1, "a-acc", "10.0.0.1"),
        fake_nb(2, "b-dis", "10.0.0.2"),                       # role not selected
        fake_nb(3, "c", "10.0.0.3", role="core"),               # role fallback
        fake_nb(4, "d-core", "10.0.0.4", model="QFX5120-32C-AFO"),  # ignored model
        fake_nb(5, "e-acc", "10.0.0.5", primary=False),         # no IP
        fake_nb(6, "f", "10.0.0.6"),                            # no role at all
    ])
    assert [(r.device_id, r.bucket) for r in records] == [("1", "access"), ("3", "core")]


@test
def netbox_errors_are_readable(root: Path):
    assert core.normalize_netbox_url(" https://nb.example/api/ ") == "https://nb.example"
    assert core.normalize_netbox_url("https://nb.example/") == "https://nb.example"
    url = "https://nb.example.net"
    resolve = Exception("HTTPSConnectionPool(host='nb.example.net'): Max retries (Caused by NameResolutionError(...getaddrinfo failed))")
    assert core.friendly_netbox_error(resolve, url).startswith("Couldn't find nb.example.net")
    denied = Exception("403 Forbidden")
    denied.req = SimpleNamespace(status_code=403)
    assert "API token" in core.friendly_netbox_error(denied, url)
    assert "didn't answer" in core.friendly_netbox_error(Exception("Read timed out."), url)
    full = Device("1.1.1.1", "a", "22").to_ui()
    assert set(full) == core.UI_REQUIRED_FIELDS, full
    rich = Device("1.1.1.1", "a-core", "22", group="G", tags="x", reachability="up").to_ui()
    assert rich["group"] == "G" and rich["tags"] == ["x"] and rich["kind"] == "core" and rich["reachability"] == "up"


class FakeClient:
    def __init__(self, results):
        self.results = results

    def fetch(self, connection):
        result = self.results[connection.id]
        if isinstance(result, Exception):
            raise result
        return result


@test
def netbox_sync_history_and_safety(root: Path):
    profiles, store = fresh(root)
    good = NetBoxConnection("good", "Good", "https://a", "k")
    bad = NetBoxConnection("bad", "Bad", "https://b", "k")
    off = NetBoxConnection("off", "Off", "https://c", "k", enabled=False)
    for connection in (good, bad, off):
        profiles.save_netbox_connection(connection)
    store.add_or_update(Device("10.9.9.9", "manual-1", "22"))
    store.devices.append(Device("10.8.8.8", "old-bad", "22", source="netbox", netbox_connection_id="bad", netbox_device_id="99"))
    store.devices.append(Device("10.7.7.7", "stale", "22", source="netbox", netbox_connection_id="good", netbox_device_id="55"))
    store.save()
    client = FakeClient({
        "good": [core.NetBoxDevice("good", "1", "x-core", "10.0.0.1", "core")],
        "bad": core.NetBoxFetchError("timeout"),
        "off": [core.NetBoxDevice("off", "2", "y-acc", "10.0.0.2", "access")],
    })
    entry = core.run_netbox_sync(profiles, store, None, "manual", client=client)
    assert entry["added"] == 1 and entry["removed"] == 1, entry
    assert [c["id"] for c in entry["connections"]] == ["good", "bad"], "disabled connections are skipped"
    assert not entry["connections"][1]["ok"] and entry["connections"][1]["error"] == "timeout"
    keys = {d.key for d in store.devices}
    assert "10.9.9.9:22" in keys and "10.8.8.8:22" in keys and "10.7.7.7:22" not in keys
    assert store.find("10.0.0.1:22").role == "core"
    reloaded = ProfileStore(root / "config.json")
    assert reloaded.sync_history[0]["id"] == entry["id"] and reloaded.settings["last_sync_at"]
    only_off = core.run_netbox_sync(profiles, store, ["off"], "manual", client=client)
    assert [c["id"] for c in only_off["connections"]] == ["off"] and only_off["added"] == 1


@test
def scheduled_sync_due(root: Path):
    from datetime import datetime, timedelta
    profiles = ProfileStore(root / "config.json")
    assert not profiles.sync_due()
    profiles.save_netbox_connection(NetBoxConnection("c", "C", "https://c", "k"))
    profiles.update_settings({"sync_interval_minutes": 60})
    assert profiles.sync_due()
    profiles.settings["last_sync_at"] = datetime.now().isoformat(timespec="seconds")
    assert not profiles.sync_due()
    assert profiles.sync_due(datetime.now() + timedelta(minutes=61))
    profiles.settings["last_sync_at"] = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
    profiles.record_sync({"finished_at": datetime.now().isoformat(timespec="seconds"),
                          "connections": [{"id": "c", "name": "C", "ok": False, "count": 0, "error": "down"}]})
    assert not profiles.sync_due(), "a failed sync must not be retried every 30 seconds"
    assert profiles.sync_due(datetime.now() + timedelta(minutes=61))
    try:
        profiles.update_settings({"sync_interval_minutes": 7})
        raise AssertionError("odd interval should be rejected")
    except ValueError:
        pass
    profiles.update_settings({"diagnostic_timeout": 99, "preflight_check": 0})
    assert profiles.settings["diagnostic_timeout"] == 10 and profiles.settings["preflight_check"] is False


# --------------------------------------------------------------------------- diagnostics

def banner_server(payload: bytes) -> tuple[int, socket.socket]:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(5)

    def serve():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            try:
                conn.sendall(payload)
                time.sleep(0.2)
            finally:
                conn.close()

    threading.Thread(target=serve, daemon=True).start()
    return server.getsockname()[1], server


def closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@test
def diagnostics(root: Path):
    port, server = banner_server(b"SSH-2.0-HUAWEI-1.5\r\n")
    try:
        result = core.diagnose("127.0.0.1", str(port), timeout=2, full=True)
        assert result["status"] == "up" and result["port"]["banner"] == "SSH-2.0-HUAWEI-1.5", result
        assert result["ping"] and result["ping"]["ok"], result["ping"]
    finally:
        server.close()
    shut = closed_port()
    refused = core.probe_port("127.0.0.1", shut, timeout=4)  # Windows retries a refused SYN for about 2 s
    assert not refused["ok"] and "refused" in refused["detail"], refused
    quick = core.diagnose("127.0.0.1", str(shut), timeout=1, full=False)
    assert quick["status"] == "filtered", quick  # ping answers, port closed
    assert core.clean_banner(b"\xff\xfb\x01\xff\xfd\x03\x1b[2JLogin: ") == "Login:"
    seen = []
    results = core.check_many([("a", "127.0.0.1", str(shut)), ("b", "127.0.0.1", str(shut))], timeout=1, on_result=lambda k, r: seen.append(k))
    assert sorted(seen) == ["a", "b"] and set(results) == {"a", "b"}


# --------------------------------------------------------------------------- PuTTY and logs

@test
def putty_commands(root: Path):
    profile = core.ConnectionProfile("p", "NOC Team", "u", "pw", "putty.exe", True, str(root / "logs"))
    ssh = Device("10.0.0.1", "sw/1", "22")
    log = core.session_log_path(profile, ssh)
    assert log.parent == root / "logs" and log.name.endswith("_NOC_Team_sw_1_10.0.0.1_22.log")
    assert core.build_putty_command(profile, ssh, log) == ["putty.exe", "-ssh", "10.0.0.1", "-P", "22", "-l", "u", "-pw", "pw", "-sessionlog", str(log), "-logoverwrite"]
    telnet = Device("10.0.0.2", "t", "23")
    assert core.build_putty_command(profile, telnet, None) == ["putty.exe", "-telnet", "10.0.0.2", "-P", "23"]
    saved = Device("10.0.0.3", "s", "22", putty_session="My Session")
    assert core.build_putty_command(profile, saved, None) == ["putty.exe", "-load", "My Session"]
    no_password = core.ConnectionProfile("p", "P", "", "", "putty.exe", False, "")
    assert core.build_putty_command(no_password, ssh, None) == ["putty.exe", "-ssh", "10.0.0.1", "-P", "22"]
    assert core.session_log_path(no_password, ssh) is None


@test
def session_logs(root: Path):
    folder = root / "logs"
    profile = core.ConnectionProfile("p", "My_Team", "u", "", "putty.exe", True, str(folder))
    device = Device("10.0.0.5", "bld_2-acc", "22")
    path = core.session_log_path(profile, device)
    path.write_bytes(b"\x1b[1;32mhello\x1b[0m\r\nline 2\r\n" + b"x" * 50)
    info = core.parse_log_name(path.name, ["My_Team"])
    assert (info["profile"], info["sysname"], info["ip"], info["port"]) == ("My_Team", "bld_2-acc", "10.0.0.5", "22"), info
    listed = core.list_session_logs([profile])
    assert listed and listed[0]["ip"] == "10.0.0.5" and listed[0]["size"] > 0
    resolved = core.resolve_log_path(str(path), [profile])
    tail = core.read_log_tail(resolved)
    assert tail["text"].startswith("hello\nline 2") and not tail["truncated"]
    assert core.read_log_tail(resolved, max_bytes=10)["truncated"]
    outside = root / "secret.log"
    outside.write_text("nope", encoding="utf-8")
    for bad in (str(outside), str(folder / "missing.log"), str(root / "config.json")):
        try:
            core.resolve_log_path(bad, [profile])
            raise AssertionError(f"{bad} should be rejected")
        except ValueError:
            pass


@test
def version_compare(root: Path):
    assert core.version_tuple("v2.10.0") > core.version_tuple("2.9.9")
    assert core.version_tuple("2.0.0") == core.version_tuple("2.0")[:2] + (0,)


# --------------------------------------------------------------------------- UI bridge

class FakeProcess:
    launched: list[list[str]] = []
    exit_code: int | None = None

    def __init__(self, command, shell=False):
        FakeProcess.launched.append(command)

    def wait(self, timeout=None):
        if FakeProcess.exit_code is None:
            raise app.subprocess.TimeoutExpired("putty", timeout)
        return FakeProcess.exit_code


def make_api(root: Path):
    events: list[tuple[str, dict]] = []
    profiles, store = fresh(root)
    fake_putty = root / "putty.exe"
    fake_putty.write_bytes(b"")
    profile = profiles.active_profile()
    profile.putty_path, profile.username, profile.password = str(fake_putty), "noc", "TopSecret!"
    profiles.save_profile(profile)
    profiles.save_netbox_connection(NetBoxConnection("nb", "NB", "https://nb", "ApiKey123"))
    api = LauncherApi(profiles, store, emit=lambda e, p: events.append((e, p)))
    return api, events


def wait_for(predicate, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@test
def bridge_state_never_leaks_secrets(root: Path):
    api, _ = make_api(root)
    state = api.get_state()
    text = json.dumps(state)
    assert "TopSecret!" not in text and "ApiKey123" not in text
    assert state["profiles"][0]["has_password"] and state["netbox"]["connections"][0]["has_key"]
    assert {t["name"] for t in state["themes"]} == set(APP_THEMES)


@test
def bridge_profiles_keep_or_clear_password(root: Path):
    api, _ = make_api(root)
    profile = api.get_state()["profiles"][0]
    data = {**profile, "password": ""}
    assert "__error" not in api.save_profile(data)
    assert api._profiles.active_profile().password == "TopSecret!"
    api.save_profile({**data, "clear_password": True})
    assert api._profiles.active_profile().password == ""
    assert "PuTTY" in api.save_profile({**data, "putty_path": str(root / "nope.exe")})["__error"]
    assert "name" in api.save_profile({**data, "name": " "})["__error"]


@test
def bridge_launch_quick_connect_and_failures(root: Path):
    api, events = make_api(root)
    original = app.subprocess.Popen
    app.subprocess.Popen = FakeProcess
    FakeProcess.launched.clear()
    try:
        shut = closed_port()
        FakeProcess.exit_code = 1
        result = api.quick_connect("127.0.0.1", str(shut), "")
        assert "__error" not in result, result
        assert result["created"] and FakeProcess.launched[-1][:3] == [str(root / "putty.exe"), "-ssh", "127.0.0.1"]
        assert "-pw" in FakeProcess.launched[-1]
        key = f"127.0.0.1:{shut}"
        assert api._profiles.recent_devices[0]["ip"] == "127.0.0.1"
        assert wait_for(lambda: any(e == "notify" and p["level"] == "error" for e, p in events)), events
        notes = [p for e, p in events if e == "notify"]
        assert notes[0]["title"].startswith("Couldn't reach") and notes[0]["key"] == key
        assert len(notes) == 1, "a failed preflight should not be reported twice"
        assert api._store.find(key).reachability == "down" and api._store.find(key).last_failure
        again = api.quick_connect("127.0.0.1", str(shut), "")
        assert not again["created"], "quick connect reuses saved devices"

        port, server = banner_server(b"SSH-2.0-test\r\n")
        try:
            events.clear()
            api._store.add_or_update(Device("127.0.0.1", "lab-core", str(port)))
            FakeProcess.exit_code = 1
            api.launch([f"127.0.0.1:{port}"])
            assert wait_for(lambda: any(e == "notify" for e, _ in events))
            note = next(p for e, p in events if e == "notify")
            assert "ended with an error" in note["title"], note
            assert api._store.find(f"127.0.0.1:{port}").reachability == "up"
        finally:
            server.close()

        api._profiles.active_profile().putty_path = str(root / "missing.exe")
        assert "PuTTY wasn't found" in api.launch([key])["__error"]
        assert "__error" in api.quick_connect("bad host", "22")
    finally:
        assert wait_for(lambda: not any(t.name == "watch-launch" for t in threading.enumerate()), 15)
        app.subprocess.Popen = original
        FakeProcess.exit_code = None


@test
def bridge_bulk_check_and_groups(root: Path):
    api, events = make_api(root)
    shut = closed_port()
    for i in range(3):
        api._store.add_or_update(Device("127.0.0.1", f"d{i}", str(shut + i)))
    keys = [d.key for d in api._store.devices]
    assert api.check_devices(keys) == {"total": 3}
    assert wait_for(lambda: any(e == "check_done" for e, _ in events), 15)
    done = next(p for e, p in events if e == "check_done")
    assert done["done"] == 3 and not done["cancelled"]
    progress = [d["key"] for e, p in events if e == "check_progress" for d in p["devices"]]
    assert sorted(progress) == sorted(keys)
    assert all(d.reachability in {"filtered", "down"} and d.last_checked for d in DeviceStore(root / "devices.csv").devices)
    assert "__error" in api.check_devices([])

    api.create_group("Lab")
    api.set_group(keys[:2], "Lab")
    state = api.get_devices()
    assert {"name": "Lab", "count": 2} in state["groups"]
    assert "__error" in api.rename_group("Lab", " ")
    api.rename_group("Lab", "Bench")
    api.delete_group("Bench")
    assert not api._store.groups() and not api._profiles.groups
    fav = api.set_favorite(keys[:1], True)
    assert fav["devices"][0]["favorite"]


@test
def bridge_errors_are_friendly(root: Path):
    api, _ = make_api(root)
    assert "only available" in api.export_devices(None, None)["__error"]
    assert "no longer" in api.diagnose_device("1.2.3.4:22")["__error"]
    assert "__error" in api.read_log(str(root / "config.json"))
    assert "__error" in api.open_url("https://evil.example.com")
    api._profiles.netbox_connections[0].enabled = False
    assert "Turn on" in api.sync_netbox()["__error"]
    saved = api.save_netbox_connection({"id": "nb", "name": "NB", "url": "https://nb2", "api_key": "", "roles": ["core"],
                                        "manufacturers": "huawei", "sites": "", "status": "active", "ignored_models": "", "enabled": True})
    assert "__error" not in saved and api._profiles.netbox_connections[0].api_key == "ApiKey123", "blank token keeps the saved one"
    assert "role" in api.save_netbox_connection({"name": "X", "url": "https://x", "api_key": "k", "roles": []})["__error"]


@test
def backup_before_upgrade_runs_once(root: Path):
    home = Path(os.environ["CHIRAG_LAUNCHER_HOME"])
    for name in ("config.before-v2.json", "devices.before-v2.csv"):
        (home / name).unlink(missing_ok=True)
    (home / "config.json").write_text('{"version": 2, "profiles": []}', encoding="utf-8")
    (home / "devices.csv").write_text("ip,sysname,port\n1.1.1.1,a,22\n", encoding="utf-8")
    created = core.backup_before_upgrade()
    assert {p.name for p in created} == {"config.before-v2.json", "devices.before-v2.csv"}
    assert core.backup_before_upgrade() == []


def run() -> int:
    failures = 0
    for fn in TESTS:
        with tempfile.TemporaryDirectory() as folder:
            started = time.perf_counter()
            try:
                fn(Path(folder))
                print(f"  ok    {fn.__name__} ({(time.perf_counter() - started) * 1000:.0f} ms)")
            except Exception:
                failures += 1
                print(f"  FAIL  {fn.__name__}")
                traceback.print_exc()
    print(f"\n{len(TESTS) - failures} of {len(TESTS)} tests passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
