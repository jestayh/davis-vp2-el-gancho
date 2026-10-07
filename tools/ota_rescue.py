# Instalador de emergencia del firmware (MicroPython, ESP32).
#
# Para cuando el OTA normal no logra conectarse a GitHub. Descarga version.json y
# los archivos .mpy desde el proxy de Cloudflare por HTTP (sin TLS, casi sin RAM),
# verifica la huella SHA-256 de cada uno y solo instala si TODOS coinciden.
#
# Uso: con la ESP32 conectada por USB (PC o celular con OTG), presionar Ctrl+C
# hasta ver ">>>" y pegar la linea de docs/ACTUALIZAR_DESDE_CELULAR.md, que
# descarga este archivo y lo ejecuta.

import gc
import os
import time
import socket
import machine

try:
    import ujson as json
except ImportError:
    import json
try:
    import uhashlib as hashlib
except ImportError:
    import hashlib
try:
    import ubinascii as binascii
except ImportError:
    import binascii

HOST = "davis-ota-proxy.estayh-jose.workers.dev"


def _open(path):
    """GET over plain HTTP; returns the socket positioned at the start of the body"""
    s = socket.socket()
    s.settimeout(20)
    s.connect(socket.getaddrinfo(HOST, 80)[0][-1])
    s.write(b"GET /" + path.encode() + b" HTTP/1.0\r\nHost: " + HOST.encode() +
            b"\r\nUser-Agent: ESP32-MicroPython-OTA\r\nConnection: close\r\n\r\n")
    status = s.readline()
    if b" 200 " not in status:
        s.close()
        raise OSError("HTTP " + status.decode().strip() + " en /" + path)
    while True:
        line = s.readline()
        if not line or line == b"\r\n":
            break
    return s


def _download(path, target):
    """Stream a file to flash; returns (size, sha256 hex)"""
    s = _open(path)
    h = hashlib.sha256()
    size = 0
    with open(target, "wb") as f:
        while True:
            chunk = s.read(512)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            size += len(chunk)
    s.close()
    return size, binascii.hexlify(h.digest()).decode()


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def run():
    # Drop the stopped station app from RAM (it is reloaded after the reboot)
    import sys
    for name in list(sys.modules):
        if name == "app" or name == "config" or name.startswith("src"):
            del sys.modules[name]
    gc.collect()
    print("\n=== INSTALADOR DE EMERGENCIA ===")
    print("RAM libre:", gc.mem_free())

    s = _open("version.json")
    manifest = json.loads(s.read().decode())
    s.close()
    files = manifest.get("files", [])
    print("Version disponible: v{} ({} archivos)".format(manifest.get("version"), len(files)))
    if not files or any("sha256" not in f for f in files):
        print("ERROR: el manifiesto no trae huellas SHA-256. No se instala nada.")
        return

    downloaded = []
    for i, item in enumerate(files, 1):
        remote, local = item["remote"], item["local"]
        tmp = local + ".new"
        print("[{}/{}] Descargando {}...".format(i, len(files), remote))
        try:
            size, sha = _download(remote, tmp)
        except Exception as e:
            sha, size = None, 0
            print("   error de descarga:", e)
        if sha != item["sha256"]:
            print("   ERROR: el archivo no coincide con su huella SHA-256.")
            print("No se instalo nada. La estacion sigue con la version anterior.")
            _remove(tmp)
            for t, _ in downloaded:
                _remove(t)
            return
        print("   OK ({} bytes, huella verificada)".format(size))
        downloaded.append((tmp, local))
        gc.collect()

    print("Todo verificado. Instalando...")
    for tmp, local in downloaded:
        _remove(local)
        os.rename(tmp, local)
    with open("data/version.json", "w") as f:
        json.dump({"version": manifest.get("version"), "installed_at": time.time()}, f)

    print("=== LISTO: v{} instalada. Reiniciando en 3 segundos... ===".format(manifest.get("version")))
    time.sleep(3)
    machine.reset()


run()
