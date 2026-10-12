"""Hardware watchdog (src/watchdog.py) and the long jobs that must keep feeding it"""

import json
import sys
import types

import pytest

from src import watchdog


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(watchdog, "_wdt", None)


def test_does_nothing_on_a_pc(fresh):
    assert watchdog.start() is False
    watchdog.feed()                      # no error


def test_starts_the_esp32_watchdog_once(fresh, monkeypatch):
    made = []

    class WDT:
        def __init__(self, timeout):
            made.append(timeout)
            self.fed = 0

        def feed(self):
            self.fed += 1

    monkeypatch.setattr(sys, "platform", "esp32")
    monkeypatch.setitem(sys.modules, "machine", types.SimpleNamespace(WDT=WDT))
    assert watchdog.start() is True and watchdog.start() is True
    assert made == [watchdog.TIMEOUT_SECONDS * 1000]
    watchdog.feed()
    assert watchdog._wdt.fed == 1


def test_board_refusing_the_watchdog_is_not_fatal(fresh, monkeypatch):
    def WDT(timeout):
        raise ValueError("timeout fuera de rango")

    monkeypatch.setattr(sys, "platform", "esp32")
    monkeypatch.setitem(sys.modules, "machine", types.SimpleNamespace(WDT=WDT))
    assert watchdog.start() is False
    watchdog.feed()


def test_ota_download_feeds_per_route(monkeypatch, tmp_path):
    import src.ota_updater as ota
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    feeds = []
    monkeypatch.setattr(watchdog, "feed", lambda: feeds.append(1))

    def download(host, path, target, use_ssl):
        if host == "raw.githubusercontent.com":
            with open(target, "w") as f:
                json.dump({"version": "1.0.0"}, f)
            return True, "200"
        return False, "timeout"

    monkeypatch.setattr(ota, "_download_file", download)
    monkeypatch.setattr(ota, "get_local_version", lambda: "1.0.0")
    ota.check_and_update(proxy_host="proxy.example")
    assert len(feeds) >= 2               # before each route tried


def test_gap_recovery_feeds_per_record(monkeypatch, tmp_path):
    import src.data_logger as dl
    import src.history_recovery as hr
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(dl, "LAST_SYNC_FILE", str(tmp_path / "last_sync.txt"))
    monkeypatch.setattr(hr, "GOOGLE_APPS_SCRIPT_URL", "http://fake")
    monkeypatch.setattr(hr, "ENABLE_DMPAFT_RECOVERY", True)
    monkeypatch.setattr(hr, "upload_to_google_sheets", lambda url, rec: True)
    monkeypatch.setattr(hr.time, "sleep", lambda s: None)
    recs = [((2026, 10, 8, 10, m), bytes(52)) for m in (40, 50)]
    monkeypatch.setattr(hr, "dmpaft_fetch_raw", lambda *a, **k: list(recs))
    feeds = []
    monkeypatch.setattr(watchdog, "feed", lambda: feeds.append(1))
    dl.update_last_sync_timestamp("2026-10-08 10:30:00")
    hr.recover_gap((2026, 10, 8, 11, 0))
    assert len(feeds) == 2
