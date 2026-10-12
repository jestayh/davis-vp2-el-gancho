"""
Hardware watchdog for the ESP32.

If the program gets stuck (a socket that never answers, a deadlock), nobody at the station can
restart it. Once started, the ESP32 hardware watchdog resets the board unless feed() is called
within TIMEOUT_SECONDS. The main loop feeds it every few seconds, and so do the long jobs (OTA
download, console archive reads, gap recovery) inside their loops.

A reset caused by the watchdog also counts as a failed boot for the /main.py launcher, so a new
version that hangs is rolled back just like one that crashes.

On a PC (or if the board refuses the watchdog) start() and feed() do nothing.
"""

import sys

# The longest legitimate wait between two feeds is a TLS handshake that hangs ~2 min before failing
TIMEOUT_SECONDS = 300

_wdt = None


def start(timeout_s=TIMEOUT_SECONDS):
    """Start the hardware watchdog (cannot be stopped afterwards). Returns True if running."""
    global _wdt
    if _wdt is not None:
        return True
    if sys.platform != "esp32":
        return False
    try:
        from machine import WDT
        _wdt = WDT(timeout=int(timeout_s * 1000))
        print("[WATCHDOG] Activo: reinicio si el programa se cuelga mas de {} s".format(timeout_s))
        return True
    except Exception as e:
        print("[WATCHDOG] No se pudo activar: {}".format(e))
        return False


def feed():
    if _wdt is not None:
        try:
            _wdt.feed()
        except Exception:
            pass
