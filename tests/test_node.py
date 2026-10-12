"""Runs the Node.js tests (Google Apps Script and Cloudflare Worker) so `pytest tests` covers them too"""

import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js no instalado")
@pytest.mark.parametrize("script", ["apps_script/test_apps_script.js", "cloudflare/test_sheets_worker.js"])
def test_node(script):
    res = subprocess.run(["node", os.path.join(HERE, script)], capture_output=True, text=True, encoding="utf-8")
    failed = [line for line in res.stdout.splitlines() if line.startswith("FAIL")]
    assert res.returncode == 0, "\n".join(failed) or res.stdout + res.stderr
