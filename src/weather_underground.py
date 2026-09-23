"""
Weather Underground RapidFire API Uploader
Compatible with both PC (CPython) and ESP32 (MicroPython)
"""

try:
    import usocket as socket
except ImportError:
    import socket

import time

try:
    import urllib.request
    HAS_URLLIB = True
except ImportError:
    HAS_URLLIB = False


def log(msg):
    t = time.localtime()
    print("[{:02d}:{:02d}:{:02d}] {}".format(t[3], t[4], t[5], msg))


def urlencode(params):
    """Minimal, portable URL parameter encoder (avoids urllib dependency on MicroPython)"""
    parts = []
    for k, v in params.items():
        if v is not None:
            # Simple conversion to string
            val_str = str(v)
            # Replace spaces and common delimiters if any
            val_str = val_str.replace(" ", "%20")
            parts.append("{}={}".format(k, val_str))
    return "&".join(parts)


def http_get_socket(host, path_with_query, timeout=10):
    """
    Perform a lightweight raw HTTP GET request via direct TCP socket (port 80).
    Ideal for MicroPython on ESP32 without requiring urequests/urllib.
    """
    s = None
    try:
        addr = socket.getaddrinfo(host, 80)[0][-1]
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(addr)

        req = (
            "GET {} HTTP/1.1\r\n"
            "Host: {}\r\n"
            "User-Agent: DavisVantagePro2-Uploader\r\n"
            "Connection: close\r\n\r\n"
        ).format(path_with_query, host)

        s.sendall(req.encode('utf-8'))

        response = bytearray()
        while True:
            chunk = s.recv(512)
            if not chunk:
                break
            response.extend(chunk)

        res_text = response.decode('utf-8', 'ignore')
        return res_text
    finally:
        if s:
            try:
                s.close()
            except Exception:
                pass


def upload(station_id, api_key, data, timeout=10):
    """
    Upload weather data to Weather Underground PWS RapidFire API.
    Masks credentials in all logs.

    Args:
        station_id: Weather Underground station ID
        api_key: Weather Underground API password / station key
        data: dictionary from davis_reader.parse_packet
        timeout: network timeout in seconds

    Returns:
        True on success, False on failure.
    """
    try:
        # Build parameter mapping for Weather Underground
        params = {
            "ID": station_id,
            "PASSWORD": api_key,
            "dateutc": "now",
            "action": "updateraw",
            "realtime": "1",
            "rtfreq": "30",
        }

        # Outdoor readings
        if data.get("temp_out_f") is not None:
            params["tempf"] = "{:.1f}".format(data["temp_out_f"])
        if data.get("humidity_out") is not None:
            params["humidity"] = str(data["humidity_out"])
        if data.get("dewpoint_f") is not None:
            params["dewptf"] = "{:.1f}".format(data["dewpoint_f"])
        if data.get("pressure_inhg") is not None:
            params["baromin"] = "{:.2f}".format(data["pressure_inhg"])

        # Wind readings
        if data.get("wind_speed_mph") is not None:
            params["windspeedmph"] = "{:.1f}".format(data["wind_speed_mph"])
        if data.get("wind_direction_deg") is not None:
            params["winddir"] = str(int(data["wind_direction_deg"]))

        # Advanced Wind metrics (Option A - Rolling window)
        if data.get("windspdmph_avg2m") is not None:
            params["windspdmph_avg2m"] = "{:.1f}".format(data["windspdmph_avg2m"])
        if data.get("winddir_avg2m") is not None:
            params["winddir_avg2m"] = str(int(data["winddir_avg2m"]))
        if data.get("windgustmph_10m") is not None:
            params["windgustmph_10m"] = "{:.1f}".format(data["windgustmph_10m"])
            params["windgustmph"] = "{:.1f}".format(data["windgustmph_10m"])
        elif data.get("wind_speed_10min_avg_mph") is not None:
            params["windgustmph"] = "{:.1f}".format(data["wind_speed_10min_avg_mph"])
        if data.get("windgustdir_10m") is not None:
            params["windgustdir_10m"] = str(int(data["windgustdir_10m"]))

        # Rain readings
        if data.get("rain_rate_in_per_hr") is not None:
            params["rainin"] = "{:.2f}".format(data["rain_rate_in_per_hr"])
        if data.get("rain_day_in") is not None:
            params["dailyrainin"] = "{:.2f}".format(data["rain_day_in"])

        # Solar Radiation & UV (optional sensors)
        if data.get("solar_radiation_wm2") is not None:
            params["solarradiation"] = str(data["solar_radiation_wm2"])
        if data.get("uv_index") is not None:
            params["uv"] = "{:.1f}".format(data["uv_index"])

        # Indoor readings
        if data.get("temp_in_f") is not None:
            params["indoortempf"] = "{:.1f}".format(data["temp_in_f"])
        if data.get("humidity_in") is not None:
            params["indoorhumidity"] = str(data["humidity_in"])

        query_str = urlencode(params)
        path = "/weatherstation/updateweatherstation.php?" + query_str
        host = "rtupdate.wunderground.com"

        if HAS_URLLIB:
            url = "http://" + host + path
            req = urllib.request.Request(url, headers={"User-Agent": "DavisVantagePro2-Uploader"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = resp.read().decode("utf-8", "ignore")
        else:
            result = http_get_socket(host, path, timeout=timeout)

        success = "success" in result.lower()

        if success:
            t_out = data.get("temp_out_f")
            t_str = "{:.1f}F".format(t_out) if t_out is not None else "--F"
            h_out = data.get("humidity_out")
            h_str = "{}%".format(h_out) if h_out is not None else "--%"
            p_out = data.get("pressure_inhg")
            p_str = "{:.2f}inHg".format(p_out) if p_out is not None else "--inHg"
            w_spd = data.get("wind_speed_mph")
            w_dir = data.get("wind_direction_deg")
            w_str = "{}mph/{}°".format(w_spd if w_spd is not None else 0, w_dir if w_dir is not None else 0)
            r_rate = data.get("rain_rate_in_per_hr") or 0.0

            log("[OK] WU Upload: T={} H={} P={} W={} R={:.2f}in/h".format(
                t_str, h_str, p_str, w_str, r_rate
            ))
        else:
            # Secure log without exposing API key
            clean_res = result.strip().replace("\r", " ").replace("\n", " ")
            log("[FAIL] WU: Response: {}".format(clean_res[:100]))

        return success

    except Exception as e:
        log("[ERROR] WU upload exception: {}".format(e))
        return False
