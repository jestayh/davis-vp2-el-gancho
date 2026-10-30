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

import io
import sys

IS_ESP32 = sys.platform == "esp32"

# Latest firmware version announced by the Cloudflare proxy (X-Davis-Fw header) on the last upload
FW_HINT = None
# Why the last failed upload failed (sent in the daily health report), or None
LAST_ERROR = None

# Backup route outside Cloudflare (appengine/main.py, plain HTTP). From 2026-10-28 the house network
# reached *.workers.dev only now and then; config.py can't change by OTA, so the host lives here.
try:
    from config import RELAY_HOST
except ImportError:
    RELAY_HOST = "davis-el-gancho.rj.r.appspot.com"
RELAY_URL = "http://" + RELAY_HOST + "/" if RELAY_HOST else ""
# The route that worked last goes first, so an outage of the other one costs no timeout per row
USING_RELAY = False


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
        "barometer_trend": data.get("barometer_trend") or "",
        "rain_interval_mm": data.get("rain_interval_mm"),
        "rain_rate_mm_per_hr": data.get("rain_rate_mm_per_hr") if data.get("rain_rate_mm_per_hr") is not None else 0.0,
        # Rain totals stay empty when unknown (archive records): the Apps Script continues them
        "rain_day_mm": data.get("rain_day_mm"),
        "storm_rain_mm": data.get("storm_rain_mm"),
        "rain_month_mm": data.get("rain_month_mm"),
        "rain_year_mm": data.get("rain_year_mm"),
        "heat_dd": data.get("heat_dd") if data.get("heat_dd") is not None else 0.0,
        "cool_dd": data.get("cool_dd") if data.get("cool_dd") is not None else 0.0,
        "temp_in_c": data.get("temp_in_c"),
        "humidity_in": data.get("humidity_in"),
        "in_dew_c": data.get("in_dew_c"),
        "in_heat_c": data.get("in_heat_c"),
        "in_emc": data.get("in_emc"),
        "in_air_density": data.get("in_air_density"),
        # Empty when unknown rather than an invented 225 samples / 100 % reception
        "wind_samples": data.get("wind_samples"),
        "wind_tx": data.get("wind_tx") if data.get("wind_tx") is not None else 1,
        "iss_recept": data.get("iss_recept"),
        "arc_int": data.get("arc_int") if data.get("arc_int") is not None else 10,
        "console_battery_v": data.get("console_battery_v"),
        "tx_battery_status": data.get("tx_battery_status"),
        "solar_radiation_wm2": data.get("solar_radiation_wm2"),
        "uv_index": data.get("uv_index"),
    }
    # Optional: why the ESP32 last restarted (first row after boot), target tab ("consola") and
    # failed WU uploads in the interval
    for key in ("reset_cause", "destino", "wu_err"):
        if data.get(key):
            payload[key] = data[key]
    return _post_with_backup(script_url, payload, timeout)


def upload_status(script_url, status, timestamp, timeout=12):
    """
    Daily health report of the ESP32 to the "Estado" tab. The "ESTADO " prefix makes an older
    Apps Script reject it as an invalid date instead of writing it among the weather rows.
    """
    if not script_url:
        return False
    payload = dict(status)
    payload["destino"] = "estado"
    payload["timestamp"] = "ESTADO " + timestamp
    return _post_with_backup(script_url, payload, timeout)


def _post_with_backup(script_url, payload, timeout=12):
    """POST through the main proxy or, if it fails, the backup route (last working one first)"""
    global USING_RELAY
    routes = [script_url]
    if RELAY_URL and script_url and script_url != RELAY_URL:
        routes = [RELAY_URL, script_url] if USING_RELAY else [script_url, RELAY_URL]
    for url in routes:
        if _post_json(url, payload, timeout):
            if USING_RELAY != (url == RELAY_URL):
                USING_RELAY = url == RELAY_URL
                log("Ruta de subida: {}".format("respaldo" if USING_RELAY else "principal"))
            return True
    return False


# MicroPython's json.dump only writes to real streams: io.IOBase subclasses qualify (they get
# UTF-8 bytes); CPython passes str
class _ByteCounter(io.IOBase):
    """json.dump target that only counts the UTF-8 bytes (for Content-Length)"""
    def __init__(self):
        self.n = 0

    def write(self, s):
        size = len(s.encode("utf-8")) if isinstance(s, str) else len(s)
        self.n += size
        return size


class _ChunkWriter(io.IOBase):
    """Buffers json.dump's many small writes into fixed 256-byte socket writes"""
    def __init__(self, sock, size=256):
        self.sock = sock
        self.buf = bytearray(size)
        self.mv = memoryview(self.buf)
        self.n = 0

    def write(self, s):
        b = s.encode("utf-8") if isinstance(s, str) else s
        size = len(b)
        if self.n + size > len(self.buf):
            self.flush()
        if size > len(self.buf):
            self.sock.write(b)
            return size
        self.mv[self.n:self.n + size] = b       # copied now: MicroPython may reuse b
        self.n += size
        return size

    def flush(self):
        if self.n:
            self.sock.write(self.mv[:self.n])
            self.n = 0


def _post_json(script_url, payload, timeout=12):
    """POST payload as JSON to the Apps Script / proxy. Returns True on success."""
    if not script_url or not (script_url.startswith("https://") or script_url.startswith("http://")):
        return False
    s = ss = None
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

            # The JSON body is streamed in small pieces instead of being built as one string:
            # MicroPython takes memory from the system for large blocks and never gives it back,
            # and without that memory the ESP32 cannot open sockets any more (OSError -203)
            count = _ByteCounter()
            ujson.dump(payload, count)
            head = ("POST {} HTTP/1.0\r\nHost: {}\r\nUser-Agent: Davis-ESP32\r\n"
                    "Content-Type: application/json\r\nContent-Length: {}\r\n"
                    "Connection: close\r\n\r\n").format(uri, host, count.n)
            gc.collect()

            ai = socket.getaddrinfo(host, port)
            addr = ai[0][-1]
            s = socket.socket()
            # The proxy now answers only after Google stored the row (~2-6 s, more if busy)
            s.settimeout(20)
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

            ss.write(head)
            out = _ChunkWriter(ss)
            ujson.dump(payload, out)
            out.flush()
            del out, head
            raw_resp = ss.readline()

            # Scan response headers for the firmware version hint
            global FW_HINT
            FW_HINT = None
            for _ in range(20):
                line = ss.readline()
                if not line or line in (b"\r\n", b"\n"):
                    break
                if line[:11].lower() == b"x-davis-fw:":
                    FW_HINT = line[11:].strip().decode("ascii", "ignore") or None

            if is_ssl:
                try:
                    ss.close()
                except Exception:
                    pass
            s.close()
            ss = s = None
            gc.collect()

            status_str = raw_resp.decode("utf-8", "ignore") if raw_resp else ""
            if "200" in status_str or "302" in status_str:
                log("Fila enviada exitosamente a Google Sheets.")
                return True
            else:
                _fail("respuesta " + status_str[:40].strip())
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
        _fail("{}: {}".format(type(e).__name__, e))
        return False
    finally:
        # A failure after connect() must not leave the socket open holding network memory
        for sock in (ss, s):
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass


def _fail(reason):
    global LAST_ERROR
    LAST_ERROR = reason[:60]
    log("Error enviando a Google Sheets: {}".format(reason))
