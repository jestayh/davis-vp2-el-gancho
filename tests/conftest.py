"""
Shared setup for the tests (run from the repo root: python -m pytest tests).

- The code imports `config`, which is private and not in the repository: the tests always use
  config.example.py instead, so they behave the same on any machine and never touch real services.
- MicroPython's `framebuf` does not exist on a PC: tests/fakes provides a minimal stand-in.
"""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "fakes"))

_spec = importlib.util.spec_from_file_location("config", os.path.join(ROOT, "config.example.py"))
_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_config)
sys.modules["config"] = _config
