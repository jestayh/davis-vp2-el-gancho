"""
OTA Publisher Tool for Davis VP2 "El Gancho" Weather Station
Automatically compiles MicroPython bytecode, bumps firmware version, and pushes to GitHub.

Usage:
    python ota_publish.py "Descripcion de los cambios realizados"
"""

import sys
import os
import json
import datetime
import subprocess

VERSION_FILE = "version.json"

FILES_TO_COMPILE = [
    ("main.py", "app.mpy"),
    ("src/display.py", "src/display.mpy"),
    ("src/davis_reader.py", "src/davis_reader.mpy"),
    ("src/google_sheets.py", "src/google_sheets.mpy"),
    ("src/history_recovery.py", "src/history_recovery.mpy"),
    ("src/ota_updater.py", "src/ota_updater.mpy"),
]


def bump_version(ver_str):
    parts = [int(p) for p in ver_str.split(".")]
    if len(parts) == 3:
        parts[2] += 1
    elif len(parts) == 2:
        parts[1] += 1
    else:
        parts[0] += 1
    return ".".join(str(p) for p in parts)


def main():
    desc = sys.argv[1] if len(sys.argv) > 1 else "Actualizacion general de firmware"

    print("\n" + "=" * 70)
    print("  Davis VP2 'El Gancho' - Publicador de Firmware OTA para GitHub")
    print("=" * 70 + "\n")

    # 1. Leer versión actual
    if os.path.exists(VERSION_FILE):
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            v_data = json.load(f)
    else:
        v_data = {"version": "1.0.0"}

    old_ver = v_data.get("version", "1.0.0")
    new_ver = bump_version(old_ver)
    today_str = datetime.date.today().isoformat()

    print(f"[1/4] Incrementando version: v{old_ver} -> v{new_ver}")
    print(f"      Descripcion: {desc}")

    # 2. Compilar archivos a .mpy
    print("\n[2/4] Compilando codigo fuente a bytecode optimizado (.mpy)...")
    for src, dst in FILES_TO_COMPILE:
        if os.path.exists(src):
            cmd = f"mpy-cross -O2 {src} -o {dst}"
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if res.returncode != 0:
                print(f"  [ERROR] Fallo al compilar {src}: {res.stderr}")
                sys.exit(1)
            size = os.path.getsize(dst)
            print(f"  [OK] {src} -> {dst} ({size} bytes)")
        else:
            print(f"  [OMITIDO] {src} no existe")

    # 3. Guardar version.json
    print("\n[3/4] Actualizando version.json...")
    v_data["version"] = new_ver
    v_data["date"] = today_str
    v_data["description"] = desc
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        json.dump(v_data, f, indent=2, ensure_ascii=False)
    print("  [OK] version.json guardado.")

    # 4. Git commit y push
    print("\n[4/4] Empaquetando y subiendo a GitHub...")
    subprocess.run("git add -A", shell=True, check=True)
    commit_msg = f"release: v{new_ver} - {desc}"
    commit_res = subprocess.run(f'git commit -m "{commit_msg}"', shell=True, capture_output=True, text=True)
    print(commit_res.stdout.strip())

    print("\nEnviando cambios a GitHub (git push origin main)...")
    push_res = subprocess.run("git push origin main", shell=True)
    if push_res.returncode == 0:
        print("\n" + "=" * 70)
        print(f"  EXITO: Firmware v{new_ver} publicado en GitHub!")
        print("  Tu ESP32 detectara la actualizacion, la descargara y se reiniciara solo.")
        print("=" * 70 + "\n")
    else:
        print("\n[AVISO] No se pudo hacer push automaticamente.")
        print("Verifica que el repositorio remoto este configurado con:")
        print("  git remote add origin https://github.com/jestayh/davis-vp2-el-gancho.git")


if __name__ == "__main__":
    main()
