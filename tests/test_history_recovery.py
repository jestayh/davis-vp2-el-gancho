"""Gap recovery and one-time backfill (src/history_recovery.py) against a fake Davis console on localhost"""

import os
import socket
import struct
import sys
import threading
from datetime import datetime, timedelta

import pytest

import src.data_logger as dl
import src.history_recovery as hr
from src.davis_reader import calculate_crc


def make_record(t, i):
    """52-byte Rev B archive record with predictable values"""
    d = t.day + t.month * 32 + (t.year - 2000) * 512
    tt = t.hour * 100 + t.minute
    temp_f10 = int((50 + (i % 30)) * 10)
    rec = struct.pack("<HHhhhHHHHHhBBBBBBB", d, tt, temp_f10, temp_f10 + 5, temp_f10 - 5,
                      (1 if i % 7 == 0 else 0), 0, 29900, 0, 200, 680, 50, 80,
                      i % 10, (i % 10) + 3, 3, 255 if i % 5 == 0 else 4, 255)
    return rec + b"\xff" * (52 - len(rec))


START = datetime(2026, 10, 7, 20, 0)
ARCHIVE = [(START + timedelta(minutes=10 * i), make_record(START + timedelta(minutes=10 * i), i)) for i in range(400)]


def _serve(srv):
    """Minimal console: answers wakeups and DMPAFT with paged archive records (with CRC)"""
    while True:
        try:
            c, _ = srv.accept()
        except OSError:
            return
        c.settimeout(5)
        try:
            buf = b""
            while True:
                b = c.recv(64)
                if not b:
                    break
                buf += b
                if buf.endswith(b"\n") and b"DMPAFT" not in buf:
                    c.sendall(b"\n\r")
                    buf = b""
                    continue
                if b"DMPAFT\n" in buf:
                    c.sendall(b"\x06")
                    dt = b""
                    while len(dt) < 6:
                        dt += c.recv(6 - len(dt))
                    d, tt = struct.unpack("<HH", dt[:4])
                    since = datetime((d >> 9) + 2000, (d >> 5) & 0xF, d & 0x1F, tt // 100, tt % 100)
                    recs = [r for (ts, r) in ARCHIVE if ts > since]
                    npages = (len(recs) + 4) // 5
                    hdr = struct.pack("<HH", npages, 0)
                    c.sendall(b"\x06" + hdr + struct.pack(">H", calculate_crc(hdr)))
                    for p in range(npages):
                        if c.recv(1) != b"\x06":
                            break
                        chunk = recs[p * 5:(p + 1) * 5]
                        chunk += [b"\xff" * 52] * (5 - len(chunk))
                        body = bytes([p % 256]) + b"".join(chunk) + b"\x00" * 4
                        c.sendall(body + struct.pack(">H", calculate_crc(body)))
                    break
        except Exception:
            pass
        finally:
            c.close()


@pytest.fixture(scope="module")
def console():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    threading.Thread(target=_serve, args=(srv,), daemon=True).start()
    yield ("127.0.0.1", srv.getsockname()[1])
    srv.close()


@pytest.fixture
def station(console, tmp_path, monkeypatch):
    """history_recovery wired to the fake console, a temp data dir and a recording uploader"""
    monkeypatch.chdir(tmp_path)
    os.makedirs("data")
    monkeypatch.setattr(dl, "LAST_SYNC_FILE", str(tmp_path / "last_sync.txt"))
    monkeypatch.setattr(hr, "DAVIS_HOST", console[0])
    monkeypatch.setattr(hr, "DAVIS_PORT", console[1])
    monkeypatch.setattr(hr, "GOOGLE_APPS_SCRIPT_URL", "http://fake")
    monkeypatch.setattr(hr, "ENABLE_DMPAFT_RECOVERY", True)
    monkeypatch.setattr(hr, "log_reading_csv", lambda *a, **k: True)
    monkeypatch.setattr(hr.time, "sleep", lambda s: None)

    class Uploads(list):
        fail_after = None

    uploads = Uploads()

    def upload(url, rec):
        if uploads.fail_after is not None and len(uploads) >= uploads.fail_after:
            return False
        uploads.append(dict(rec))
        return True

    monkeypatch.setattr(hr, "upload_to_google_sheets", upload)
    return uploads


def last_sync():
    return open(dl.LAST_SYNC_FILE).read()


def test_dmpaft_fetch_respects_bounds(station):
    raw = hr.dmpaft_fetch_raw(hr.DAVIS_HOST, hr.DAVIS_PORT, (2026, 10, 7, 23, 50), (2026, 10, 8, 23, 50), max_records=160)
    assert len(raw) == 144
    assert raw[0][0] == (2026, 10, 8, 0, 0) and raw[-1][0] == (2026, 10, 8, 23, 50)


def test_parse_archive_record(station):
    raw = hr.dmpaft_fetch_raw(hr.DAVIS_HOST, hr.DAVIS_PORT, (2026, 10, 7, 23, 50), (2026, 10, 8, 1, 30))
    p = hr.parse_archive_record(raw[0][1], raw[0][0])
    assert p["timestamp"] == "2026-10-08 00:00:00"
    assert p["timestamp_utc"] == "2026-10-08T03:00:00Z"
    assert p["rain_day_mm"] is None and p["storm_rain_mm"] is None      # totals come from the sheet
    assert p["wind_samples"] == 200 and p["iss_recept"] == 88.9
    calm = [hr.parse_archive_record(r, t) for t, r in raw[:10]]
    assert any(c["wind_direction_deg"] is None for c in calm)           # 255 = calm, no direction


def test_gap_recovery_in_chunks(station):
    dl.update_last_sync_timestamp("2026-10-08 10:30:00")
    for _ in range(10):
        done, _ = hr.recover_gap((2026, 10, 9, 20, 40))
        if done:
            break
    ts = [u["timestamp"] for u in station]
    assert done
    assert ts[0] == "2026-10-08 10:40:00" and ts[-1] == "2026-10-09 20:30:00"
    assert len(ts) == len(set(ts)) == 204          # 10:40 to 20:30 next day, every 10 min
    assert last_sync().startswith("2026-10-09 20:3")


def test_interrupted_recovery_resumes_where_it_stopped(station):
    dl.update_last_sync_timestamp("2026-10-08 10:30:00")
    station.fail_after = 5
    done, progressed = hr.recover_gap((2026, 10, 8, 14, 0))
    assert not done and progressed and last_sync() == "2026-10-08 11:20:00"
    station.fail_after = None
    done, _ = hr.recover_gap((2026, 10, 8, 14, 0))
    assert done and station[-1]["timestamp"] == "2026-10-08 13:50:00" and len(station) == 20


def test_invalid_dates_never_reach_last_sync(station, tmp_path):
    with open(dl.LAST_SYNC_FILE, "w") as f:
        f.write("1999-12-31 21:10:00")
    assert dl.get_last_csv_timestamp(str(tmp_path / "none.csv")) is None
    dl.update_last_sync_timestamp("2000-01-01 04:50:00")
    assert last_sync().startswith("1999")


def test_backfill_jobs_run_once_and_skip_overwritten_windows(station):
    assert hr.console_backfill_pending()
    assert hr.run_console_backfill() is True
    # 08-oct window: the whole console day goes to the Consola tab with a prefix
    assert len(station) == 144 and all(u["destino"] == "consola" for u in station)
    assert station[0]["timestamp"] == "CONSOLA 2026-10-08 00:00:00"
    # The other windows are older or newer than this fake archive: done without uploading
    assert not hr.console_backfill_pending()
    assert hr.run_console_backfill() is True and len(station) == 144


def test_backfill_retries_when_console_does_not_answer(station, monkeypatch):
    monkeypatch.setattr(hr, "dmpaft_fetch_raw", lambda *a, **k: None)
    assert hr.run_console_backfill() is False and hr.console_backfill_pending()


def test_read_wind_samples_only_from_the_exact_record(station, monkeypatch):
    rec = bytearray(52)
    struct.pack_into("<H", rec, 18, 212)
    calls = []

    def fetch(host, port, since, until, max_records=0, timeout=0):
        calls.append((since, until))
        return fetch.result

    monkeypatch.setattr(hr, "dmpaft_fetch_raw", fetch)
    slot = (2026, 10, 11, 22, 10)
    fetch.result = [(slot, bytes(rec))]
    assert hr.read_wind_samples(slot) == 212 and calls[0] == ((2026, 10, 11, 22, 0), slot)
    fetch.result = [((2026, 10, 11, 22, 0), bytes(rec))]      # this slot not written yet
    assert hr.read_wind_samples(slot) is None
    fetch.result = None
    assert hr.read_wind_samples(slot) is None
    assert hr.read_wind_samples((2026, 11, 1, 0, 0)) is None and calls[-1][0] == (2026, 10, 31, 23, 50)


def test_upload_payload_keeps_empty_values_empty(monkeypatch):
    import src.google_sheets as gs
    captured = {}

    class FakeRequests:
        @staticmethod
        def post(url, json=None, headers=None, timeout=None):
            captured.update(json)
            return type("R", (), {"status_code": 200})()

    monkeypatch.setitem(sys.modules, "requests", FakeRequests)
    gs.upload_to_google_sheets("http://fake", {"timestamp": "CONSOLA 2026-10-08 00:00:00", "destino": "consola",
                                               "reset_cause": "Encendido", "rain_day_mm": None})
    assert captured["destino"] == "consola" and captured["reset_cause"] == "Encendido"
    assert captured["rain_day_mm"] is None and captured["barometer_trend"] == ""
    # Unknown wind samples / reception stay empty instead of an invented 225 / 100 %
    assert captured["wind_samples"] is None and captured["iss_recept"] is None and captured["wind_tx"] == 1
