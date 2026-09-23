#!/usr/bin/env python
"""
Davis Vantage Pro 2 -> Local CSV Database & Weather Underground Uploader
Unified Python implementation for both PC (CPython) and ESP32 (MicroPython)
"""

import sys
import time

from config import (
    DAVIS_HOST, DAVIS_PORT,
    WU_STATION_ID, WU_API_KEY,
    WU_UPLOAD_INTERVAL_SECONDS,
    CARD_ROTATION_SECONDS,
    CSV_LOG_INTERVAL_SECONDS,
    ENABLE_DMPAFT_RECOVERY,
    SOCKET_TIMEOUT_SECONDS, CSV_FILEPATH,
    WIFI_SSID, WIFI_PASSWORD,
    WIFI_NETWORKS, MAMA_FALLBACK_PASSWORD,
    GOOGLE_APPS_SCRIPT_URL,
    STATUS_LED_PIN, ENABLE_STATUS_LED, STATUS_LED_MODE
)
from src.davis_reader import query_station, log, format_timestamp_local, format_timestamp_utc
from src.weather_underground import upload
from src.data_logger import log_reading_csv, DailyStats
from src.display import WeatherDisplay
from src.google_sheets import upload_to_google_sheets

IS_ESP32 = sys.platform == "esp32"
_led_p = STATUS_LED_PIN if globals().get("ENABLE_STATUS_LED", True) else None
_led_m = globals().get("STATUS_LED_MODE", "HEARTBEAT")
display = WeatherDisplay(led_pin=_led_p, led_mode=_led_m)


def connect_wifi_if_needed(show_display=False):
    """Connect to WiFi if running on an ESP32 board with multi-network and auto-scan support"""
    if not IS_ESP32:
        return

    try:
        import network
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)

        if wlan.isconnected():
            ip_info = wlan.ifconfig()
            display.set_ip(ip_info[0])
            if show_display:
                try:
                    active_ssid = wlan.config('essid') or WIFI_SSID
                except Exception:
                    active_ssid = WIFI_SSID
                display.show_wifi_status(active_ssid, ip_info[0])
            if time.time() < 1000000000:
                try:
                    import ntptime
                    ntptime.settime()
                    log("NTP Time synchronized successfully!")
                except Exception:
                    pass
            return

        target_ssid = WIFI_SSID
        target_pwd = WIFI_PASSWORD

        try:
            log("Scanning 2.4 GHz WiFi networks...")
            nets = wlan.scan()
            visible = []
            for n in nets:
                try:
                    s = n[0].decode("utf-8", "ignore")
                    if s and s not in visible:
                        visible.append(s)
                except Exception:
                    pass
            log("Visible 2.4 GHz networks: {}".format(visible[:8]))

            # 1. Match against known networks list (User house or Parents house)
            matched = False
            for s_name, s_pwd in WIFI_NETWORKS:
                if s_name in visible:
                    target_ssid = s_name
                    target_pwd = s_pwd
                    matched = True
                    log("Matched known network: '{}'".format(target_ssid))
                    break

            # 2. If not found, search for any 2.4GHz network matching 'Mama'
            if not matched:
                candidates = ["2.4G.Mama", "2.4G_Mama", "Mama_2.4G", "Mama-2.4G", "2.4GMama", "Mama"]
                for c in candidates:
                    if c in visible:
                        target_ssid = c
                        target_pwd = MAMA_FALLBACK_PASSWORD
                        matched = True
                        break
                if not matched:
                    for v in visible:
                        if "mama" in v.lower():
                            target_ssid = v
                            target_pwd = MAMA_FALLBACK_PASSWORD
                            matched = True
                            break
                if matched:
                    log("Auto-detected 2.4 GHz network: '{}' (matches 'Mama')".format(target_ssid))

            # 3. Default fallback
            if not matched:
                target_ssid = WIFI_SSID
                target_pwd = WIFI_PASSWORD
                log("[INFO] Attempting configured default: {}".format(target_ssid))

        except Exception as scan_err:
            log("[WARNING] WiFi scan error: {}".format(scan_err))
            target_ssid = WIFI_SSID
            target_pwd = WIFI_PASSWORD

        log("Connecting to WiFi: {}...".format(target_ssid))
        if show_display:
            display.show_wifi_status(target_ssid)
        wlan.connect(target_ssid, target_pwd)
        start_t = time.time()
        while not wlan.isconnected() and (time.time() - start_t) < 25:
            time.sleep(0.5)

        if wlan.isconnected():
            ip_info = wlan.ifconfig()
            log("WiFi Connected! IP: {}".format(ip_info[0]))
            display.set_ip(ip_info[0])
            try:
                active_ssid = wlan.config('essid') or target_ssid
            except Exception:
                active_ssid = target_ssid
            if show_display:
                display.show_wifi_status(active_ssid, ip_info[0])
            # Sync real-time clock via NTP on ESP32
            try:
                import ntptime
                ntptime.settime()
                log("NTP Time synchronized successfully!")
            except Exception as ntp_err:
                log("[WARNING] NTP sync failed (using internal clock): {}".format(ntp_err))
        else:
            log("[WARNING] Could not connect to WiFi within timeout.")
    except Exception as e:
        log("[ERROR] WiFi setup error: {}".format(e))


def main(max_cycles=None):
    if IS_ESP32:
        time.sleep(2)
    print("\n" + "=" * 70)
    print("  Davis Vantage Pro 2 -> Local CSV & Weather Underground")
    print("  Plataforma: {}".format("ESP32 (MicroPython)" if IS_ESP32 else "PC (Python 3)"))
    print("  Unidades: Sistema Métrico (°C, km/h, mm, hPa) [Chile]")
    print("  Modo: Consulta bajo demanda (LPS 3 2) cada {}s".format(WU_UPLOAD_INTERVAL_SECONDS))
    print("  Rotacion OLED: Pantallas cada {}s".format(CARD_ROTATION_SECONDS))
    print("  Subida a WU: {} (cada {}s)".format(WU_STATION_ID, WU_UPLOAD_INTERVAL_SECONDS))
    print("  Subida a Drive: {} (cada {}s)".format("Habilitado" if GOOGLE_APPS_SCRIPT_URL else "Deshabilitado", CSV_LOG_INTERVAL_SECONDS))
    print("  Auto-Recuperacion (DMPAFT): {}".format("Habilitado" if ENABLE_DMPAFT_RECOVERY else "Deshabilitado"))
    print("  LED de Estado (GPIO {}): {}".format(STATUS_LED_PIN, STATUS_LED_MODE if ENABLE_STATUS_LED else "Deshabilitado"))
    print("  Base de Datos: {}".format(CSV_FILEPATH))
    print("=" * 70 + "\n")

    # Connect WiFi if running on ESP32 (splash shown only during boot)
    connect_wifi_if_needed(show_display=True)
    display.set_station_id(WU_STATION_ID)

    # Check for Over-The-Air (OTA) firmware updates from GitHub
    if IS_ESP32:
        try:
            import src.ota_updater as ota
            ota.check_and_update(repo_user="jestayh", repo_name="davis-vp2-el-gancho", branch="main", display=display)
        except Exception as ota_err:
            log("[OTA] Error comprobando actualizaciones: {}".format(ota_err))
        finally:
            try:
                if "src.ota_updater" in sys.modules:
                    del sys.modules["src.ota_updater"]
            except Exception:
                pass
            import gc
            gc.collect()

    # Check and backfill historical data if any gap occurred
    if ENABLE_DMPAFT_RECOVERY:
        try:
            import src.history_recovery as hr
            hr.check_and_recover_history(display)
        except Exception as e:
            log("[HISTORICO] Error: {}".format(e))
        finally:
            try:
                if "src.history_recovery" in sys.modules:
                    del sys.modules["src.history_recovery"]
            except Exception:
                pass
        if IS_ESP32:
            import gc
            gc.collect()

    # Initial Davis console query to seed weather data
    if display and display.has_oled:
        display.show_message("DAVIS VP2", "Consultando consola", "Obteniendo datos...", "")

    data = None
    for attempt in range(5):
        data = query_station(DAVIS_HOST, DAVIS_PORT, timeout=SOCKET_TIMEOUT_SECONDS)
        if data:
            break
        time.sleep(2)

    if not data:
        log("[WARNING] No se obtuvo respuesta inicial de la consola Davis. Reintentando en bucle...")
        data = {
            "timestamp": format_timestamp_local(),
            "timestamp_utc": format_timestamp_utc(),
            "temp_out_c": 0.0,
            "humidity_out": 0,
            "pressure_hpa": 1013.2,
            "barometer_trend": "Estable"
        }

    stats = DailyStats()
    pending_drive_records = []
    last_wu_upload_time = 0
    last_logged_slot = int(time.time() // CSV_LOG_INTERVAL_SECONDS)
    cycle = 0
    slot_temp_hi = None
    slot_temp_low = None
    last_day_rain_seen = None

    try:
        while max_cycles is None or cycle < max_cycles:
            cycle_start = time.time()
            cycle += 1
            current_time = time.time()

            try:
                # Periodic WiFi watchdog (every ~60s on ESP32)
                if IS_ESP32 and cycle % 12 == 0:
                    try:
                        import network
                        wlan = network.WLAN(network.STA_IF)
                        if not wlan.isconnected():
                            log("[WIFI] Conexion perdida. Reconectando...")
                            connect_wifi_if_needed(show_display=False)
                    except Exception:
                        pass

                # 1. Determine if we need fresh data from the Davis station
                current_slot = int(current_time // CSV_LOG_INTERVAL_SECONDS)
                wu_due = (current_time - last_wu_upload_time >= WU_UPLOAD_INTERVAL_SECONDS)
                # Stagger Drive upload by 5s (1 card rotation tick) after WU to guarantee pristine C heap
                drive_due = (current_slot > last_logged_slot) and not wu_due

                if wu_due:
                    fresh = query_station(DAVIS_HOST, DAVIS_PORT, timeout=SOCKET_TIMEOUT_SECONDS)
                    if fresh:
                        data = fresh
                        cur_t = data.get("temp_out_c")
                        if cur_t is not None:
                            if slot_temp_hi is None or cur_t > slot_temp_hi:
                                slot_temp_hi = cur_t
                            if slot_temp_low is None or cur_t < slot_temp_low:
                                slot_temp_low = cur_t

                # Format concise status line for console
                if cycle % 6 == 1 or wu_due or drive_due:
                    log("#{:03d} | EXT: {:.1f}°C/{}% | INT: {:.1f}°C | VIENTO: {:.1f}km/h | PRES: {:.1f}hPa".format(
                        cycle,
                        data.get("temp_out_c") or 0.0,
                        data.get("humidity_out") or 0,
                        data.get("temp_in_c") or 0.0,
                        data.get("wind_speed_kmh") or 0.0,
                        data.get("pressure_hpa") or 0.0
                    ))

                # 2. Weather Underground Upload (every WU_UPLOAD_INTERVAL_SECONDS)
                if wu_due:
                    uploaded_wu = upload(WU_STATION_ID, WU_API_KEY, data, timeout=SOCKET_TIMEOUT_SECONDS)
                    last_wu_upload_time = current_time
                    display.update_cloud_status(wu_ok=uploaded_wu, timestamp_str=data.get("timestamp"))

                # 3. Synchronized CSV & Google Drive log (every CSV_LOG_INTERVAL_SECONDS)
                if drive_due:
                    aligned_utc_secs = current_slot * CSV_LOG_INTERVAL_SECONDS
                    record_data = dict(data)
                    record_data["timestamp"] = format_timestamp_local(aligned_utc_secs)
                    record_data["timestamp_utc"] = format_timestamp_utc(aligned_utc_secs)
                    record_data["temp_out_hi_c"] = slot_temp_hi if slot_temp_hi is not None else data.get("temp_out_c")
                    record_data["temp_out_low_c"] = slot_temp_low if slot_temp_low is not None else data.get("temp_out_c")
                    cur_rain = data.get("rain_day_mm", 0.0) or 0.0
                    if last_day_rain_seen is not None and cur_rain >= last_day_rain_seen:
                        record_data["rain_interval_mm"] = round(cur_rain - last_day_rain_seen, 1)
                    else:
                        record_data["rain_interval_mm"] = 0.0
                    last_day_rain_seen = cur_rain
                    slot_temp_hi = data.get("temp_out_c")
                    slot_temp_low = data.get("temp_out_c")

                    log("--- [SYNC :{:02d}s] Registro -> Local: {} | UTC: {} ---".format(
                        CSV_LOG_INTERVAL_SECONDS, record_data["timestamp"], record_data["timestamp_utc"]
                    ))

                    # Local CSV is always appended immediately (never lost)
                    log_reading_csv(CSV_FILEPATH, record_data, uploaded=False)

                    # Upload to Google Sheets / Drive (via ultra-fast Cloudflare Worker proxy)
                    if GOOGLE_APPS_SCRIPT_URL:
                        if IS_ESP32:
                            try:
                                import gc
                                gc.collect()
                            except Exception:
                                pass

                        try:
                            uploaded_gdrive = upload_to_google_sheets(GOOGLE_APPS_SCRIPT_URL, record_data)
                        except Exception as gs_err:
                            log("[WARNING] Google Sheets upload error: {}".format(gs_err))
                            uploaded_gdrive = False

                        display.update_cloud_status(gdrive_ok=uploaded_gdrive, timestamp_str=record_data.get("timestamp"))

                        if uploaded_gdrive:
                            display.show_sheets_success(record_data.get("timestamp"))
                            display.sheets_success_pulse()
                            time.sleep(3.0)

                            # Flush backlog from previous internet outage
                            if pending_drive_records:
                                log("[DRIVE] Internet activo. Subiendo {} registros pendientes por corte...".format(len(pending_drive_records)))
                                while pending_drive_records:
                                    p_rec = pending_drive_records[0]
                                    p_ok = False
                                    time.sleep(1.0)
                                    if IS_ESP32:
                                        import gc
                                        gc.collect()
                                    try:
                                        p_ok = upload_to_google_sheets(GOOGLE_APPS_SCRIPT_URL, p_rec)
                                    except Exception:
                                        p_ok = False
                                    if p_ok:
                                        display.sheets_success_pulse()
                                        pending_drive_records.pop(0)
                                    else:
                                        break
                                if not pending_drive_records:
                                    log("[DRIVE] Todos los registros pendientes fueron sincronizados a Google Sheets!")
                        else:
                            # Internet outage: store in memory buffer to re-upload when connection returns
                            if len(pending_drive_records) < 100:
                                pending_drive_records.append(record_data)
                            log("[DRIVE] Sin internet. Registro guardado en local CSV y encolado ({} pendientes)".format(len(pending_drive_records)))

                    last_logged_slot = current_slot

                # 4. Update OLED Display (slides vertically to next rotating card every 5s)
                display.show_weather(data, cycle=cycle)

                # 5. Update Daily In-Memory Statistics
                stats.update(data, True)
                if cycle % 20 == 0:
                    log(stats.get_summary())

            except KeyboardInterrupt:
                raise
            except Exception as cycle_err:
                log("[WARNING] Excepcion en ciclo #{:03d}: {}".format(cycle, cycle_err))
                if IS_ESP32:
                    try:
                        import sys
                        sys.print_exception(cycle_err)
                    except Exception:
                        pass

            # 6. MicroPython GC
            if IS_ESP32:
                try:
                    import gc
                    gc.collect()
                except Exception:
                    pass

            # Maintain smooth card rotation interval
            elapsed = time.time() - cycle_start
            sleep_time = CARD_ROTATION_SECONDS - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        log("Execution halted normally.")

    except KeyboardInterrupt:
        log("Execution halted by user.")
        if not IS_ESP32:
            sys.exit(0)
    except Exception as e:
        log("Fatal error: {}".format(e))
        if not IS_ESP32:
            sys.exit(1)


if __name__ == "__main__":
    main()
