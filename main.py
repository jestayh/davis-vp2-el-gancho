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
from src.davis_reader import (
    query_station, sync_console_time, log, format_timestamp_local, format_timestamp_utc,
    clock_is_valid, sync_ntp
)
from src.weather_underground import upload
from src.data_logger import log_reading_csv, update_last_sync_timestamp, DailyStats
from src.display import WeatherDisplay
from src.google_sheets import upload_to_google_sheets
import src.google_sheets as gsheets
import config as _config

# Optional settings (defaults apply when an older config.py on the device lacks them)
ENABLE_CONSOLE_CLOCK_SYNC = getattr(_config, "ENABLE_CONSOLE_CLOCK_SYNC", True)
CONSOLE_CLOCK_TOLERANCE_SECONDS = getattr(_config, "CONSOLE_CLOCK_TOLERANCE_SECONDS", 30)
CONSOLE_CLOCK_CHECK_INTERVAL_SECONDS = 86400
CONSOLE_CLOCK_RETRY_SECONDS = 3600
OTA_CHECK_INTERVAL_SECONDS = getattr(_config, "OTA_CHECK_INTERVAL_SECONDS", 86400)
# Cloudflare Worker used as fallback route when GitHub cannot be reached directly ("" disables it)
OTA_PROXY_HOST = getattr(_config, "OTA_PROXY_HOST", "davis-ota-proxy.estayh-jose.workers.dev")
# Minimum gap between OTA checks triggered by the version announced by the Sheets proxy
OTA_HINT_MIN_INTERVAL_SECONDS = 3600
# Without a valid clock nothing is logged; NTP is retried this often (and refreshed daily once valid)
NTP_RETRY_SECONDS = 300
NTP_RESYNC_SECONDS = 86400
# Attempts to recover a gap from the console archive before giving it up
GAP_RECOVERY_MAX_FAILURES = 6

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
            if not clock_is_valid():
                sync_ntp()
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
            # Real-time clock via NTP (fails while the router has no internet; retried in the main loop)
            sync_ntp()
        else:
            log("[WARNING] Could not connect to WiFi within timeout.")
    except Exception as e:
        log("[ERROR] WiFi setup error: {}".format(e))


def run_console_clock_sync():
    """Check/correct the Davis console clock. Returns seconds until the next check is due."""
    if not ENABLE_CONSOLE_CLOCK_SYNC:
        return CONSOLE_CLOCK_CHECK_INTERVAL_SECONDS
    try:
        status, _ = sync_console_time(
            DAVIS_HOST, DAVIS_PORT,
            tolerance_s=CONSOLE_CLOCK_TOLERANCE_SECONDS,
            timeout=SOCKET_TIMEOUT_SECONDS
        )
    except Exception as e:
        log("[RELOJ] Error: {}".format(e))
        status = "error"
    if status in ("ok", "set"):
        return CONSOLE_CLOCK_CHECK_INTERVAL_SECONDS
    return CONSOLE_CLOCK_RETRY_SECONDS


def read_installed_fw_version():
    """Firmware version recorded by the OTA updater (same default as src/ota_updater.py)"""
    try:
        import json
        with open("data/version.json", "r") as f:
            return json.load(f).get("version", "1.0.0")
    except Exception:
        return "1.0.0"


def confirm_ota_install():
    """Mark a freshly OTA-installed version as good, so the /main.py launcher won't roll it back"""
    try:
        import os
        os.remove("data/ota_pending.json")
        log("[OTA] Version v{} confirmada: arranque correcto".format(read_installed_fw_version()))
    except OSError:
        pass  # no version under trial


def run_ota_check():
    """Look for a newer firmware version on GitHub (ESP32 only). Reboots itself after an update."""
    if not IS_ESP32:
        return
    try:
        import src.ota_updater as ota
        ota.check_and_update(repo_user="jestayh", repo_name="davis-vp2-el-gancho", branch="main",
                             display=display, proxy_host=OTA_PROXY_HOST)
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


def read_reset_cause():
    """Why the ESP32 started (power-up after unplugging vs. a crash/watchdog), or None on PC"""
    if not IS_ESP32:
        return None
    try:
        import machine
        cause = machine.reset_cause()
        names = (
            ("PWRON_RESET", "Encendido"),
            ("HARD_RESET", "Reset hardware"),
            ("WDT_RESET", "Watchdog"),
            ("DEEPSLEEP_RESET", "Despertar"),
            ("SOFT_RESET", "Reinicio software"),
        )
        for attr, name in names:
            if cause == getattr(machine, attr, None):
                return name
        return "Causa {}".format(cause)
    except Exception:
        return None


def _with_history_module(fn):
    """Load src.history_recovery only while needed (keeps the main loop's RAM free)"""
    try:
        import src.history_recovery as hr
        return fn(hr)
    finally:
        try:
            if "src.history_recovery" in sys.modules:
                del sys.modules["src.history_recovery"]
        except Exception:
            pass
        if IS_ESP32:
            import gc
            gc.collect()


def run_gap_recovery(until_tuple):
    """Upload what the console archive holds between the last uploaded record and until_tuple"""
    try:
        return _with_history_module(lambda hr: hr.recover_gap(until_tuple, display))
    except Exception as e:
        log("[HISTORICO] Error: {}".format(e))
        return False, False


def console_backfill_pending():
    try:
        return _with_history_module(lambda hr: hr.console_backfill_pending())
    except Exception:
        return False


def run_console_backfill():
    try:
        return _with_history_module(lambda hr: hr.run_console_backfill(display))
    except Exception as e:
        log("[RESPALDO] Error: {}".format(e))
        return False


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

    # Check for Over-The-Air (OTA) firmware updates from GitHub (also repeated daily in the main loop)
    run_ota_check()
    next_ota_check = time.time() + OTA_CHECK_INTERVAL_SECONDS
    installed_fw = read_installed_fw_version()
    next_hint_ota_check = 0
    ota_confirmed = False

    # Make sure the console clock matches local time before reading its archive
    next_clock_check = time.time() + run_console_clock_sync()

    # Gaps since the last uploaded record (power cut, outage, reboot) are filled from the console
    # archive right after the first successful upload, once internet and the clock are confirmed
    reset_cause = read_reset_cause()
    if reset_cause:
        log("Causa del arranque: {}".format(reset_cause))
    if not clock_is_valid():
        log("[RELOJ] Sin hora valida (NTP sin internet): no se registrara nada hasta sincronizar")

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
    last_wu_upload_time = 0
    last_logged_slot = int(time.time() // CSV_LOG_INTERVAL_SECONDS)
    cycle = 0
    slot_temp_hi = None
    slot_temp_low = None
    last_day_rain_seen = None
    ntp_retry_cycles = max(1, NTP_RETRY_SECONDS // CARD_ROTATION_SECONDS)
    ntp_resync_cycles = max(1, NTP_RESYNC_SECONDS // CARD_ROTATION_SECONDS)
    check_gap = True          # at boot and after any failed upload
    gap_until = None          # first record uploaded after the gap (exclusive end of the recovery)
    gap_failures = 0
    backfill_pending = None   # unknown until the first successful upload

    try:
        while max_cycles is None or cycle < max_cycles:
            cycle += 1

            # NTP: retried every few minutes while the clock is invalid, refreshed daily afterwards
            if IS_ESP32:
                clock_was_valid = clock_is_valid()
                retry_ntp = cycle % (ntp_resync_cycles if clock_was_valid else ntp_retry_cycles) == 0
                if retry_ntp and sync_ntp():
                    # The clock may have jumped (from 2000 to today): rebase the schedules on it
                    new_slot = int(time.time() // CSV_LOG_INTERVAL_SECONDS)
                    if not clock_was_valid or new_slot < last_logged_slot:
                        last_logged_slot = new_slot - 1
                        last_wu_upload_time = 0
                    if not clock_was_valid:
                        next_clock_check = 0
                        check_gap = True

            cycle_start = time.time()
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
                    if uploaded_wu:
                        display.wu_flicker()
                        if not ota_confirmed:
                            confirm_ota_install()
                            ota_confirmed = True

                # 3. Synchronized CSV & Google Drive log (every CSV_LOG_INTERVAL_SECONDS)
                if drive_due and not clock_is_valid():
                    # A 1999/2000 date would be worthless: skip it, the console archive fills the gap later
                    log("[RELOJ] Sin hora valida: registro omitido (se recuperara desde la consola)")
                    last_logged_slot = current_slot
                    check_gap = True
                    gap_until = None
                    last_day_rain_seen = None  # the next interval would include the skipped ones
                elif drive_due:
                    aligned_utc_secs = current_slot * CSV_LOG_INTERVAL_SECONDS
                    record_data = dict(data)
                    record_data["timestamp"] = format_timestamp_local(aligned_utc_secs)
                    record_data["timestamp_utc"] = format_timestamp_utc(aligned_utc_secs)
                    record_data["temp_out_hi_c"] = slot_temp_hi if slot_temp_hi is not None else data.get("temp_out_c")
                    record_data["temp_out_low_c"] = slot_temp_low if slot_temp_low is not None else data.get("temp_out_c")
                    cur_rain = data.get("rain_day_mm")
                    if cur_rain is None or last_day_rain_seen is None:
                        # First record after boot or a skipped slot: unknown here, the Apps Script
                        # derives it from the day total of the previous row
                        record_data["rain_interval_mm"] = None
                    elif cur_rain >= last_day_rain_seen:
                        record_data["rain_interval_mm"] = round(cur_rain - last_day_rain_seen, 1)
                    else:
                        # The console reset the day total at midnight: all of it fell since then
                        record_data["rain_interval_mm"] = round(cur_rain, 1)
                    last_day_rain_seen = cur_rain
                    slot_temp_hi = data.get("temp_out_c")
                    slot_temp_low = data.get("temp_out_c")

                    log("--- [SYNC :{:02d}s] Registro -> Local: {} | UTC: {} ---".format(
                        CSV_LOG_INTERVAL_SECONDS, record_data["timestamp"], record_data["timestamp_utc"]
                    ))

                    if reset_cause:
                        record_data["reset_cause"] = reset_cause

                    # Local CSV is always appended immediately (never lost)
                    log_reading_csv(CSV_FILEPATH, record_data, uploaded=False)

                    # Upload to Google Sheets / Drive (via ultra-fast Cloudflare Worker proxy)
                    if not GOOGLE_APPS_SCRIPT_URL:
                        update_last_sync_timestamp(record_data["timestamp"])
                    else:
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
                            if not ota_confirmed:
                                confirm_ota_install()
                                ota_confirmed = True
                            display.show_sheets_success(record_data.get("timestamp"))
                            display.sheets_success_pulse()
                            time.sleep(3.0)
                            reset_cause = None

                            # Fill any gap before this record from the console archive (it holds ~17 days),
                            # so an outage or power cut leaves no hole in the sheet
                            if check_gap:
                                if gap_until is None:
                                    ts = record_data["timestamp"]
                                    gap_until = (int(ts[0:4]), int(ts[5:7]), int(ts[8:10]), int(ts[11:13]), int(ts[14:16]))
                                gap_done, gap_progress = run_gap_recovery(gap_until)
                                if not gap_done and not gap_progress:
                                    gap_failures += 1
                                    if gap_failures >= GAP_RECOVERY_MAX_FAILURES:
                                        log("[HISTORICO] No se pudo recuperar el vacio tras {} intentos; se abandona.".format(gap_failures))
                                        gap_done = True
                                if gap_done:
                                    check_gap = False
                                    gap_until = None
                                    gap_failures = 0
                            if not check_gap:
                                update_last_sync_timestamp(record_data["timestamp"])

                                # One-time upload of the 2026-10-08 console day to the "Consola" tab
                                if backfill_pending is None:
                                    backfill_pending = console_backfill_pending()
                                if backfill_pending and run_console_backfill():
                                    backfill_pending = False

                            # The Sheets proxy announces the latest published firmware: update without waiting a day
                            fw_hint = gsheets.FW_HINT
                            if IS_ESP32 and fw_hint and fw_hint != installed_fw and time.time() >= next_hint_ota_check:
                                log("[OTA] El proxy anuncia la version v{} (instalada v{}). Revisando...".format(fw_hint, installed_fw))
                                next_hint_ota_check = time.time() + OTA_HINT_MIN_INTERVAL_SECONDS
                                run_ota_check()
                        else:
                            # Internet outage: nothing is queued in RAM; the console archive is the buffer
                            check_gap = True
                            gap_until = None
                            log("[DRIVE] Sin internet. Se recuperara desde la consola cuando vuelva la conexion")

                    last_logged_slot = current_slot

                # 4. Update OLED Display (slides vertically to next rotating card every 5s)
                display.show_weather(data, cycle=cycle)

                # 5. Update Daily In-Memory Statistics
                stats.update(data, True)
                if cycle % 20 == 0:
                    log(stats.get_summary())

                # 5b. Daily Davis console clock check (skipped in cycles that upload to WU/Sheets)
                if not wu_due and not drive_due and time.time() >= next_clock_check:
                    next_clock_check = time.time() + run_console_clock_sync()

                # 5c. Daily OTA check, so updates arrive without rebooting the ESP32
                if IS_ESP32 and not wu_due and not drive_due and time.time() >= next_ota_check:
                    next_ota_check = time.time() + OTA_CHECK_INTERVAL_SECONDS
                    run_ota_check()

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

            # Update status LED timer (ensures 20s solid light turns off accurately)
            display.check_led_timer()

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
