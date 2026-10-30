"""Backup route on Google App Engine (appengine/main.py): same answers as the Cloudflare Workers"""

import importlib.util
import io
import pathlib

import pytest


@pytest.fixture
def relay(monkeypatch):
    monkeypatch.setenv("APPS_SCRIPT_URL", "https://script.example/exec")
    path = pathlib.Path(__file__).resolve().parent.parent / "appengine" / "main.py"
    spec = importlib.util.spec_from_file_location("appengine_relay", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def call(mod, method, path, body=b""):
    out = {}

    def start_response(status, headers):
        out["status"], out["headers"] = status, dict(headers)

    env = {"REQUEST_METHOD": method, "PATH_INFO": path, "CONTENT_LENGTH": str(len(body)),
           "wsgi.input": io.BytesIO(body)}
    out["body"] = b"".join(mod.app(env, start_response))
    return out


def test_row_answer_200_only_when_google_stored_it(relay, monkeypatch):
    sent = []
    answers = iter([(True, "OK"), (False, "GOOGLE 200: ERROR: hoja bloqueada")])
    monkeypatch.setattr(relay, "store_row", lambda body: sent.append(body) or next(answers))
    monkeypatch.setattr(relay, "latest_firmware_version", lambda: "1.0.24")
    r = call(relay, "POST", "/", b'{"timestamp": "2026-10-30 16:00:00"}')
    assert r["status"].startswith("200") and r["headers"]["X-Davis-Fw"] == "1.0.24"
    assert sent == [b'{"timestamp": "2026-10-30 16:00:00"}']
    r = call(relay, "POST", "/", b"{}")
    assert r["status"].startswith("502") and r["body"].startswith(b"GOOGLE")


def test_firmware_files_only_as_binary_with_length(relay, monkeypatch):
    monkeypatch.setattr(relay, "_get", lambda url, timeout: b'{"version": "1.0.24"}')
    r = call(relay, "GET", "/version.json")
    assert r["status"].startswith("200") and r["headers"]["Content-Type"] == "application/octet-stream"
    assert r["headers"]["Content-Length"] == str(len(r["body"]))
    assert call(relay, "GET", "/src/display.mpy")["status"].startswith("200")
    # Anything else is not proxied (not an open proxy)
    assert call(relay, "GET", "/config.py")["body"] == b"OK"
    assert call(relay, "GET", "/src/../config.py")["body"] == b"OK"
