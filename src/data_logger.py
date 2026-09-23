"""
CSV Data Logger & Lightweight Statistics & Wind Tracker (Option A)
Compatible with both PC (CPython) and ESP32 (MicroPython)
"""

import os
import time
import math

CSV_FIELDNAMES = [
    "timestamp",
    # Barometer
    "pressure_hpa",
    "pressure_inhg",
    "barometer_trend",
    # Outdoor
    "temp_out_c",
    "temp_out_f",
    "humidity_out",
    "dewpoint_c",
    "dewpoint_f",
    "wet_bulb_c",
    "wind_chill_c",
    "heat_index_c",
    "thw_index_c",
    # Indoor
    "temp_in_c",
    "temp_in_f",
    "humidity_in",
    # Wind (Instantaneous + Davis 10m avg)
    "wind_speed_kmh",
    "wind_speed_mph",
    "wind_direction_deg",
    "wind_speed_10min_avg_kmh",
    "wind_speed_10min_avg_mph",
    # Advanced Wind (Rolling buffer - Option A)
    "windspdkmh_avg2m",
    "windspdmph_avg2m",
    "winddir_avg2m",
    "windgustkmh_10m",
    "windgustmph_10m",
    "windgustdir_10m",
    # Rain
    "rain_rate_mm_per_hr",
    "rain_rate_in_per_hr",
    "rain_day_mm",
    "rain_day_in",
    "rain_month_mm",
    "rain_month_in",
    "rain_year_mm",
    "rain_year_in",
    "storm_rain_mm",
    "storm_rain_in",
    "storm_start_date",
    # Evapotranspiration
    "day_et_in",
    "month_et_in",
    "year_et_in",
    # Solar & UV
    "solar_radiation_wm2",
    "uv_index",
    # Diagnostics & Astronomy
    "console_battery_v",
    "tx_battery_status",
    "sunrise",
    "sunset",
    "forecast_icons",
    "forecast_rule",
    # Status
    "uploaded_to_wu",
]


def ensure_parent_dir(filepath):
    """Ensure directory containing filepath exists (safe for both PC and ESP32)"""
    if "/" in filepath or "\\" in filepath:
        normalized = filepath.replace("\\", "/")
        folder = normalized.rsplit("/", 1)[0]
        if folder:
            try:
                if hasattr(os, "makedirs"):
                    os.makedirs(folder, exist_ok=True)
                else:
                    try:
                        os.mkdir(folder)
                    except Exception:
                        pass
            except Exception:
                pass


def file_exists(filepath):
    """Check if file exists portably"""
    try:
        with open(filepath, "r"):
            return True
    except Exception:
        return False


import sys
IS_ESP32 = sys.platform == "esp32"
LAST_SYNC_FILE = "data/last_sync.txt"


def update_last_sync_timestamp(ts_str):
    """Save lightweight 19-byte timestamp to state file (prevents flash wear-out on ESP32)"""
    if not ts_str:
        return
    try:
        ensure_parent_dir(LAST_SYNC_FILE)
        with open(LAST_SYNC_FILE, "w") as f:
            f.write(str(ts_str).strip()[:19])
    except Exception:
        pass


def get_last_csv_timestamp(filepath):
    """
    Read the last valid timestamp from tiny state file or fallback CSV.
    Returns (year, month, day, hour, minute) tuple or None.
    Fast and memory-efficient for MicroPython.
    """
    # 1. Check tiny 19-byte state file first
    try:
        if file_exists(LAST_SYNC_FILE):
            with open(LAST_SYNC_FILE, "r") as f:
                ts_str = f.read().strip()
                if len(ts_str) >= 16:
                    year = int(ts_str[0:4])
                    month = int(ts_str[5:7])
                    day = int(ts_str[8:10])
                    hour = int(ts_str[11:13])
                    minute = int(ts_str[14:16])
                    return (year, month, day, hour, minute)
    except Exception:
        pass

    # 2. Fallback to CSV (for PC or legacy systems)
    try:
        if not file_exists(filepath):
            return None
        size = os.stat(filepath)[6]
        if size == 0:
            return None
        with open(filepath, "r") as f:
            if size > 2048:
                f.seek(size - 2048)
            lines = f.readlines()
            for line in reversed(lines):
                line = line.strip()
                if not line or line.startswith("timestamp"):
                    continue
                parts = line.split(",")
                ts_str = parts[0].strip()
                if len(ts_str) >= 16:
                    year = int(ts_str[0:4])
                    month = int(ts_str[5:7])
                    day = int(ts_str[8:10])
                    hour = int(ts_str[11:13])
                    minute = int(ts_str[14:16])
                    return (year, month, day, hour, minute)
    except Exception:
        pass
    return None


def log_reading_csv(filepath, data, uploaded=False, cycle=None):
    """
    Log weather reading.
    On ESP32: Updates tiny 19-byte state file only, bypassing heavy CSV to protect flash memory.
    On PC: Appends full reading to CSV database file.
    """
    if isinstance(data, int) and isinstance(uploaded, dict):
        real_cycle = data
        data = uploaded
        uploaded = cycle if cycle is not None else False
        cycle = real_cycle

    # Always maintain the tiny state file for outage recovery
    ts = data.get("timestamp")
    if ts:
        update_last_sync_timestamp(ts)

    # On ESP32, avoid heavy CSV writes to prevent flash wear-out and disk saturation
    if IS_ESP32:
        return True

    ensure_parent_dir(filepath)
    is_new = not file_exists(filepath)

    row_vals = []
    for field in CSV_FIELDNAMES:
        if field == "uploaded_to_wu":
            row_vals.append("Yes" if uploaded else "No")
        else:
            val = data.get(field)
            if val is None:
                row_vals.append("")
            else:
                row_vals.append(str(val))

    line = ",".join(row_vals) + "\n"

    try:
        with open(filepath, "a") as f:
            if is_new:
                f.write(",".join(CSV_FIELDNAMES) + "\n")
            f.write(line)
        return True
    except Exception as e:
        print("[ERROR] CSV logging error: {}".format(e))
        return False


class DailyStats:
    """
    Lightweight in-memory daily statistics calculator (Metric for Chile).
    Uses ~100 bytes of RAM (vital for ESP32), avoiding reading large files from disk.
    """
    def __init__(self):
        self.current_day = None
        self.count = 0
        self.wu_attempts = 0
        self.wu_success_count = 0
        self.temp_out_min_c = None
        self.temp_out_max_c = None
        self.temp_out_sum_c = 0.0
        self.wind_max_kmh = 0.0
        self.day_rain_mm = 0.0

    def update(self, data, uploaded):
        t = time.localtime()
        day = t[2]
        if self.current_day != day:
            self.current_day = day
            self.count = 0
            self.wu_attempts = 0
            self.wu_success_count = 0
            self.temp_out_min_c = None
            self.temp_out_max_c = None
            self.temp_out_sum_c = 0.0
            self.wind_max_kmh = 0.0
            self.day_rain_mm = 0.0

        self.count += 1
        # uploaded is True (success), False (failed attempt), or None (no upload scheduled)
        if uploaded is not None:
            self.wu_attempts += 1
            if uploaded is True:
                self.wu_success_count += 1

        t_out = data.get("temp_out_c")
        if t_out is not None:
            if self.temp_out_min_c is None or t_out < self.temp_out_min_c:
                self.temp_out_min_c = t_out
            if self.temp_out_max_c is None or t_out > self.temp_out_max_c:
                self.temp_out_max_c = t_out
            self.temp_out_sum_c += t_out

        w_kmh = data.get("windgustkmh_10m") or data.get("wind_speed_kmh")
        if w_kmh is not None and w_kmh > self.wind_max_kmh:
            self.wind_max_kmh = w_kmh

        if data.get("rain_day_mm") is not None:
            self.day_rain_mm = data["rain_day_mm"]

    def get_summary(self):
        if self.count == 0:
            return "Sin datos registrados hoy"
        avg_temp = self.temp_out_sum_c / self.count if self.count else 0.0
        min_t = self.temp_out_min_c if self.temp_out_min_c is not None else 0.0
        max_t = self.temp_out_max_c if self.temp_out_max_c is not None else 0.0
        success_pct = (self.wu_success_count * 100.0 / self.wu_attempts) if self.wu_attempts else 100.0
        return (
            "=== Resumen Diario ({} lecturas locales) ===\n"
            "  Temp Ext: mín {:.1f}°C, máx {:.1f}°C (media {:.1f}°C)\n"
            "  Ráfaga Máxima: {:.1f} km/h\n"
            "  Lluvia del Día: {:.1f} mm\n"
            "  Subidas a WU: {}/{} exitosas ({:.1f}%)\n"
            "============================================"
        ).format(
            self.count,
            min_t, max_t, avg_temp,
            self.wind_max_kmh,
            self.day_rain_mm,
            self.wu_success_count, self.wu_attempts, success_pct
        )
