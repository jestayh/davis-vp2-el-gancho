# Lanzador de la estacion. Se copia a la ESP32 como /main.py (no se actualiza por OTA):
#   mpremote connect COM3 cp device/main.py :main.py
#
# Arranca la aplicacion (app.mpy) y protege contra actualizaciones OTA defectuosas:
# si una version recien instalada se reinicia MAX_BOOTS veces sin que la aplicacion
# confirme un arranque correcto (primera subida exitosa a WU o Sheets), restaura la
# version anterior desde /backup y la marca como defectuosa para no reinstalarla.

import gc
import os
import time
import json
import machine

PENDING_FILE = "data/ota_pending.json"
BAD_VERSION_FILE = "data/ota_bad.json"
VERSION_FILE = "data/version.json"
BACKUP_DIR = "backup"
MAX_BOOTS = 3


def _copy(src, dst):
    with open(src, "rb") as fi:
        with open(dst, "wb") as fo:
            while True:
                chunk = fi.read(512)
                if not chunk:
                    break
                fo.write(chunk)


def _write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)


def check_pending_update():
    try:
        with open(PENDING_FILE, "r") as f:
            st = json.load(f)
    except Exception:
        return  # nothing under trial

    st["boots"] = st.get("boots", 0) + 1
    if st["boots"] <= MAX_BOOTS:
        _write_json(PENDING_FILE, st)
        print("[OTA] v{} a prueba: arranque {}/{} sin confirmar".format(st.get("version"), st["boots"], MAX_BOOTS))
        return

    print("[OTA] v{} fallo {} arranques seguidos. Restaurando v{}...".format(
        st.get("version"), MAX_BOOTS, st.get("previous")))
    backed_up = st.get("backed_up", [])
    for local_file in st.get("files", []):
        try:
            if local_file in backed_up:
                _copy(BACKUP_DIR + "/" + local_file, local_file)
            else:
                os.remove(local_file)  # file did not exist in the previous version
        except Exception as e:
            print("[OTA] Error restaurando {}: {}".format(local_file, e))
    _write_json(VERSION_FILE, {"version": st.get("previous"), "rolled_back_at": time.time()})
    _write_json(BAD_VERSION_FILE, {"version": st.get("version")})
    try:
        os.remove(PENDING_FILE)
    except Exception:
        pass
    print("[OTA] Version anterior restaurada. Reiniciando...")
    time.sleep(2)
    machine.reset()


gc.collect()
try:
    check_pending_update()
except Exception as e:
    print("[OTA] Error revisando actualizacion pendiente:", e)
gc.collect()

try:
    import app
    app.main()
except Exception as e:
    print("FATAL error in app:", e)
    time.sleep(5)
    machine.reset()
