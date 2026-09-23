"""
Historical Data Recovery via Davis DMPAFT Protocol
Isolated module to prevent boot-time memory retention in main loop.
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
    log, get_local_time_tuple, wakeup_console,
    encode_davis_datetime, verify_crc,
    calculate_dewpoint, get_effective_timezone_offset,
    format_timestamp_utc,
    calculate_wet_bulb, calculate_wind_chill,
    calculate_heat_index, calculate_thw_index,
    calculate_wind_run, calculate_degree_days,
    calculate_indoor_heat_index, calculate_emc,
    calculate_air_density
)
from src.data_logger import log_reading_csv, get_last_csv_timestamp
from src.google_sheets import upload_to_google_sheets

IS_ESP32 = sys.platform == "esp32"


def dmpaft_fetch_missing_records(host, port, since_tuple, timeout=10, baseline_rain=None):
    """
    Query Davis console internal data logger (DMPAFT) for archive records
    stored after since_tuple = (year, month, day, hour, minute).
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
            return []

        pkt = encode_davis_datetime(*since_tuple)
        sock.sendall(pkt)

        hdr_buf = sock.recv(10)
        if hdr_buf.startswith(b"\x06"):
            hdr = hdr_buf[1:7]
        else:
            hdr = hdr_buf[:6]

        if len(hdr) < 6:
            return []

        npages, start_idx, _ = struct.unpack("<HHH", hdr)
        log("DMPAFT: {} paginas disponibles (indice inicial={})".format(npages, start_idx))
        if npages == 0:
            sock.sendall(b"\x1b")
            return []

        if npages > 36:
            npages = 36

        for page_idx in range(npages):
            sock.sendall(b"\x06")

            page_data = b""
            start_rx = time.time()
            while len(page_data) < 267 and (time.time() - start_rx) < timeout:
                chunk = sock.recv(267 - len(page_data))
                if not chunk:
                    break
                page_data += chunk

            if len(page_data) < 267:
                log("Pagina {} incompleta ({} bytes)".format(page_idx, len(page_data)))
                break

            if not verify_crc(page_data):
                log("Pagina {} error de CRC - descartando".format(page_idx))
                continue

            for rec_i in range(5):
                if page_idx == 0 and rec_i < start_idx:
                    continue
                offset = 1 + (rec_i * 52)
                rec = page_data[offset : offset + 52]

                d_raw, t_raw = struct.unpack_from("<HH", rec, 0)
                if d_raw == 0xFFFF or d_raw == 0:
                    continue

                r_year = ((d_raw >> 9) & 0x7F) + 2000
                r_month = (d_raw >> 5) & 0x0F
                r_day = d_raw & 0x1F
                r_hour = t_raw // 100
                r_min = t_raw % 100

                rec_tuple = (r_year, r_month, r_day, r_hour, r_min)
                if rec_tuple <= since_tuple:
                    continue

                t_out_raw = struct.unpack_from("<h", rec, 4)[0]
                temp_out_f = round(t_out_raw / 10.0, 1) if t_out_raw != 32767 else None
                temp_out_c = round((temp_out_f - 32.0) * 5.0 / 9.0, 1) if temp_out_f is not None else None

                t_hi_raw = struct.unpack_from("<h", rec, 6)[0]
                temp_out_hi_f = round(t_hi_raw / 10.0, 1) if t_hi_raw != 32767 else temp_out_f
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
                rain_in = round(rain_clicks / 100.0, 2)
                rain_mm = round(rain_in * 25.4, 1)
                rain_interval_mm = rain_mm

                rain_rate_clicks = struct.unpack_from("<H", rec, 12)[0]
                rain_rate_in = round(rain_rate_clicks / 100.0, 2)
                rain_rate_mm = round(rain_rate_in * 25.4, 1)

                w_spd_mph = rec[24] if rec[24] != 255 else 0
                w_spd_kmh = round(w_spd_mph * 1.60934, 1)
                w_gust_mph = rec[25] if rec[25] != 255 else w_spd_mph
                w_gust_kmh = round(w_gust_mph * 1.60934, 1)
                w_dir_hi = round(rec[26] * 22.5) if rec[26] <= 15 else None
                w_dir = round(rec[27] * 22.5) if rec[27] <= 15 else 0

                wind_samples_raw = struct.unpack_from("<H", rec, 18)[0]
                wind_samples = wind_samples_raw if wind_samples_raw != 0xFFFF else 225
                iss_recept = round(min(100.0, (wind_samples / 225.0) * 100.0), 1)

                solar_raw = struct.unpack_from("<H", rec, 16)[0]
                solar = solar_raw if solar_raw != 32767 and solar_raw != 0xFFFF else None
                uv_raw = rec[28]
                uv = round(uv_raw / 10.0, 1) if uv_raw != 255 else None

                # Compute comfort metrics matching WeatherLink bulletin
                wet_bulb_c = calculate_wet_bulb(temp_out_c, hum_out)
                wind_chill_c = calculate_wind_chill(temp_out_c, w_spd_kmh)
                heat_index_c = calculate_heat_index(temp_out_c, hum_out)
                thw_index_c = calculate_thw_index(temp_out_c, hum_out, w_spd_kmh)

                # Indoor derived metrics & Degree days
                in_dew_f = calculate_dewpoint(temp_in_f, hum_in)
                in_dew_c = round((in_dew_f - 32.0) * 5.0 / 9.0, 1) if in_dew_f is not None else None
                in_heat_c = calculate_indoor_heat_index(temp_in_c, hum_in)
                in_emc = calculate_emc(temp_in_c, hum_in)
                in_air_density = calculate_air_density(temp_in_c, hum_in, press_hpa)
                heat_dd, cool_dd = calculate_degree_days(temp_out_c, 10)
                wind_run_km = calculate_wind_run(w_spd_kmh, 10)

                rain_month = 0.0
                rain_year = 0.0
                storm_rain = 0.0
                if baseline_rain:
                    rain_month = baseline_rain.get("rain_month_mm", 0.0) or 0.0
                    rain_year = baseline_rain.get("rain_year_mm", 0.0) or 0.0
                    storm_rain = baseline_rain.get("storm_rain_mm", 0.0) or 0.0

                ts_local = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:00".format(
                    r_year, r_month, r_day, r_hour, r_min
                )

                offset = get_effective_timezone_offset()
                try:
                    local_epoch = time.mktime((r_year, r_month, r_day, r_hour, r_min, 0, 0, 0))
                    utc_epoch = int(local_epoch - (offset * 3600))
                    ts_utc = format_timestamp_utc(utc_epoch)
                except Exception:
                    ts_utc = ts_local + "Z"

                records.append({
                    "timestamp": ts_local,
                    "timestamp_utc": ts_utc,
                    "temp_out_c": temp_out_c,
                    "temp_out_f": temp_out_f,
                    "temp_out_hi_c": temp_out_hi_c,
                    "temp_out_hi_f": temp_out_hi_f,
                    "temp_out_low_c": temp_out_low_c,
                    "temp_out_low_f": temp_out_low_f,
                    "temp_in_c": temp_in_c,
                    "temp_in_f": temp_in_f,
                    "humidity_out": hum_out,
                    "humidity_in": hum_in,
                    "in_dew_c": in_dew_c,
                    "in_heat_c": in_heat_c,
                    "in_emc": in_emc,
                    "in_air_density": in_air_density,
                    "dewpoint_c": dew_c,
                    "dewpoint_f": dew_f,
                    "wet_bulb_c": wet_bulb_c,
                    "wind_chill_c": wind_chill_c,
                    "heat_index_c": heat_index_c,
                    "thw_index_c": thw_index_c,
                    "heat_dd": heat_dd,
                    "cool_dd": cool_dd,
                    "pressure_hpa": press_hpa,
                    "pressure_inhg": press_inhg,
                    "wind_speed_kmh": w_spd_kmh,
                    "wind_speed_mph": w_spd_mph,
                    "wind_speed_10min_avg_kmh": w_spd_kmh,
                    "wind_speed_10min_avg_mph": w_spd_mph,
                    "wind_direction_deg": w_dir,
                    "windspdkmh_avg2m": w_spd_kmh,
                    "windspdmph_avg2m": w_spd_mph,
                    "winddir_avg2m": w_dir,
                    "windgustkmh_10m": w_gust_kmh,
                    "windgustmph_10m": w_gust_mph,
                    "windgustdir_10m": w_dir_hi,
                    "wind_run_km": wind_run_km,
                    "wind_samples": wind_samples,
                    "wind_tx": 1,
                    "iss_recept": iss_recept,
                    "arc_int": 10,
                    "rain_interval_mm": rain_interval_mm,
                    "rain_day_mm": rain_mm,
                    "rain_day_in": rain_in,
                    "storm_rain_mm": storm_rain,
                    "storm_rain_in": round(storm_rain / 25.4, 2),
                    "rain_month_mm": rain_month,
                    "rain_month_in": round(rain_month / 25.4, 2),
                    "rain_year_mm": rain_year,
                    "rain_year_in": round(rain_year / 25.4, 2),
                    "rain_rate_mm_per_hr": rain_rate_mm,
                    "rain_rate_in_per_hr": rain_rate_in,
                    "console_battery_v": None,
                    "tx_battery_status": None,
                    "solar_radiation_wm2": solar,
                    "uv_index": uv,
                    "uploaded_to_wu": "No"
                })

        sock.sendall(b"\x1b")
        time.sleep(0.2)
    except Exception as e:
        log("Error en recuperacion DMPAFT: {}".format(e))
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


def check_and_recover_history(disp):
    """
    Check if there is a gap in data/weather.csv since last shutdown/power outage.
    If gap > 10 min and ENABLE_DMPAFT_RECOVERY is True, query Davis console (DMPAFT),
    backfill local CSV, and upload missing records to Google Sheets.
    """
    if not ENABLE_DMPAFT_RECOVERY:
        return

    last_tuple = get_last_csv_timestamp(CSV_FILEPATH)
    if not last_tuple:
        log("[HISTORICO] No hay registros previos en CSV. Se inicia registro limpio.")
        return

    try:
        last_epoch = time.mktime((last_tuple[0], last_tuple[1], last_tuple[2], last_tuple[3], last_tuple[4], 0, 0, 0))
        now_tuple = get_local_time_tuple()
        now_epoch = time.mktime((now_tuple[0], now_tuple[1], now_tuple[2], now_tuple[3], now_tuple[4], 0, 0, 0))
        gap_seconds = now_epoch - last_epoch

        if gap_seconds <= 600:
            log("[HISTORICO] Base de datos al dia (ultimo registro hace {}s)".format(int(gap_seconds)))
            return

        gap_min = int(gap_seconds // 60)
        log("[HISTORICO] Detectado corte/vacio de datos de {} min (desde {:04d}-{:02d}-{:02d} {:02d}:{:02d})".format(
            gap_min, last_tuple[0], last_tuple[1], last_tuple[2], last_tuple[3], last_tuple[4]
        ))

        if disp and disp.has_oled:
            disp.show_message("RECU HISTORICO", "Corte detectado", "Faltan ~{} min".format(gap_min), "Consultando...")

        # Cap query at 48 hours to prevent memory exhaustion
        fetch_tuple = last_tuple
        if gap_seconds > 86400 * 2:
            capped_epoch = now_epoch - (86400 * 2)
            ct = time.localtime(capped_epoch)
            fetch_tuple = (ct[0], ct[1], ct[2], ct[3], ct[4])

        # Obtener acumulados actuales de lluvia de la estacion como linea base
        baseline_rain = None
        try:
            from src.davis_reader import query_station
            live_snap = query_station(DAVIS_HOST, DAVIS_PORT, timeout=2.5)
            if live_snap:
                baseline_rain = {
                    "rain_month_mm": live_snap.get("rain_month_mm", 0.0),
                    "rain_year_mm": live_snap.get("rain_year_mm", 0.0),
                    "storm_rain_mm": live_snap.get("storm_rain_mm", 0.0),
                }
        except Exception:
            pass

        records = dmpaft_fetch_missing_records(DAVIS_HOST, DAVIS_PORT, fetch_tuple, baseline_rain=baseline_rain)
        if not records:
            log("[HISTORICO] No se encontraron registros adicionales en la consola Davis.")
            return

        log("[HISTORICO] Recuperados {} registros faltantes desde la consola Davis!".format(len(records)))
        if disp and disp.has_oled:
            disp.show_message("RECU HISTORICO", "Recuperados:", "{} registros".format(len(records)), "Guardando...")

        for i, rec in enumerate(records, 1):
            log_reading_csv(CSV_FILEPATH, rec, uploaded=False)
            if GOOGLE_APPS_SCRIPT_URL:
                try:
                    up_ok = upload_to_google_sheets(GOOGLE_APPS_SCRIPT_URL, rec)
                    if up_ok and disp:
                        disp.sheets_success_pulse()
                except Exception as e:
                    log("[WARNING] Error subiendo historico a Sheets: {}".format(e))
            if disp and disp.has_oled and (i % 2 == 0 or i == len(records)):
                disp.show_message("RECU HISTORICO", "Subiendo a Drive", "{}/{}".format(i, len(records)), rec["timestamp"][11:16])
            time.sleep(0.2)

        log("[HISTORICO] Recuperacion completada con exito. {} registros guardados y subidos.".format(len(records)))
        if disp and disp.has_oled:
            disp.show_message("RECU HISTORICO", "Completado!", "{} registros".format(len(records)), "Iniciando...")
            time.sleep(1.5)
    except Exception as e:
        log("[ERROR] Error en proceso de recuperacion historica: {}".format(e))
