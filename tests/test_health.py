"""Daily health report (src/health.py) and its upload (src/google_sheets.upload_status)"""

import sys

import src.google_sheets as gs
import src.health as health


def make(monkeypatch, mem=None):
    clock = {"ms": 5000}
    monkeypatch.setattr(health, "_ticks", lambda: clock["ms"])
    monkeypatch.setattr(health, "_mem_free", lambda: mem.pop(0) if mem else None)
    monkeypatch.setattr(health, "_rssi", lambda: -67)
    monkeypatch.setattr(health, "_ssid", lambda: "2.4G_Casa")
    monkeypatch.setattr(health, "_ota_route", lambda: "Proxy HTTP")
    return health.Health(fw="1.0.15", reset_cause="Watchdog"), clock


def test_first_report_right_after_boot_then_daily(monkeypatch):
    h, clock = make(monkeypatch)
    assert h.due()
    h.sent()
    clock["ms"] += 23 * 3600 * 1000
    h.tick()
    assert not h.due()
    clock["ms"] += 3600 * 1000
    h.tick()
    assert h.due() and h.report()["uptime_h"] == 24.0


def test_counters_and_lowest_ram_start_over_after_each_report(monkeypatch):
    h, clock = make(monkeypatch, mem=[80000, 51200, 70000, 60000])
    h.tick()
    h.tick()
    h.err_console += 2
    h.err_sheets += 1
    h.recoveries += 1
    h.sys_mem(32000)
    h.sys_mem(23040)                                  # lowest free system memory: 22.5 KB
    h.sys_mem(None)
    r = h.report()                                    # samples current RAM: 70000
    assert r == {"fw": "1.0.15", "uptime_h": 0.0, "reset_cause": "Watchdog", "mem_free_kb": 68.4,
                 "mem_min_kb": 50.0, "rssi": -67, "err_console": 2, "err_sheets": 1, "err_wu": 0,
                 "recoveries": 1, "err_display": 0, "sys_min_kb": 22.5, "ssid": "2.4G_Casa",
                 "ota_route": "Proxy HTTP"}
    h.sent()
    h.tick()                                          # RAM 60000
    r = h.report()
    assert r["err_console"] == 0 and r["recoveries"] == 0 and r["mem_min_kb"] == 58.6


def test_ota_route_read_from_the_updater_file(tmp_path):
    f = tmp_path / "ota_route.txt"
    f.write_text("Proxy HTTP\n")
    assert health._ota_route(str(f)) == "Proxy HTTP"
    assert health._ota_route(str(tmp_path / "missing.txt")) is None


def test_no_ram_or_wifi_data_on_a_pc():
    h = health.Health()
    h.tick()
    r = h.report()
    assert r["mem_free_kb"] is None and r["mem_min_kb"] is None and r["rssi"] is None


def test_status_upload_is_tagged_so_old_scripts_reject_it(monkeypatch):
    captured = {}

    class FakeRequests:
        @staticmethod
        def post(url, json=None, headers=None, timeout=None):
            captured.update(json)
            return type("R", (), {"status_code": 200})()

    monkeypatch.setitem(sys.modules, "requests", FakeRequests)
    assert gs.upload_status("http://fake", {"fw": "1.0.15", "err_wu": 3}, "2026-10-12 12:00:05")
    assert captured == {"fw": "1.0.15", "err_wu": 3, "destino": "estado", "timestamp": "ESTADO 2026-10-12 12:00:05"}
    assert gs.upload_status("", {}, "2026-10-12 12:00:05") is False
