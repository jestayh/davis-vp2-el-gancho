"""
Historical Data Recovery via Davis DMPAFT Protocol
Loaded only while a gap is being filled (main._with_history_module), to keep the main loop's RAM
free. Reading the archive itself lives in davis_reader (also used for every record's wind samples).

The Davis console keeps ~17 days of 10-minute archive records. Whenever the ESP32 could
not upload (no internet, no valid clock, power cut), the missing interval is read back
from that archive and uploaded, so the console acts as the outage buffer.
"""

import time
import sys

try:
    import ustruct as struct
except ImportError:
    import struct

from config import (
    DAVIS_HOST, DAVIS_PORT,
    ENABLE_DMPAFT_RECOVERY,
    GOOGLE_APPS_SCRIPT_URL
)
from src.davis_reader import (
    log, dmpaft_fetch_raw, ARCHIVE_INTERVAL_SECONDS,
    tuple_to_secs as _tuple_to_secs, secs_to_tuple as _secs_to_tuple, _EPOCH_OFFSET,
    calculate_dewpoint, get_effective_timezone_offset,
    format_timestamp_utc,
    calculate_wet_bulb, calculate_wind_chill,
    calculate_heat_index, calculate_thw_index,
    calculate_wind_run, calculate_degree_days,
    calculate_indoor_heat_index, calculate_emc,
    calculate_air_density
)
from src.data_logger import get_last_sync, update_last_sync_timestamp
from src.google_sheets import upload_to_google_sheets
from src import watchdog

IS_ESP32 = sys.platform == "esp32"

RECOVERY_CHUNK_RECORDS = 72                # 12 h per pass: keeps raw records well under ~10 KB of RAM
RECOVERY_MAX_AGE_SECONDS = 7 * 86400       # never reach further back than this


def _ts_str(t):
    return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:00".format(t[0], t[1], t[2], t[3], t[4])


def parse_archive_record(rec, rec_tuple):
    """
    Decode one 52-byte archive record (rev B) into the dict used by the uploaders.
    Rain totals (day/storm/month/year) are left empty: the archive only stores the rain of
    each interval, so the Apps Script continues them from the previous row of the sheet.
    """
    r_year, r_month, r_day, r_hour, r_min = rec_tuple

    t_out_raw = struct.unpack_from("<h", rec, 4)[0]
    temp_out_f = round(t_out_raw / 10.0, 1) if t_out_raw != 32767 else None
    temp_out_c = round((temp_out_f - 32.0) * 5.0 / 9.0, 1) if temp_out_f is not None else None

    t_hi_raw = struct.unpack_from("<h", rec, 6)[0]
    temp_out_hi_f = round(t_hi_raw / 10.0, 1) if t_hi_raw != -32768 else temp_out_f
    temp_out_hi_c = round((temp_out_hi_f - 32.0) * 5.0 / 9.0, 1) if temp_out_hi_f is not None else temp_out_c

    t_low_raw = struct.unpack_from("<h", rec, 8)[0]
    temp_out_low_f = round(t_low_raw / 10.0, 1) if t_low_raw != 32767 else temp_out_f
    temp_out_low_c = round((temp_out_low_f - 32.0) * 5.0 / 9.0, 1) if temp_out_low_f is not None else temp_out_c

    t_in_raw = struct.unpack_from("<h", rec, 20)[0]
    temp_in_f = round(t_in_raw / 10.0, 1) if t_in_raw != 32767 else None
    temp_in_c = round((temp_in_f - 32.0) * 5.0 / 9.0, 1) if temp_in_f is not None else None

    hum_in = rec[22] if rec[22] <= 100 else None
    hum_out = rec[23] if rec[23] <= 100 else None
    dew_f = calculate_dewpoint(temp_out_f, hum_out)
    dew_c = round((dew_f - 32.0) * 5.0 / 9.0, 1) if dew_f is not None else None

    bar_raw = struct.unpack_from("<H", rec, 14)[0]
    press_inhg = round(bar_raw / 1000.0, 3) if bar_raw > 0 else None
    press_hpa = round(press_inhg * 33.8639, 1) if press_inhg else None

    rain_clicks = struct.unpack_from("<H", rec, 10)[0]
    rain_mm = round(rain_clicks * 0.254, 1)

    rain_rate_clicks = struct.unpack_from("<H", rec, 12)[0]
    rain_rate_in = round(rain_rate_clicks / 100.0, 2)
    rain_rate_mm = round(rain_rate_in * 25.4, 1)

    w_spd_mph = rec[24] if rec[24] != 255 else 0
    w_spd_kmh = round(w_spd_mph * 1.60934, 1)
    w_gust_mph = rec[25] if rec[25] != 255 else w_spd_mph
    w_gust_kmh = round(w_gust_mph * 1.60934, 1)
    w_dir_hi = round(rec[26] * 22.5) if rec[26] <= 15 else None
    w_dir = round(rec[27] * 22.5) if rec[27] <= 15 else None  # 255 = calm, no direction

    wind_samples_raw = struct.unpack_from("<H", rec, 18)[0]
    wind_samples = wind_samples_raw if wind_samples_raw != 0xFFFF else None
    iss_recept = round(min(100.0, (wind_samples / 225.0) * 100.0), 1) if wind_samples is not None else None

    solar_raw = struct.unpack_from("<H", rec, 16)[0]
    solar = solar_raw if solar_raw != 32767 and solar_raw != 0xFFFF else None
    uv_raw = rec[28]
    uv = round(uv_raw / 10.0, 1) if uv_raw != 255 else None

    heat_dd, cool_dd = calculate_degree_days(temp_out_c, 10)

    ts_local = _ts_str(rec_tuple)
    offset = get_effective_timezone_offset()
    try:
        utc_epoch = _tuple_to_secs(rec_tuple) - _EPOCH_OFFSET - int(offset * 3600)
        ts_utc = format_timestamp_utc(utc_epoch)
    except Exception:
        ts_utc = ts_local + "Z"

    return {
        "timestamp": ts_local,
        "timestamp_utc": ts_utc,
        "temp_out_c": temp_out_c,
        "temp_out_hi_c": temp_out_hi_c,
        "temp_out_low_c": temp_out_low_c,
        "temp_in_c": temp_in_c,
        "humidity_out": hum_out,
        "humidity_in": hum_in,
        "in_dew_c": None if temp_in_f is None else _f_to_c(calculate_dewpoint(temp_in_f, hum_in)),
        "in_heat_c": calculate_indoor_heat_index(temp_in_c, hum_in),
        "in_emc": calculate_emc(temp_in_c, hum_in),
        "in_air_density": calculate_air_density(temp_in_c, hum_in, press_hpa),
        "dewpoint_c": dew_c,
        "wet_bulb_c": calculate_wet_bulb(temp_out_c, hum_out),
        "wind_chill_c": calculate_wind_chill(temp_out_c, w_spd_kmh),
        "heat_index_c": calculate_heat_index(temp_out_c, hum_out),
        "thw_index_c": calculate_thw_index(temp_out_c, hum_out, w_spd_kmh),
        "heat_dd": heat_dd,
        "cool_dd": cool_dd,
        "pressure_hpa": press_hpa,
        "pressure_inhg": press_inhg,
        "wind_speed_kmh": w_spd_kmh,
        "wind_speed_10min_avg_kmh": w_spd_kmh,
        "wind_direction_deg": w_dir,
        "windspdkmh_avg2m": w_spd_kmh,
        "windgustkmh_10m": w_gust_kmh,
        "windgustdir_10m": w_dir_hi,
        "wind_run_km": calculate_wind_run(w_spd_kmh, 10),
        "wind_samples": wind_samples,
        "wind_tx": 1,
        "iss_recept": iss_recept,
        "arc_int": 10,
        "rain_interval_mm": rain_mm,
        "rain_rate_mm_per_hr": rain_rate_mm,
        "rain_day_mm": None,
        "storm_rain_mm": None,
        "rain_month_mm": None,
        "rain_year_mm": None,
        "console_battery_v": None,
        "tx_battery_status": None,
        "solar_radiation_wm2": solar,
        "uv_index": uv,
        "uploaded_to_wu": "No"
    }


def _f_to_c(temp_f):
    return round((temp_f - 32.0) * 5.0 / 9.0, 1) if temp_f is not None else None


def _show(disp, *lines):
    if disp and disp.has_oled:
        disp.show_message(*lines)


def recover_gap(until_tuple, disp=None):
    """
    Upload the console archive records missing between the last record that reached
    Google Sheets (data/last_sync.txt) and until_tuple (exclusive), in chunks of
    RECOVERY_CHUNK_RECORDS. Requires the console clock to match local time (kept by
    the daily console clock sync).

    Returns (done, progressed): done=True when nothing is missing anymore, progressed=True
    when at least one record was uploaded in this call (so the caller should keep going).
    """
    if not ENABLE_DMPAFT_RECOVERY or not GOOGLE_APPS_SCRIPT_URL:
        return True, False

    since = get_last_sync()
    if not since:
        log("[HISTORICO] Sin registro previo valido; no hay vacio que recuperar.")
        return True, False

    until_secs = _tuple_to_secs(until_tuple)
    since_secs = _tuple_to_secs(since)
    if until_secs - since_secs <= ARCHIVE_INTERVAL_SECONDS:
        return True, False

    if until_secs - since_secs > RECOVERY_MAX_AGE_SECONDS:
        since = _secs_to_tuple(until_secs - RECOVERY_MAX_AGE_SECONDS)
        since_secs = _tuple_to_secs(since)
        log("[HISTORICO] Vacio mayor a 7 dias: se recupera solo desde {}".format(_ts_str(since)))

    gap_min = (until_secs - since_secs) // 60
    log("[HISTORICO] Faltan ~{} min ({} -> {}). Leyendo archivo de la consola...".format(
        gap_min, _ts_str(since), _ts_str(until_tuple)))
    _show(disp, "RECU HISTORICO", "Corte detectado", "Faltan ~{} min".format(gap_min), "Consultando...")

    # Exclusive upper bound: the record stamped until_tuple was already uploaded live
    last_wanted = _secs_to_tuple(until_secs - 60)
    raw = dmpaft_fetch_raw(DAVIS_HOST, DAVIS_PORT, since, last_wanted, max_records=RECOVERY_CHUNK_RECORDS)
    if raw is None:
        log("[HISTORICO] La consola no respondio; se reintentara.")
        return False, False
    if not raw:
        log("[HISTORICO] La consola no tiene registros para ese intervalo.")
        update_last_sync_timestamp(_ts_str(last_wanted))
        return True, False

    total = len(raw)
    sent = 0
    while raw:
        rec_tuple, rec_bytes = raw.pop(0)
        watchdog.feed()
        rec = parse_archive_record(rec_bytes, rec_tuple)
        del rec_bytes
        try:
            ok = upload_to_google_sheets(GOOGLE_APPS_SCRIPT_URL, rec)
        except Exception as e:
            log("[WARNING] Error subiendo historico a Sheets: {}".format(e))
            ok = False
        if not ok:
            log("[HISTORICO] Subida interrumpida en {}; se reintentara.".format(rec["timestamp"]))
            return False, sent > 0
        update_last_sync_timestamp(rec["timestamp"])
        sent += 1
        if disp:
            disp.sheets_success_pulse()
        if sent % 2 == 0 or sent == total:
            _show(disp, "RECU HISTORICO", "Subiendo a Drive", "{}/{}".format(sent, total), rec["timestamp"][11:16])
        del rec
        if IS_ESP32:
            import gc
            gc.collect()
        time.sleep(0.2)

    log("[HISTORICO] {} registros recuperados desde la consola.".format(sent))
    if total < RECOVERY_CHUNK_RECORDS:
        # Archive exhausted for this gap (the console may simply have no older records)
        update_last_sync_timestamp(_ts_str(last_wanted))
        return True, True
    return False, True
