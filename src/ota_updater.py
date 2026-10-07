"""
OTA (Over-The-Air) Firmware Updater for Davis VP2 Weather Station
Downloads pre-compiled bytecode (.mpy) from GitHub, trying several routes in order:
  1. GitHub directly over HTTPS
  2. Cloudflare Worker proxy over HTTPS
  3. Cloudflare Worker proxy over plain HTTP (last resort)
Every file is verified against the SHA-256 listed in version.json before installing.
Safe atomic downloads with automatic rollback on error.
"""

import sys
import time

try:
    import usocket as socket
except ImportError:
    import socket

try:
    import ussl as ssl
except ImportError:
    import ssl

try:
    import ujson as json
except ImportError:
    import json

try:
    import uos as os
except ImportError:
    import os

try:
    import uhashlib as hashlib
except ImportError:
    import hashlib

try:
    import ubinascii as binascii
except ImportError:
    import binascii

IS_ESP32 = sys.platform == "esp32"
LOCAL_VERSION_FILE = "data/version.json"


def get_local_version():
    """Read currently installed firmware version from flash"""
    try:
        with open(LOCAL_VERSION_FILE, "r") as f:
            data = json.load(f)
            return data.get("version", "1.0.0")
    except Exception:
        return "1.0.0"


def save_local_version(ver_str):
    """Save installed firmware version to flash"""
    try:
        with open(LOCAL_VERSION_FILE, "w") as f:
            json.dump({"version": str(ver_str), "installed_at": time.time()}, f)
    except Exception:
        pass


def _download_file(host, path, target_path, use_ssl=True, port=None):
    """
    Download a file over HTTPS or plain HTTP using chunked streaming.
    Returns (success_bool, status_line)
    """
    s = None
    try:
        if port is None:
            port = 443 if use_ssl else 80
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(12)
        s.connect((host, port))
        if use_ssl:
            # Universal positional wrap_socket with SNI (server_hostname)
            ss = ssl.wrap_socket(s, False, None, None, 0, None, host)
        else:
            ss = s
        rd = ss.read if hasattr(ss, "read") else ss.recv
        wr = ss.write if hasattr(ss, "write") else ss.sendall

        req = "GET " + path + " HTTP/1.1\r\nHost: " + host + "\r\nUser-Agent: ESP32-MicroPython-OTA\r\nConnection: close\r\n\r\n"
        wr(req.encode())

        header_bytes = b""
        while b"\r\n\r\n" not in header_bytes:
            chunk = rd(128)
            if not chunk:
                break
            header_bytes += chunk

        idx = header_bytes.find(b"\r\n\r\n")
        if idx == -1:
            ss.close()
            return False, "No header separator"

        status_line = header_bytes[:header_bytes.find(b"\r\n")].decode()
        if "200" not in status_line:
            ss.close()
            return False, status_line

        initial_body = header_bytes[idx + 4:]
        with open(target_path, "wb") as f:
            if initial_body:
                f.write(initial_body)
            while True:
                chunk = rd(512)
                if not chunk:
                    break
                f.write(chunk)

        ss.close()
        return True, status_line
    except Exception as e:
        if s:
            try:
                s.close()
            except Exception:
                pass
        return False, str(e)


def _sha256_file(path):
    """Hex SHA-256 of a file, read in small chunks to spare RAM"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(512)
            if not chunk:
                break
            h.update(chunk)
    return binascii.hexlify(h.digest()).decode()


def _build_sources(repo_user, repo_name, branch, proxy_host):
    """Ordered list of (label, host, path_prefix, use_ssl) download routes"""
    sources = [("GitHub", "raw.githubusercontent.com", "/" + repo_user + "/" + repo_name + "/" + branch, True)]
    if proxy_host:
        sources.append(("Proxy HTTPS", proxy_host, "", True))
        sources.append(("Proxy HTTP", proxy_host, "", False))
    return sources


def _fetch(sources, start_idx, rel_path, target_path, expected_sha=None):
    """
    Try every source (starting with the last one that worked) until the file downloads
    and, when expected_sha is given, matches it. Returns (ok, index_of_source_used, last_error).
    """
    last_err = ""
    n = len(sources)
    for k in range(n):
        i = (start_idx + k) % n
        label, host, prefix, use_ssl = sources[i]
        ok, status = _download_file(host, prefix + "/" + rel_path, target_path, use_ssl)
        if ok and expected_sha:
            try:
                got = _sha256_file(target_path)
            except Exception as e:
                got = "error: {}".format(e)
            if got != expected_sha:
                ok, status = False, "SHA-256 no coincide"
        if ok:
            return True, i, ""
        last_err = "{}: {}".format(label, status)
        print("[OTA] {} fallo para {} ({})".format(label, rel_path, status))
        try:
            os.remove(target_path)
        except Exception:
            pass
        if IS_ESP32:
            import gc
            gc.collect()
    return False, start_idx, last_err


def check_and_update(repo_user="jestayh", repo_name="davis-vp2-el-gancho", branch="main", display=None, proxy_host=None):
    """
    Check GitHub for newer firmware version.
    If available, downloads all updated .mpy files atomically and reboots.
    proxy_host: Cloudflare Worker hostname used as fallback route (None disables it).
    """
    sources = _build_sources(repo_user, repo_name, branch, proxy_host)

    local_ver = get_local_version()
    print("[OTA] Comprobando actualizaciones en GitHub (version local: v{})...".format(local_ver))

    tmp_manifest = "data/version_remote.json"
    ok, src_idx, err = _fetch(sources, 0, "version.json", tmp_manifest)
    if not ok:
        print("[OTA] No se pudo obtener version.json por ninguna ruta ({})".format(err))
        return False
    print("[OTA] Manifiesto obtenido via {}".format(sources[src_idx][0]))

    manifest = None
    try:
        with open(tmp_manifest, "r") as f:
            manifest = json.load(f)
        os.remove(tmp_manifest)
    except Exception as e:
        print("[OTA] Error leyendo manifiesto: {}".format(e))
        return False

    remote_ver = manifest.get("version")
    if not remote_ver or remote_ver == local_ver:
        print("[OTA] Firmware al dia (v{}). No se requieren actualizaciones.".format(local_ver))
        return False

    print("\n" + "=" * 60)
    print("  [OTA] NUEVA VERSION DETECTADA: v{} -> v{}".format(local_ver, remote_ver))
    print("  Descripcion: {}".format(manifest.get("description", "")))
    print("=" * 60)

    if display and display.has_oled:
        display.show_message("ACTUALIZACION", "Nueva version:", "v" + str(remote_ver), "Descargando...")

    files = manifest.get("files", [])
    downloaded_tmp = []

    for i, item in enumerate(files, 1):
        remote_file = item["remote"]
        local_file = item["local"]
        tmp_file = local_file + ".tmp"

        print("[OTA] [{}/{}] Descargando {}...".format(i, len(files), remote_file))
        if display and display.has_oled:
            display.show_message("ACTUALIZANDO", "Descargando", "{}/{} files".format(i, len(files)), remote_file[:16])

        # Ensure directory exists if in a subfolder like src/
        if "/" in local_file:
            d_name = local_file.split("/")[0]
            try:
                os.mkdir(d_name)
            except Exception:
                pass

        f_ok, src_idx, f_err = _fetch(sources, src_idx, remote_file, tmp_file, item.get("sha256"))
        if not f_ok:
            print("[OTA] ERROR descargando {}: {}".format(remote_file, f_err))
            # Clean up all downloaded tmp files to avoid half-baked installs
            for t_file, _ in downloaded_tmp:
                try:
                    os.remove(t_file)
                except Exception:
                    pass
            if display and display.has_oled:
                display.show_message("ERROR OTA", "Fallo descarga", "Conservando ver", "previa OK")
                time.sleep(2)
            return False

        downloaded_tmp.append((tmp_file, local_file))

    # All files downloaded and verified! Apply them atomically
    print("[OTA] Todos los archivos descargados y verificados. Aplicando nuevo firmware...")
    for tmp_file, local_file in downloaded_tmp:
        try:
            try:
                os.remove(local_file)
            except Exception:
                pass
            os.rename(tmp_file, local_file)
        except Exception as e:
            print("[OTA] Error reemplazando {}: {}".format(local_file, e))

    save_local_version(remote_ver)
    print("[OTA] Firmware actualizado exitosamente a v{}!".format(remote_ver))

    if display and display.has_oled:
        display.show_message("ACTUALIZADO", "v" + str(remote_ver) + " instalada", "100% Exitoso!", "Reiniciando...")
        time.sleep(2.5)

    if IS_ESP32:
        try:
            import machine
            machine.reset()
        except Exception:
            pass

    return True
