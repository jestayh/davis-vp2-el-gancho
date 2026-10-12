"""Runs a few cycles of main.main() on the PC with the console, uploads and clock simulated"""

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    import main
    import src.data_logger as dl
    monkeypatch.setattr(dl, "LAST_SYNC_FILE", str(tmp_path / "data" / "last_sync.txt"))

    clock = {"t": 1791800000.0}                       # 2026-10-12 local afternoon

    def fake_time():
        return clock["t"]

    def fake_sleep(s):
        clock["t"] += s

    monkeypatch.setattr(main.time, "time", fake_time)
    monkeypatch.setattr(main.time, "sleep", fake_sleep)
    reading = {"temp_out_c": 15.3, "humidity_out": 72, "pressure_hpa": 1013.2, "wind_speed_kmh": 3.2,
               "rain_day_mm": 0.0, "forecast_icons": 8, "forecast_rule": 1}

    def query_station(*a, **k):
        clock["t"] += 1
        return dict(reading, timestamp=main.format_timestamp_local())

    calls = {"sheets": [], "status": [], "wu": 0}
    monkeypatch.setattr(main, "query_station", query_station)
    monkeypatch.setattr(main, "upload", lambda *a, **k: calls.__setitem__("wu", calls["wu"] + 1) or True)
    monkeypatch.setattr(main, "upload_to_google_sheets", lambda url, rec: calls["sheets"].append(dict(rec)) or True)
    monkeypatch.setattr(main, "upload_status", lambda url, st, ts: calls["status"].append((st, ts)) or True)
    monkeypatch.setattr(main, "GOOGLE_APPS_SCRIPT_URL", "http://fake")
    monkeypatch.setattr(main, "run_console_clock_sync", lambda: 86400)
    monkeypatch.setattr(main, "run_gap_recovery", lambda until: (True, False))
    monkeypatch.setattr(main, "read_wind_samples", lambda slot: 221)
    return main, calls, clock


def test_main_loop_logs_uploads_and_reports(app):
    main, calls, clock = app
    main.main(max_cycles=200)                         # ~17 simulated minutes
    assert calls["wu"] > 0
    assert len(calls["sheets"]) >= 1
    rec = calls["sheets"][0]
    assert rec["timestamp"].endswith(":00") and int(rec["timestamp"][14:16]) % 5 == 0 and rec["wind_samples"] == 221 and rec["iss_recept"] == 98.2
    # First health report right after the first successful upload, not repeated within the day
    assert len(calls["status"]) == 1
    status, ts = calls["status"][0]
    assert status["fw"] and status["err_console"] == 0 and ts[:4] == "2026"


def test_stale_py_next_to_mpy_is_removed(tmp_path):
    import main
    for name in ("google_sheets.py", "google_sheets.mpy", "health.mpy", "__init__.py", "config_like.py"):
        (tmp_path / name).write_text("x")
    removed = main.drop_stale_sources(str(tmp_path))
    assert removed == ["google_sheets.py"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["__init__.py", "config_like.py", "google_sheets.mpy", "health.mpy"]
    assert main.drop_stale_sources(str(tmp_path / "missing")) == []


def test_reason_of_a_deliberate_restart_is_reported_once(tmp_path):
    import main
    marker = tmp_path / "reset_reason.txt"
    assert main.take_reset_reason(str(marker)) is None
    marker.write_text("Memoria agotada")
    assert main.take_reset_reason(str(marker)) == "Memoria agotada"
    assert not marker.exists() and main.take_reset_reason(str(marker)) is None


def test_restart_after_an_ota_install_is_named_as_such(tmp_path):
    import json
    import main
    pending = tmp_path / "ota_pending.json"
    assert main.ota_update_boot(str(pending)) is None                  # nothing under trial
    pending.write_text(json.dumps({"version": "1.0.19", "previous": "1.0.18", "boots": 1}))
    assert main.ota_update_boot(str(pending)) == "Actualizacion v1.0.19"
    pending.write_text(json.dumps({"version": "1.0.19", "previous": "1.0.18", "boots": 2}))
    assert main.ota_update_boot(str(pending)) == "Actualizacion v1.0.19 (arranque 2)"
