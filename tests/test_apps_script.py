"""Runs the Google Apps Script test (Node.js) so `pytest tests` covers it too"""

import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js no instalado")
def test_google_apps_script():
    res = subprocess.run(["node", os.path.join(HERE, "apps_script", "test_apps_script.js")],
                         capture_output=True, text=True, encoding="utf-8")
    failed = [line for line in res.stdout.splitlines() if line.startswith("FAIL")]
    assert res.returncode == 0, "\n".join(failed) or res.stdout + res.stderr
