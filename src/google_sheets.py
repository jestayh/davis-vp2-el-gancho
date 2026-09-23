"""
Google Sheets / Drive Webhook Uploader
Compatible with both PC (CPython) and ESP32 (MicroPython)
Sends weather rows to a Google Apps Script Web App without external dependencies.
"""

try:
    import urequests as requests
except ImportError:
    try:
        import urllib.request
        import urllib.parse
        import json
    except ImportError:
        pass

import sys

IS_ESP32 = sys.platform == "esp32"


def log(msg):
    print("[GoogleDrive] {}".format(msg))


def upload_to_google_sheets(script_url, data, timeout=12):
    """
    Upload weather data dictionary to Google Sheets via Google Apps Script Webhook.
    Returns True on success, False otherwise.
    """
    if not script_url or not (script_url.startswith("https://") or script_url.startswith("http://")):
        return False

    # Extract relevant fields in metric format (matching WeatherLink bulletin & Google Sheets)
    payload = {
        "timestamp": data.get("timestamp"),
        "timestamp_utc": data.get("timestamp_utc"),
        "temp_out_c": data.get("temp_out_c"),
        "temp_out_hi_c": data.get("temp_out_hi_c") if data.get("temp_out_hi_c") is not None else data.get("temp_out_c"),
        "temp_out_low_c": data.get("temp_out_low_c") if data.get("temp_out_low_c") is not None else data.get("temp_out_c"),
        "wind_chill_c": data.get("wind_chill_c"),
        "heat_index_c": data.get("heat_index_c"),
        "thw_index_c": data.get("thw_index_c"),
        "dewpoint_c": data.get("dewpoint_c"),
        "wet_bulb_c": data.get("wet_bulb_c"),
        "humidity_out": data.get("humidity_out"),
        "wind_speed_kmh": data.get("wind_speed_kmh"),
        "wind_direction_deg": data.get("wind_direction_deg"),
        "windspdkmh_avg2m": data.get("windspdkmh_avg2m") if data.get("windspdkmh_avg2m") is not None else data.get("wind_speed_kmh"),
        "wind_speed_10min_avg_kmh": data.get("wind_speed_10min_avg_kmh") if data.get("wind_speed_10min_avg_kmh") is not None else data.get("wind_speed_kmh"),
        "wind_run_km": data.get("wind_run_km") if data.get("wind_run_km") is not None else 0.0,
        "windgustkmh_10m": data.get("windgustkmh_10m"),
        "windgustdir_10m": data.get("windgustdir_10m"),
        "pressure_hpa": data.get("pressure_hpa"),
        "barometer_trend": data.get("barometer_trend") or "Estable",
        "rain_interval_mm": data.get("rain_interval_mm") if data.get("rain_interval_mm") is not None else 0.0,
        "rain_rate_mm_per_hr": data.get("rain_rate_mm_per_hr") if data.get("rain_rate_mm_per_hr") is not None else 0.0,
        "rain_day_mm": data.get("rain_day_mm") if data.get("rain_day_mm") is not None else 0.0,
        "storm_rain_mm": data.get("storm_rain_mm") if data.get("storm_rain_mm") is not None else 0.0,
        "rain_month_mm": data.get("rain_month_mm") if data.get("rain_month_mm") is not None else 0.0,
        "rain_year_mm": data.get("rain_year_mm") if data.get("rain_year_mm") is not None else 0.0,
        "heat_dd": data.get("heat_dd") if data.get("heat_dd") is not None else 0.0,
        "cool_dd": data.get("cool_dd") if data.get("cool_dd") is not None else 0.0,
        "temp_in_c": data.get("temp_in_c"),
        "humidity_in": data.get("humidity_in"),
        "in_dew_c": data.get("in_dew_c"),
        "in_heat_c": data.get("in_heat_c"),
        "in_emc": data.get("in_emc"),
        "in_air_density": data.get("in_air_density"),
        "wind_samples": data.get("wind_samples") if data.get("wind_samples") is not None else 225,
        "wind_tx": data.get("wind_tx") if data.get("wind_tx") is not None else 1,
        "iss_recept": data.get("iss_recept") if data.get("iss_recept") is not None else 100.0,
        "arc_int": data.get("arc_int") if data.get("arc_int") is not None else 10,
        "console_battery_v": data.get("console_battery_v"),
        "tx_battery_status": data.get("tx_battery_status"),
        "solar_radiation_wm2": data.get("solar_radiation_wm2"),
        "uv_index": data.get("uv_index"),
    }

    try:
        if IS_ESP32:
            # High-efficiency single-socket TLS implementation for MicroPython
            # Avoids double-socket heap exhaustion (MBEDTLS_ERR_X509_ALLOC_FAILED)
            # and prevents 400 Bad Request on Google Apps Script redirect
            import gc
            gc.collect()
            try:
                import ssl
                import socket
            except ImportError:
                import ussl as ssl
                import usocket as socket
            import ujson

            # Parse URL host and path
            is_ssl = script_url.startswith("https://")
            port = 443 if is_ssl else 80
            if is_ssl:
                path = script_url[8:]
            elif script_url.startswith("http://"):
                path = script_url[7:]
            else:
                path = script_url

            slash_idx = path.find("/")
            if slash_idx != -1:
                host = path[:slash_idx]
                uri = path[slash_idx:]
            else:
                host = path
                uri = "/"

            # Pre-build request body before opening socket to minimize heap
            body = ujson.dumps(payload).encode("utf-8")
            req = (
                b"POST " + uri.encode("ascii") + b" HTTP/1.0\r\n" +
                b"Host: " + host.encode("ascii") + b"\r\n" +
                b"User-Agent: Davis-ESP32\r\n" +
                b"Content-Type: application/json\r\n" +
                b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n" +
                b"Connection: close\r\n\r\n" +
                body
            )
            del body
            gc.collect()

            ai = socket.getaddrinfo(host, port)
            addr = ai[0][-1]
            s = socket.socket()
            s.settimeout(10)
            s.connect(addr)

            if is_ssl:
                gc.collect()
                if hasattr(ssl, "SSLContext"):
                    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                    ctx.verify_mode = ssl.CERT_NONE
                    ss = ctx.wrap_socket(s, server_hostname=host)
                else:
                    ss = ssl.wrap_socket(s, server_hostname=host, cert_reqs=ssl.CERT_NONE)
            else:
                ss = s

            ss.write(req)
            del req
            raw_resp = ss.readline()
            if is_ssl:
                try:
                    ss.close()
                except Exception:
                    pass
            s.close()
            gc.collect()

            status_str = raw_resp.decode("utf-8", "ignore") if raw_resp else ""
            if "200" in status_str or "302" in status_str:
                log("Fila enviada exitosamente a Google Sheets.")
                return True
            else:
                log("Google Sheets respondio: {}".format(status_str[:40].strip()))
                return False

        else:
            # Standard PC upload using requests or urllib
            try:
                import requests
                r = requests.post(script_url, json=payload, headers={"User-Agent": "Davis-ESP32"}, timeout=timeout)
                if r.status_code in (200, 302):
                    log("Fila guardada exitosamente en Google Sheets.")
                    return True
                else:
                    log("Google Sheets respondio con codigo {}".format(r.status_code))
                    return False
            except ImportError:
                import json
                import urllib.request
                json_data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    script_url,
                    data=json_data,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    code = resp.getcode()
                    if code in (200, 302):
                        log("Fila guardada exitosamente en Google Sheets (HTTP {}).".format(code))
                        return True
                    else:
                        log("Google Sheets respondio con codigo {}".format(code))
                        return False
    except Exception as e:
        try:
            import sys
            sys.print_exception(e)
        except Exception:
            pass
        err = str(e).lower()
        if "302" in err or "redirect" in err:
            log("Fila enviada a Google Sheets (302 Redirect).")
            return True
        log("Error enviando a Google Sheets: {}".format(e))
        return False
