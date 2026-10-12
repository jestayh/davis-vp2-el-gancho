"""
Historical Data Recovery via Davis DMPAFT Protocol
Isolated module to prevent boot-time memory retention in main loop.

The Davis console keeps ~17 days of 10-minute archive records. Whenever the ESP32 could
not upload (no internet, no valid clock, power cut), the missing interval is read back
from that archive and uploaded, so the console acts as the outage buffer.
"""

import time
import sys

try:
    import usocket as socket
except ImportError:
    import socket

try:
    import ustruct as struct
except ImportError:
    import struct

from config import (
    DAVIS_HOST, DAVIS_PORT,
    ENABLE_DMPAFT_RECOVERY,
    CSV_FILEPATH, GOOGLE_APPS_SCRIPT_URL
)
from src.davis_reader import (
    log, wakeup_console,
    encode_davis_datetime, verify_crc,
    calculate_dewpoint, get_effective_timezone_offset,
    format_timestamp_utc, _civil_to_secs,
    calculate_wet_bulb, calculate_wind_chill,
    calculate_heat_index, calculate_thw_index,
    calculate_wind_run, calculate_degree_days,
    calculate_indoor_heat_index, calculate_emc,
    calculate_air_density
)
from src.data_logger import (
    log_reading_csv, get_last_csv_timestamp, update_last_sync_timestamp, file_exists
)
from src.google_sheets import upload_to_google_sheets

IS_ESP32 = sys.platform == "esp32"

ARCHIVE_INTERVAL_SECONDS = 600
RECOVERY_CHUNK_RECORDS = 72                # 12 h per pass: keeps raw records well under ~10 KB of RAM
RECOVERY_MAX_AGE_SECONDS = 7 * 86400       # never reach further back than this

# One-time jobs: windows of the console archive uploaded raw to the "Consola" tab, stamped with the
# console clock, which ran exactly 3 h behind until it was fixed on 2026-10-11 ~12:25. The Apps
# Script function importarConsola() measures that offset against the main sheet and inserts only
# the dates the main sheet is missing. Every window ends before the clock fix, so +3 h holds for all.
# - 08-oct: station unplugged twice while the internet was down, day uploaded stamped 1999/2000.
# - 03-oct 06:30 and 11-oct 12:10-12:20: single records lost before gap recovery existed / while
#   the ESP32 was being flashed. Windows include an hour around each gap.
BACKFILL_JOBS = (
    ("consola_20261008", (2026, 10, 7, 23, 50), (2026, 10, 8, 23, 50)),   # console clock: (exclusive, inclusive]
    ("consola_20261003", (2026, 10, 3, 2, 20), (2026, 10, 3, 4, 30)),
    ("consola_20261011", (2026, 10, 11, 7, 50), (2026, 10, 11, 9, 20)),
)


def _backfill_done_file(job_id):
    return "data/" + job_id + ".done"

# Seconds-since-epoch of the platform's time.gmtime(0) (1970 on PC, 2000 on MicroPython)
_EPOCH_OFFSET = _civil_to_secs(*time.gmtime(0)[:6])


def _tuple_to_secs(t):
    return _civil_to_secs(t[0], t[1], t[2], t[3], t[4], 0)


def _secs_to_tuple(secs):
    t = time.gmtime(secs - _EPOCH_OFFSET)
    return (t[0], t[1], t[2], t[3], t[4])


def _ts_str(t):
    return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:00".format(t[0], t[1], t[2], t[3], t[4])


def dmpaft_fetch_raw(host, port, since_tuple, until_tuple=None, max_records=RECOVERY_CHUNK_RECORDS, timeout=10):
    """
    Read raw 52-byte archive records with since_tuple < date <= until_tuple (console clock,
    tuples are (year, month, day, hour, minute)), oldest first, at most max_records.
    Returns a list of (date_tuple, record_bytes), or None if the console did not answer.
    Records are parsed later, one at a time, to keep RAM low on the ESP32.
    """
    sock = None
    records = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))

        wakeup_console(sock)

        sock.sendall(b"DMPAFT\n")
        ack = sock.recv(1)
        if ack != b"\x06":
            log("Consola Davis no acepto DMPAFT")
            return None

        sock.sendall(encode_davis_datetime(*since_tuple))

        hdr_buf = sock.recv(10)
        if hdr_buf.startswith(b"\x06"):
            hdr = hdr_buf[1:7]
        else:
            hdr = hdr_buf[:6]

        if len(hdr) < 6:
            return None

        npages, start_idx, _ = struct.unpack("<HHH", hdr)
        log("DMPAFT: {} paginas disponibles (indice inicial={})".format(npages, start_idx))
        if npages == 0:
            sock.sendall(b"\x1b")
            return records

        done = False
        for page_idx in range(npages):
            sock.sendall(b"\x06")

            page_data = b""
            start_rx = time.time()
            while len(page_data) < 267 and (time.time() - start_rx) < timeout:
                chunk = sock.recv(267 - len(page_data))
                if not chunk:
                    break
                page_data += chunk

            # A bad page would silently drop 5 records: give up and retry the whole read later
            if len(page_data) < 267:
                log("Pagina {} incompleta ({} bytes)".format(page_idx, len(page_data)))
                sock.sendall(b"\x1b")
                return None

            if not verify_crc(page_data):
                log("Pagina {} error de CRC".format(page_idx))
                sock.sendall(b"\x1b")
                return None

            for rec_i in range(5):
                if page_idx == 0 and rec_i < start_idx:
                    continue
                offset = 1 + (rec_i * 52)
                rec = page_data[offset : offset + 52]

                d_raw, t_raw = struct.unpack_from("<HH", rec, 0)
                if d_raw == 0xFFFF or d_raw == 0:
                    continue

                rec_tuple = (((d_raw >> 9) & 0x7F) + 2000, (d_raw >> 5) & 0x0F, d_raw & 0x1F,
                             t_raw // 100, t_raw % 100)
                if rec_tuple <= since_tuple:
                    continue  # older records from the circular buffer
                if until_tuple is not None and rec_tuple > until_tuple:
                    done = True
                    break

                records.append((rec_tuple, bytes(rec)))
                if len(records) >= max_records:
                    done = True
                    break

            if done:
                break

        sock.sendall(b"\x1b")
        time.sleep(0.2)
    except Exception as e:
        log("Error en recuperacion DMPAFT: {}".format(e))
        return None
    finally:
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass
            time.sleep(0.5)

    return records


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


def read_wind_samples(slot_tuple):
    """
    Wind samples the console counted for the 10-min interval ending at slot_tuple (its archive
    record), or None if that record is not written yet or the console did not answer. The live
    LOOP packet does not carry this number.
    """
    since = _secs_to_tuple(_tuple_to_secs(slot_tuple) - ARCHIVE_INTERVAL_SECONDS)
    raw = dmpaft_fetch_raw(DAVIS_HOST, DAVIS_PORT, since, slot_tuple, max_records=1, timeout=5)
    if not raw or raw[0][0] != slot_tuple:
        return None
    samples = struct.unpack_from("<H", raw[0][1], 18)[0]
    return samples if samples != 0xFFFF else None


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

    since = get_last_csv_timestamp(CSV_FILEPATH)
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
    raw = dmpaft_fetch_raw(DAVIS_HOST, DAVIS_PORT, since, last_wanted)
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
        rec = parse_archive_record(rec_bytes, rec_tuple)
        del rec_bytes
        log_reading_csv(CSV_FILEPATH, rec, uploaded=False)
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


def _pending_jobs():
    if not GOOGLE_APPS_SCRIPT_URL:
        return []
    return [job for job in BACKFILL_JOBS if not file_exists(_backfill_done_file(job[0]))]


def console_backfill_pending():
    return bool(_pending_jobs())


def _mark_done(job_id, sent):
    try:
        with open(_backfill_done_file(job_id), "w") as f:
            f.write(str(sent))
    except Exception:
        pass


def run_console_backfill(disp=None):
    """
    One-time upload of each BACKFILL_JOBS window of the console archive (console clock) to the
    "Consola" tab. Rows are tagged destino=consola; the timestamp carries a "CONSOLA " prefix so
    an older Apps Script that ignores the tag can never mix them with real dates.
    Returns True once every job is done (then it never runs again).
    """
    for job in _pending_jobs():
        if not _run_backfill_job(job, disp):
            return False
    return True


def _run_backfill_job(job, disp):
    job_id, since, until = job
    label = "RESPALDO {:02d}-{:02d}".format(until[2], until[1])
    log("[RESPALDO] Leyendo de la consola {} -> {} para la pestana Consola...".format(_ts_str(since), _ts_str(until)))
    _show(disp, label, "Leyendo consola", "", "")
    raw = dmpaft_fetch_raw(DAVIS_HOST, DAVIS_PORT, since, until, max_records=160)
    if raw is None:
        log("[RESPALDO] La consola no respondio; se reintentara.")
        return False
    if not raw:
        # Already overwritten in the circular archive: nothing left to recover
        log("[RESPALDO] La consola ya no tiene registros de {}.".format(job_id))
        _mark_done(job_id, 0)
        return True

    total = len(raw)
    sent = 0
    while raw:
        rec_tuple, rec_bytes = raw.pop(0)
        rec = parse_archive_record(rec_bytes, rec_tuple)
        del rec_bytes
        rec["timestamp"] = "CONSOLA " + rec["timestamp"]
        rec["destino"] = "consola"
        try:
            ok = upload_to_google_sheets(GOOGLE_APPS_SCRIPT_URL, rec)
        except Exception as e:
            log("[RESPALDO] Error subiendo: {}".format(e))
            ok = False
        if not ok:
            log("[RESPALDO] Subida interrumpida ({}/{}); se reintentara.".format(sent, total))
            return False
        sent += 1
        if sent % 4 == 0 or sent == total:
            _show(disp, label, "Subiendo", "{}/{}".format(sent, total), "")
        del rec
        if IS_ESP32:
            import gc
            gc.collect()
        time.sleep(0.2)

    _mark_done(job_id, sent)
    log("[RESPALDO] {} registros de la consola subidos a la pestana Consola ({}).".format(sent, job_id))
    return True
