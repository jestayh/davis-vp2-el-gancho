"""
Last record that reached Google Sheets (data/last_sync.txt, 19 bytes).

Gap recovery from the console archive starts from this timestamp. (This module used to keep
a local CSV and daily statistics too: the CSV was never written on the ESP32 and the statistics
only went to the serial port, so both were dropped to save RAM.)
"""

LAST_SYNC_FILE = "data/last_sync.txt"


def update_last_sync_timestamp(ts_str, path=None):
    """Save the timestamp of the last record that reached Google Sheets (dates before 2025 ignored)"""
    if not ts_str or str(ts_str)[:4] < "2025":
        return
    path = path or LAST_SYNC_FILE
    for attempt in range(2):
        try:
            with open(path, "w") as f:
                f.write(str(ts_str).strip()[:19])
            return
        except OSError:
            if attempt or "/" not in path:
                return
            try:
                import os
                os.mkdir(path.rsplit("/", 1)[0])     # first run: "data" folder missing
            except OSError:
                return


def get_last_sync(path=None):
    """(year, month, day, hour, minute) of the last record that reached the sheet, or None"""
    try:
        with open(path or LAST_SYNC_FILE, "r") as f:
            ts = f.read().strip()
        if len(ts) >= 16 and ts[:4] >= "2025":
            return (int(ts[0:4]), int(ts[5:7]), int(ts[8:10]), int(ts[11:13]), int(ts[14:16]))
    except Exception:
        pass
    return None
