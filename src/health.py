"""
Daily health report of the ESP32, sent to the "Estado" tab of the Google Sheet.

Counts what went wrong since the last report (console not answering, failed uploads, gap
recoveries) and samples free RAM, so problems at the station show up before they cost data.
The first report goes out right after boot (so every restart leaves a trace), then one a day.
"""

import sys
import time

IS_ESP32 = sys.platform == "esp32"
REPORT_EVERY_SECONDS = 24 * 3600


def _ticks():
    """Monotonic milliseconds: NTP setting the clock at boot does not move it"""
    return time.ticks_ms() if IS_ESP32 else int(time.time() * 1000)


def _ticks_diff(a, b):
    return time.ticks_diff(a, b) if IS_ESP32 else a - b


def _mem_free():
    if not IS_ESP32:
        return None
    try:
        import gc
        return gc.mem_free()
    except Exception:
        return None


def _rssi():
    if not IS_ESP32:
        return None
    try:
        import network
        return network.WLAN(network.STA_IF).status("rssi")
    except Exception:
        return None


def _kb(n):
    return round(n / 1024.0, 1) if n is not None else None


class Health:
    def __init__(self, fw=None, reset_cause=None):
        self.fw = fw
        self.reset_cause = reset_cause
        self.uptime_s = 0.0
        self._last = _ticks()
        self._next_report = 0.0          # uptime at which the next report is due (first: now)
        self._clear()

    def _clear(self):
        self.err_console = 0
        self.err_sheets = 0
        self.err_wu = 0
        self.recoveries = 0
        self.mem_min = None

    def tick(self):
        """Call once per main-loop cycle: advances uptime and tracks the lowest free RAM"""
        now = _ticks()
        self.uptime_s += _ticks_diff(now, self._last) / 1000.0
        self._last = now
        mem = _mem_free()
        if mem is not None and (self.mem_min is None or mem < self.mem_min):
            self.mem_min = mem

    def due(self):
        return self.uptime_s >= self._next_report

    def report(self):
        return {
            "fw": self.fw,
            "uptime_h": round(self.uptime_s / 3600.0, 1),
            "reset_cause": self.reset_cause,
            "mem_free_kb": _kb(_mem_free()),
            "mem_min_kb": _kb(self.mem_min),
            "rssi": _rssi(),
            "err_console": self.err_console,
            "err_sheets": self.err_sheets,
            "err_wu": self.err_wu,
            "recoveries": self.recoveries,
        }

    def sent(self):
        """The report reached the sheet: counters start over, next one in a day"""
        self._next_report = self.uptime_s + REPORT_EVERY_SECONDS
        self._clear()
