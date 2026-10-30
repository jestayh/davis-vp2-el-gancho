"""
Davis VP2 "El Gancho" - ruta de respaldo en Google App Engine (fuera de Cloudflare)

Hace lo mismo que los dos Workers de cloudflare/, para cuando la red de la casa no alcanza
*.workers.dev (corte intermitente desde el 2026-10-28):

  POST /                       -> entrega la fila a Google Apps Script; 200 solo si Google la
                                  guardo (si no, 502 y el ESP32 la recupera de la consola).
                                  Cabecera X-Davis-Fw con la ultima version publicada.
  GET  /version.json, /app.mpy,
       /src/<nombre>.mpy       -> archivos de firmware del repositorio (OTA), como binario.

App Engine atiende tambien por HTTP simple (el ESP32 no tiene RAM para TLS).
La URL de Apps Script va en secret.yaml (fuera del repositorio, ver app.yaml).
"""

import os
import re
import time
import urllib.request

ORIGIN = "https://raw.githubusercontent.com/jestayh/davis-vp2-el-gancho/main"
ALLOWED = re.compile(r"^/(version\.json|app\.mpy|src/[A-Za-z0-9_]+\.mpy|tools/ota_rescue\.py)$")
GOOGLE_URL = os.environ.get("APPS_SCRIPT_URL", "")

_fw_cache = {"t": 0.0, "v": None}


def _get(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": "davis-relay"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def latest_firmware_version():
    """Version published on GitHub, cached 5 min; None if it can't be read within 2 s"""
    if time.time() - _fw_cache["t"] < 300:
        return _fw_cache["v"]
    v = None
    try:
        import json
        v = json.loads(_get(ORIGIN + "/version.json", 2)).get("version")
        if not (isinstance(v, str) and re.fullmatch(r"[0-9.]{1,16}", v)):
            v = None
    except Exception:
        pass
    _fw_cache.update(t=time.time(), v=v)
    return v


def store_row(body):
    """POST the row to Apps Script. urllib follows its 302 with a GET, which returns the answer."""
    req = urllib.request.Request(GOOGLE_URL, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            answer = r.read().decode("utf-8", "replace").strip()
            status = r.status
    except Exception as e:
        return False, "ERR {}".format(e)
    # Same rule as sheets_worker.js: "SUCCESS" stored, "IGNORED" rejected on purpose (final)
    if status == 200 and (answer.startswith("SUCCESS") or answer.startswith("IGNORED")):
        return True, "OK"
    return False, "GOOGLE {}: {}".format(status, answer[:200])


def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/")

    if method == "POST":
        if not GOOGLE_URL:
            body, status, headers = b"Falta APPS_SCRIPT_URL", "500 Internal Server Error", []
        else:
            size = int(environ.get("CONTENT_LENGTH") or 0)
            ok, msg = store_row(environ["wsgi.input"].read(size))
            if not ok:
                print("Google no guardo la fila (el ESP32 la recuperara de la consola): " + msg)
            body, status = msg.encode(), "200 OK" if ok else "502 Bad Gateway"
            fw = latest_firmware_version()
            headers = [("X-Davis-Fw", fw)] if fw else []
    elif method == "GET" and ALLOWED.match(path):
        try:
            body, status = _get(ORIGIN + path, 20), "200 OK"
        except Exception as e:
            body, status = "Upstream error {}".format(e).encode(), "502 Bad Gateway"
        # Binary with Content-Length: the ESP32 reads the body raw and doesn't understand chunked
        headers = [("Cache-Control", "no-store")]
    elif method == "GET":
        body, status, headers = b"OK", "200 OK", []
    else:
        body, status, headers = b"Method not allowed", "405 Method Not Allowed", []

    start_response(status, headers + [("Content-Type", "application/octet-stream"),
                                      ("Content-Length", str(len(body))),
                                      ("Connection", "close")])
    return [body]
