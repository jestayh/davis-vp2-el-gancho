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
                 "recoveries": 1, "err_display": 0, "sys_min_kb": 22.5, "sheets_error": None, "ssid": "2.4G_Casa",
                 "ota_route": "Proxy HTTP"}
    h.sent()
    h.tick()                                          # RAM 60000
    r = h.report()
    assert r["err_console"] == 0 and r["recoveries"] == 0 and r["mem_min_kb"] == 58.6


def test_failed_upload_records_why_and_closes_the_socket(monkeypatch):
    closed = []

    class FakeSocket:
        def settimeout(self, t):
            pass

        def connect(self, addr):
            pass

        def write(self, b):
            raise OSError(116)                                # ETIMEDOUT while sending

        def close(self):
            closed.append(1)

    class FakeSocketModule:
        @staticmethod
        def getaddrinfo(host, port):
            return [(2, 1, 0, "", ("1.2.3.4", port))]

        socket = FakeSocket

    monkeypatch.setattr(gs, "IS_ESP32", True)
    monkeypatch.setitem(sys.modules, "socket", FakeSocketModule)
    import json
    monkeypatch.setitem(sys.modules, "ujson", json)
    gs.LAST_ERROR = None
    assert gs._post_json("http://proxy.example/", {"a": 1}) is False
    assert gs.LAST_ERROR.startswith("OSError") and closed


def test_backup_route_when_the_proxy_cannot_be_reached(monkeypatch):
    tried = []
    up = {"http://proxy.example/": False, "http://relay.example/": True}
    monkeypatch.setattr(gs, "RELAY_URL", "http://relay.example/")
    monkeypatch.setattr(gs, "USING_RELAY", False)
    monkeypatch.setattr(gs, "_post_json", lambda url, p, t=12: tried.append(url) or up[url])
    assert gs.upload_to_google_sheets("http://proxy.example/", {"timestamp": "2026-10-30 16:00:00"})
    assert tried == ["http://proxy.example/", "http://relay.example/"] and gs.USING_RELAY
    # While on the backup, the unreachable proxy costs no timeout per row
    tried.clear()
    assert gs.upload_status("http://proxy.example/", {}, "2026-10-30 16:10:05")
    assert tried == ["http://relay.example/"]
    # Back to the proxy once the backup fails and the proxy answers
    up.update({"http://proxy.example/": True, "http://relay.example/": False})
    tried.clear()
    assert gs.upload_to_google_sheets("http://proxy.example/", {})
    assert tried == ["http://relay.example/", "http://proxy.example/"] and not gs.USING_RELAY
    up["http://proxy.example/"] = False
    assert not gs.upload_to_google_sheets("http://proxy.example/", {})


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
