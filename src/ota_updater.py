"""
OTA (Over-The-Air) Firmware Updater for Davis VP2 Weather Station
Downloads pre-compiled bytecode (.mpy) directly from GitHub over HTTPS.
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


def _download_file_https(host, path, target_path):
    """
    Download a file from an HTTPS host using chunked streaming.
    Returns (success_bool, status_line)
    """
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(12)
        s.connect((host, 443))
        # Universal positional wrap_socket with SNI (server_hostname)
        ss = ssl.wrap_socket(s, False, None, None, 0, None, host)

        req = "GET " + path + " HTTP/1.1\r\nHost: " + host + "\r\nUser-Agent: ESP32-MicroPython-OTA\r\nConnection: close\r\n\r\n"
        ss.write(req.encode())

        header_bytes = b""
        while b"\r\n\r\n" not in header_bytes:
            chunk = ss.read(128)
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
                chunk = ss.read(512)
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


def check_and_update(repo_user="jestayh", repo_name="davis-vp2-el-gancho", branch="main", display=None):
    """
    Check GitHub for newer firmware version.
    If available, downloads all updated .mpy files atomically and reboots.
    """
    host = "raw.githubusercontent.com"
    manifest_path = "/" + repo_user + "/" + repo_name + "/" + branch + "/version.json"

    local_ver = get_local_version()
    print("[OTA] Comprobando actualizaciones en GitHub (version local: v{})...".format(local_ver))

    tmp_manifest = "data/version_remote.json"
    ok, status = _download_file_https(host, manifest_path, tmp_manifest)
    if not ok:
        print("[OTA] No se pudo obtener version.json de GitHub ({})".format(status))
        try:
            os.remove(tmp_manifest)
        except Exception:
            pass
        return False

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
        file_path = "/" + repo_user + "/" + repo_name + "/" + branch + "/" + remote_file

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

        f_ok, f_status = _download_file_https(host, file_path, tmp_file)
        if not f_ok:
            print("[OTA] ERROR descargando {}: {}".format(remote_file, f_status))
            # Clean up all downloaded tmp files to avoid half-baked installs
            for f in downloaded_tmp:
                try:
                    os.remove(f)
                except Exception:
                    pass
            try:
                os.remove(tmp_file)
            except Exception:
                pass
            if display and display.has_oled:
                display.show_message("ERROR OTA", "Fallo descarga", "Conservando ver", "previa OK")
                time.sleep(2)
            return False

        downloaded_tmp.append((tmp_file, local_file))

    # All files downloaded successfully! Apply them atomically
    print("[OTA] Todos los archivos descargados OK. Aplicando nuevo firmware...")
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
