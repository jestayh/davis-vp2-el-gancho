"""Every firmware file compiles to MicroPython bytecode, exactly as ota_publish.py does it"""

import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _published_sources():
    import ast
    with open(os.path.join(ROOT, "ota_publish.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "FILES_TO_COMPILE":
            return [src for src, _ in ast.literal_eval(node.value)]
    raise AssertionError("FILES_TO_COMPILE no encontrado en ota_publish.py")


@pytest.mark.skipif(shutil.which("mpy-cross") is None, reason="mpy-cross no instalado")
@pytest.mark.parametrize("src", _published_sources())
def test_compiles_for_esp32(src, tmp_path):
    res = subprocess.run(["mpy-cross", "-O2", os.path.join(ROOT, src), "-o", str(tmp_path / "out.mpy")],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
