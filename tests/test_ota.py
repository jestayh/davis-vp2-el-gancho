"""OTA updater (src/ota_updater.py): route order, remembered route and screen wake on update"""

import json

import pytest

import src.ota_updater as ota


@pytest.fixture
def device(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    return tmp_path


def routes(proxy="proxy.example"):
    return [s[0] for s in ota._build_sources("u", "r", "main", proxy)]


def test_proxy_http_goes_first():
    assert routes() == ["Proxy HTTP", "GitHub", "Proxy HTTPS"]
    assert routes(None) == ["GitHub"]


def test_backup_route_outside_cloudflare_comes_right_after_the_proxy():
    labels = [s[0] for s in ota._build_sources("u", "r", "main", "proxy.example", "relay.example")]
    assert labels == ["Proxy HTTP", "Respaldo HTTP", "GitHub", "Proxy HTTPS"]
    assert ota._build_sources("u", "r", "main", "proxy.example", "relay.example")[1] == \
        ("Respaldo HTTP", "relay.example", "", False)


def test_route_that_worked_is_remembered(device, monkeypatch):
    calls = []

    def download(host, path, target, use_ssl):
        calls.append(host)
        if host == "raw.githubusercontent.com":
            with open(target, "w") as f:
                json.dump({"version": "1.0.0"}, f)
            return True, "200"
        return False, "timeout"

    monkeypatch.setattr(ota, "_download_file", download)
    monkeypatch.setattr(ota, "get_local_version", lambda: "1.0.0")
    ota.check_and_update(proxy_host="proxy.example")
    assert calls == ["proxy.example", "raw.githubusercontent.com"]
    assert (device / "data" / "ota_route.txt").read_text() == "GitHub"
    calls.clear()
    ota.check_and_update(proxy_host="proxy.example")
    assert calls == ["raw.githubusercontent.com"]


def test_new_version_wakes_the_screen(device, monkeypatch):
    def download(host, path, target, use_ssl):
        if path.endswith("version.json"):
            with open(target, "w") as f:
                json.dump({"version": "9.9.9", "files": [{"remote": "app.mpy", "local": "app.mpy", "sha256": "x"}]}, f)
            return True, "200"
        return False, "404"                       # file download fails: nothing gets installed

    class Screen:
        has_oled = False
        woke = None

        def wake(self, seconds):
            self.woke = seconds

    screen = Screen()
    monkeypatch.setattr(ota, "_download_file", download)
    monkeypatch.setattr(ota, "get_local_version", lambda: "1.0.0")
    monkeypatch.setattr(ota.time, "sleep", lambda s: None)
    assert ota.check_and_update(proxy_host="proxy.example", display=screen) is False
    assert screen.woke == 15 * 60


def test_rolled_back_version_is_not_reinstalled(device, monkeypatch):
    def download(host, path, target, use_ssl):
        with open(target, "w") as f:
            json.dump({"version": "2.0.0", "files": []}, f)
        return True, "200"

    monkeypatch.setattr(ota, "_download_file", download)
    monkeypatch.setattr(ota, "get_local_version", lambda: "1.0.0")
    monkeypatch.setattr(ota, "get_bad_version", lambda: "2.0.0")
    assert ota.check_and_update(proxy_host="proxy.example") is False
