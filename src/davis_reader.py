"""
Davis Vantage Pro 2 - Complete LOOP 1 Packet Parser & Socket Client
Compatible with both PC (CPython) and ESP32 (MicroPython)
"""

try:
    import usocket as socket
except ImportError:
    import socket

try:
    import ustruct as struct
except ImportError:
    import struct

import math
import time


try:
    from config import TIMEZONE_OFFSET_HOURS
except ImportError:
    TIMEZONE_OFFSET_HOURS = "AUTO"


def get_chile_offset_hours(utc_epoch=None):
    """
    Calculate official Chilean Continental timezone offset (-3 or -4) based on
    Decreto Supremo 224 / SHOA (www.horaoficial.cl).
    - Horario de Invierno (UTC-4): desde el primer domingo de abril (medianoche sábado)
    - Horario de Verano (UTC-3): desde el primer domingo de septiembre (medianoche sábado)
    """
    if utc_epoch is None:
        utc_epoch = time.time()
    t = time.localtime(utc_epoch)
    m, d, h = t[1], t[2], t[3]
    wday = t[6]  # 0=Mon .. 6=Sun

    if m in (1, 2, 3, 10, 11, 12):
        return -3
    if m in (5, 6, 7, 8):
        return -4

    # Day of week of the 1st day of current month (0=Mon .. 6=Sun)
    wday_1st = (wday - (d - 1)) % 7
    first_sat = (5 - wday_1st) % 7 + 1
    first_sun = first_sat + 1

    if m == 4:
        # April: Winter time begins Sunday 00:00 local (03:00 UTC)
        if d < first_sun or (d == first_sun and h < 3):
            return -3
        return -4
    elif m == 9:
        # September: Summer time begins Sunday 00:00 local (04:00 UTC)
        if d < first_sun or (d == first_sun and h < 4):
            return -4
        return -3

    return -3


def get_effective_timezone_offset(secs=None):
    """Return effective timezone offset in hours"""
    try:
        from config import TIMEZONE_OFFSET_HOURS
        if str(TIMEZONE_OFFSET_HOURS).upper() == "AUTO":
            return get_chile_offset_hours(secs)
        return int(TIMEZONE_OFFSET_HOURS)
    except Exception:
        return get_chile_offset_hours(secs)


def get_utc_time_tuple(secs=None):
    """Return current or specified time tuple in UTC"""
    if secs is None:
        secs = time.time()
    return time.localtime(secs)


def get_local_time_tuple(secs=None):
    """Return current or specified time tuple in Local Time"""
    if secs is None:
        secs = time.time()
    offset = get_effective_timezone_offset(secs)
    local_secs = int(secs + (offset * 3600))
    return time.localtime(local_secs)


def format_timestamp_local(secs=None):
    """Return Local formatted timestamp: YYYY-MM-DD HH:MM:SS"""
    t = get_local_time_tuple(secs)
    return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
        t[0], t[1], t[2], t[3], t[4], t[5]
    )


def format_timestamp_utc(secs=None):
    """Return UTC ISO formatted timestamp: YYYY-MM-DDTHH:MM:SSZ"""
    t = get_utc_time_tuple(secs)
    return "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}Z".format(
        t[0], t[1], t[2], t[3], t[4], t[5]
    )


def get_iso_timestamp(secs=None):
    """Return local formatted timestamp string: YYYY-MM-DD HH:MM:SS"""
    return format_timestamp_local(secs)


def log(msg):
    """Timestamped console log output using local time"""
    t = get_local_time_tuple()
    print("[{:02d}:{:02d}:{:02d}] {}".format(t[3], t[4], t[5], msg))


try:
    import uarray as array
except ImportError:
    import array


def _build_crc_table():
    """Build the CRC-16-CCITT table for Davis Vantage Serial Protocol (compact 512 bytes)"""
    table = array.array("H")
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
        table.append(crc)
    return table


_CRC_TABLE = _build_crc_table()


def verify_crc(packet):
    """
    Verify Davis CRC-16-CCITT checksum.
    Running CRC over entire 99-byte packet (data + 2 CRC bytes) must equal 0.
    """
    crc = 0
    for b in packet:
        crc = ((crc << 8) & 0xFFFF) ^ _CRC_TABLE[((crc >> 8) ^ b) & 0xFF]
    return crc == 0


def calculate_crc(data):
    """Calculate CRC-16-CCITT checksum for arbitrary byte buffer"""
    crc = 0
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ _CRC_TABLE[((crc >> 8) ^ b) & 0xFF]
    return crc


def encode_davis_datetime(year, month, day, hour, minute):
    """Encode date/time into 6-byte Davis filter packet (<HH date/time + >H CRC)"""
    d = day + (month * 32) + ((year - 2000) * 512)
    t = (hour * 100) + minute
    payload = struct.pack("<HH", d, t)
    crc = calculate_crc(payload)
    return payload + struct.pack(">H", crc)


def calculate_dewpoint(temp_f, humidity):
    """Calculate dew point using Magnus formula (°F). Returns None on invalid input."""
    if temp_f is None or humidity is None or humidity <= 0:
        return None
    try:
        temp_c = (temp_f - 32.0) * 5.0 / 9.0
        a = 17.27
        b = 237.7
        alpha = ((a * temp_c) / (b + temp_c)) + math.log(humidity / 100.0)
        dew_c = (b * alpha) / (a - alpha)
        dew_f = (dew_c * 9.0 / 5.0) + 32.0
        return round(dew_f, 1)
    except Exception:
        return None


def calculate_wet_bulb(temp_c, humidity):
    """
    Calculate Wet Bulb temperature (°C) using Stull (2011) formula.
    Accurate within 0.3°C across -20°C to 50°C and RH 5% to 99%.
    Matches Davis WeatherLink bulletin.
    """
    if temp_c is None or humidity is None:
        return None
    try:
        t = float(temp_c)
        rh = float(humidity)
        if rh < 1.0:
            rh = 1.0
        elif rh > 100.0:
            rh = 100.0
        tw = (
            t * math.atan(0.151977 * math.sqrt(rh + 8.313659))
            + math.atan(t + rh)
            - math.atan(rh - 1.676331)
            + 0.00391838 * (rh ** 1.5) * math.atan(0.023101 * rh)
            - 4.686035
        )
        return round(tw, 1)
    except Exception:
        return None


def calculate_wind_chill(temp_c, wind_kmh):
    """
    Calculate Wind Chill (°C) per Environment Canada / NOAA.
    Active when Temp <= 10°C and Wind > 4.8 km/h. Otherwise equals temp_c.
    """
    if temp_c is None:
        return None
    try:
        t = float(temp_c)
        w = float(wind_kmh) if wind_kmh is not None else 0.0
        if t <= 10.0 and w > 4.8:
            wc = 13.12 + (0.6215 * t) - (11.37 * (w ** 0.16)) + (0.3965 * t * (w ** 0.16))
            return round(wc, 1)
        return round(t, 1)
    except Exception:
        return None


def calculate_heat_index(temp_c, humidity):
    """
    Calculate Heat Index (°C) per NOAA / Rothfusz formula.
    Active when Temp >= 26.7°C (80°F) and RH >= 40%. Otherwise equals temp_c.
    """
    if temp_c is None:
        return None
    if humidity is None:
        return round(temp_c, 1)
    try:
        t = float(temp_c)
        rh = float(humidity)
        tf = (t * 9.0 / 5.0) + 32.0
        if tf < 80.0 or rh < 40.0:
            return round(t, 1)
        hi_f = (
            -42.379
            + 2.04901523 * tf
            + 10.14333127 * rh
            - 0.22475541 * tf * rh
            - 0.00683783 * tf * tf
            - 0.05481717 * rh * rh
            + 0.00122874 * tf * tf * rh
            + 0.00085282 * tf * rh * rh
            - 0.00000199 * tf * tf * rh * rh
        )
        return round((hi_f - 32.0) * 5.0 / 9.0, 1)
    except Exception:
        return None


def calculate_thw_index(temp_c, humidity, wind_kmh):
    """
    Calculate THW (Temperature-Humidity-Wind) Index (°C).
    Combines wind chill when cold/windy or heat index adjusted by wind.
    """
    if temp_c is None:
        return None
    try:
        t = float(temp_c)
        w = float(wind_kmh) if wind_kmh is not None else 0.0
        rh = float(humidity) if humidity is not None else 50.0
        if t <= 10.0 and w > 4.8:
            return calculate_wind_chill(t, w)
        elif t >= 26.7 and rh >= 40.0:
            hi = calculate_heat_index(t, rh)
            cooling = (w / 15.0) if w > 5 else 0.0
            return round(max(t, hi - cooling), 1)
        return round(t, 1)
    except Exception:
        return None


def calculate_wind_run(wind_speed_kmh, interval_mins=10):
    """Calculate wind run (km) across interval: km = km/h * (mins / 60)"""
    if wind_speed_kmh is None:
        return 0.0
    try:
        return round(float(wind_speed_kmh) * (interval_mins / 60.0), 2)
    except Exception:
        return 0.0


def calculate_degree_days(temp_c, interval_mins=10):
    """
    Calculate Heating Degree Days (HDD) and Cooling Degree Days (CDD) in °C-days.
    Base temperature = 18.333°C (65°F).
    Sub-daily interval factor: interval_mins / 1440.0
    """
    if temp_c is None:
        return 0.0, 0.0
    try:
        t = float(temp_c)
        factor = interval_mins / 1440.0
        if t < 18.333:
            return round((18.333 - t) * factor, 3), 0.0
        else:
            return 0.0, round((t - 18.333) * factor, 3)
    except Exception:
        return 0.0, 0.0


def calculate_indoor_heat_index(temp_in_c, humidity_in):
    """Calculate indoor heat index (°C) using Steadman / Rothfusz formula"""
    if temp_in_c is None:
        return None
    if humidity_in is None:
        return round(temp_in_c, 1)
    try:
        t = float(temp_in_c)
        rh = float(humidity_in)
        tf = (t * 9.0 / 5.0) + 32.0
        if tf < 80.0:
            hi_f = 0.5 * (tf + 61.0 + ((tf - 68.0) * 1.2) + (rh * 0.094))
            return round((hi_f - 32.0) * 5.0 / 9.0, 1)
        return calculate_heat_index(t, rh)
    except Exception:
        return None


def calculate_emc(temp_in_c, humidity_in):
    """
    Calculate Equilibrium Moisture Content (EMC %) for wood using
    Hailwood-Horrobin / USDA Forest Products Lab equation (Davis App Note 22).
    """
    if temp_in_c is None or humidity_in is None:
        return None
    try:
        tf = (float(temp_in_c) * 9.0 / 5.0) + 32.0
        h = float(humidity_in) / 100.0
        if h <= 0.0:
            return 0.0
        elif h >= 1.0:
            h = 0.999
        w = 330.0 + 0.452 * tf + 0.00415 * (tf ** 2)
        k = 0.791 + 0.000463 * tf - 0.000000844 * (tf ** 2)
        k1 = 6.34 + 0.000775 * tf - 0.0000935 * (tf ** 2)
        k2 = 1.09 + 0.0284 * tf - 0.0000904 * (tf ** 2)
        kh = k * h
        denom = 1.0 + k1 * kh + k1 * k2 * (kh ** 2)
        if denom == 0 or (1.0 - kh) == 0:
            return None
        term1 = kh / (1.0 - kh)
        term2 = (k1 * kh + 2.0 * k1 * k2 * (kh ** 2)) / denom
        emc = (1800.0 / w) * (term1 + term2)
        return round(emc, 2)
    except Exception:
        return None


def calculate_air_density(temp_c, humidity, pressure_hpa):
    """
    Calculate moist air density (kg/m³) matching Davis WeatherLink formula:
    Density = 1.2929 * 273.13 * (AP_mmHg - (SVP_mmHg * RH * 0.3783)) / ((T + 273.13) * 760)
    """
    if temp_c is None or pressure_hpa is None:
        return None
    try:
        t = float(temp_c)
        rh = (float(humidity) / 100.0) if humidity is not None else 0.5
        svp = 4.584 * (10.0 ** ((7.5 * t) / (t + 237.3)))
        ap = float(pressure_hpa) * 0.750062
        density = 1.2929 * 273.13 * (ap - (svp * rh * 0.3783)) / ((t + 273.13) * 760.0)
        return round(density, 4)
    except Exception:
        return None



def format_time_hm(raw_val):
    """Convert Davis packed time (hour * 100 + minute) to 'HH:MM' string"""
    if raw_val is None or raw_val == 0xFFFF or raw_val < 0:
        return ""
    hour = raw_val // 100
    minute = raw_val % 100
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return "{:02d}:{:02d}".format(hour, minute)
    return ""


def format_storm_date(raw_val):
    """Decode Davis storm start date bitmask: year (7b), day (5b), month (4b)"""
    if not raw_val or raw_val == 0xFFFF:
        return ""
    try:
        year = ((raw_val >> 9) & 0x7F) + 2000
        day = (raw_val >> 4) & 0x1F
        month = raw_val & 0x0F
        if 1 <= month <= 12 and 1 <= day <= 31:
            return "{:04d}-{:02d}-{:02d}".format(year, month, day)
    except Exception:
        pass
    return ""


def get_trend_description(trend_code):
    """Map Davis barometer trend code to readable description (Spanish)"""
    if trend_code == -60:
        return "Bajando Rápido"
    elif trend_code == -20:
        return "Bajando Lento"
    elif trend_code == 0:
        return "Estable"
    elif trend_code == 20:
        return "Subiendo Lento"
    elif trend_code == 60:
        return "Subiendo Rápido"
    return "Estable"


def parse_packet(packet):
    """
    Parse full 99-byte Davis Vantage Pro 2 LOOP 1 packet.
    Returns dict with all decoded parameters, or None if corrupted/invalid.
    """
    if len(packet) < 99 or packet[0:3] != b'LOO':
        return None

    if not verify_crc(packet):
        log("CRC check failed - discarding corrupted packet")
        return None

    try:
        # 1. Barometer & Trend (trend at 3, Barometer at 7)
        bar_trend_raw = struct.unpack_from("<b", packet, 3)[0]
        barometer_raw = struct.unpack_from("<H", packet, 7)[0]
        pressure_inhg = round(barometer_raw / 1000.0, 3) if 20000 <= barometer_raw <= 35000 else None
        pressure_hpa = round(pressure_inhg * 33.8639, 1) if pressure_inhg else None

        # 2. Inside Temperature (offset 9) & Humidity (offset 11)
        in_temp_raw = struct.unpack_from("<h", packet, 9)[0]
        temp_in_f = round(in_temp_raw / 10.0, 1) if in_temp_raw != 32767 and -500 <= in_temp_raw <= 1500 else None
        temp_in_c = round((temp_in_f - 32.0) * 5.0 / 9.0, 1) if temp_in_f is not None else None
        in_humidity_raw = struct.unpack_from("<B", packet, 11)[0]
        humidity_in = in_humidity_raw if in_humidity_raw <= 100 else None

        # 3. Outside Temperature (offset 12) & Humidity (offset 33)
        out_temp_raw = struct.unpack_from("<h", packet, 12)[0]
        temp_out_f = round(out_temp_raw / 10.0, 1) if out_temp_raw != 32767 and -500 <= out_temp_raw <= 1500 else None
        temp_out_c = round((temp_out_f - 32.0) * 5.0 / 9.0, 1) if temp_out_f is not None else None
        out_humidity_raw = struct.unpack_from("<B", packet, 33)[0]
        humidity_out = out_humidity_raw if out_humidity_raw <= 100 else None

        # Dew point
        dewpoint_f = calculate_dewpoint(temp_out_f, humidity_out)
        dewpoint_c = round((dewpoint_f - 32.0) * 5.0 / 9.0, 1) if dewpoint_f is not None else None

        # 4. Wind Speed (offset 14), 10m Avg (offset 15), Direction (offset 16)
        wind_speed_raw = struct.unpack_from("<B", packet, 14)[0]
        wind_speed_mph = wind_speed_raw if wind_speed_raw != 255 else None
        wind_speed_kmh = round(wind_speed_mph * 1.60934, 1) if wind_speed_mph is not None else None

        wind_10min_raw = struct.unpack_from("<B", packet, 15)[0]
        wind_10min_avg_mph = wind_10min_raw if wind_10min_raw != 255 else None
        wind_10min_avg_kmh = round(wind_10min_avg_mph * 1.60934, 1) if wind_10min_avg_mph is not None else None

        wind_dir_raw = struct.unpack_from("<H", packet, 16)[0]
        wind_direction_deg = wind_dir_raw if 0 <= wind_dir_raw <= 360 else None

        # 5. Rain Rate (offset 41), UV (offset 43), Solar (offset 44), Storm (offset 46)
        rain_rate_raw = struct.unpack_from("<H", packet, 41)[0]
        rain_rate_in_hr = round(rain_rate_raw / 100.0, 2) if rain_rate_raw != 0xFFFF else 0.0
        rain_rate_mm_hr = round(rain_rate_in_hr * 25.4, 1)

        uv_raw = struct.unpack_from("<B", packet, 43)[0]
        uv_index = round(uv_raw / 10.0, 1) if (uv_raw != 255 and uv_raw != 0) else None

        solar_raw = struct.unpack_from("<H", packet, 44)[0]
        solar_radiation_wm2 = solar_raw if (solar_raw != 32767 and solar_raw != 0xFFFF) else None

        storm_rain_raw = struct.unpack_from("<H", packet, 46)[0]
        storm_rain_in = round(storm_rain_raw / 100.0, 2) if storm_rain_raw != 0xFFFF else None
        storm_rain_mm = round(storm_rain_in * 25.4, 1) if storm_rain_in is not None else None

        storm_start_date_raw = struct.unpack_from("<H", packet, 48)[0]

        # 6. Rain Accumulations (offsets 50, 52, 54)
        day_rain_raw = struct.unpack_from("<H", packet, 50)[0]
        day_rain_in = round(day_rain_raw / 100.0, 2) if day_rain_raw != 0xFFFF else 0.0
        day_rain_mm = round(day_rain_in * 25.4, 1)

        month_rain_raw = struct.unpack_from("<H", packet, 52)[0]
        month_rain_in = round(month_rain_raw / 100.0, 2) if month_rain_raw != 0xFFFF else 0.0
        month_rain_mm = round(month_rain_in * 25.4, 1)

        year_rain_raw = struct.unpack_from("<H", packet, 54)[0]
        year_rain_in = round(year_rain_raw / 100.0, 2) if year_rain_raw != 0xFFFF else 0.0
        year_rain_mm = round(year_rain_in * 25.4, 1)

        # 7. Evapotranspiration (offsets 56, 58, 60)
        day_et_raw = struct.unpack_from("<H", packet, 56)[0]
        day_et_in = round(day_et_raw / 1000.0, 3) if day_et_raw != 0xFFFF else None
        month_et_raw = struct.unpack_from("<H", packet, 58)[0]
        month_et_in = round(month_et_raw / 100.0, 2) if month_et_raw != 0xFFFF else None
        year_et_raw = struct.unpack_from("<H", packet, 60)[0]
        year_et_in = round(year_et_raw / 100.0, 2) if year_et_raw != 0xFFFF else None

        # 8. Diagnostics & Astronomical
        tx_batt_status = struct.unpack_from("<B", packet, 78)[0]
        batt_volt_raw = struct.unpack_from("<H", packet, 87)[0]
        console_battery_v = round(((batt_volt_raw * 300) / 512.0) / 100.0, 2) if (100 <= batt_volt_raw <= 2000) else None

        forecast_icons_raw = struct.unpack_from("<B", packet, 88)[0]
        forecast_rule = struct.unpack_from("<B", packet, 90)[0]
        sunrise_raw = struct.unpack_from("<H", packet, 91)[0]
        sunset_raw = struct.unpack_from("<H", packet, 93)[0]

        # Check Packet Type (offset 4: 0 = LOOP 1, 1 = LOOP 2)
        packet_type = struct.unpack_from("<B", packet, 4)[0]

        # In LOOP 2, wind averages and 10m peak gust are calculated natively by console hardware
        windspdmph_avg2m = None
        windspdkmh_avg2m = None
        windgustmph_10m = None
        windgustkmh_10m = None
        windgustdir_10m = None
        heat_index_c = None
        wind_chill_c = None
        thw_index_c = None

        if packet_type == 1:
            # 10m Avg Wind (offset 18, 10th mph)
            w10_raw = struct.unpack_from("<H", packet, 18)[0]
            if w10_raw != 0x7FFF:
                wind_10min_avg_mph = round(w10_raw / 10.0, 1)
                wind_10min_avg_kmh = round(wind_10min_avg_mph * 1.60934, 1)

            # 2m Avg Wind (offset 20, 10th mph)
            w2_raw = struct.unpack_from("<H", packet, 20)[0]
            if w2_raw != 0x7FFF:
                windspdmph_avg2m = round(w2_raw / 10.0, 1)
                windspdkmh_avg2m = round(windspdmph_avg2m * 1.60934, 1)

            # 10m Peak Wind Gust (offset 22, integer mph in Davis firmware)
            gust_raw = struct.unpack_from("<H", packet, 22)[0]
            if gust_raw != 0x7FFF:
                windgustmph_10m = float(gust_raw)
                windgustkmh_10m = round(windgustmph_10m * 1.60934, 1)

            # 10m Gust Direction (offset 24, degrees)
            gustdir_raw = struct.unpack_from("<H", packet, 24)[0]
            if gustdir_raw != 0x7FFF and 0 <= gustdir_raw <= 360:
                windgustdir_10m = gustdir_raw

        # Wet bulb (Stull 2011)
        wet_bulb_c = calculate_wet_bulb(temp_out_c, humidity_out)

        # Thermal comfort indices (NOAA / Environment Canada / WeatherLink standard)
        heat_index_c = calculate_heat_index(temp_out_c, humidity_out)
        wind_chill_c = calculate_wind_chill(temp_out_c, wind_speed_kmh)
        thw_index_c = calculate_thw_index(temp_out_c, humidity_out, wind_speed_kmh)

        # Indoor derived metrics & Degree days
        in_dew_f = calculate_dewpoint(temp_in_f, humidity_in)
        in_dew_c = round((in_dew_f - 32.0) * 5.0 / 9.0, 1) if in_dew_f is not None else None
        in_heat_c = calculate_indoor_heat_index(temp_in_c, humidity_in)
        in_emc = calculate_emc(temp_in_c, humidity_in)
        in_air_density = calculate_air_density(temp_in_c, humidity_in, pressure_hpa)
        heat_dd, cool_dd = calculate_degree_days(temp_out_c, 10)
        effective_w = wind_10min_avg_kmh if wind_10min_avg_kmh is not None else wind_speed_kmh
        wind_run_km = calculate_wind_run(effective_w, 10)

        return {
            "timestamp": format_timestamp_local(),
            "timestamp_utc": format_timestamp_utc(),
            "packet_type": packet_type,
            # Barometer
            "pressure_inhg": pressure_inhg,
            "pressure_hpa": pressure_hpa,
            "barometer_trend_code": bar_trend_raw,
            "barometer_trend": get_trend_description(bar_trend_raw),
            # Outdoor Temp / Hum / Dew
            "temp_out_f": temp_out_f,
            "temp_out_c": temp_out_c,
            "humidity_out": humidity_out,
            "dewpoint_f": dewpoint_f,
            "dewpoint_c": dewpoint_c,
            "wet_bulb_c": wet_bulb_c,
            # Indoor
            "temp_in_f": temp_in_f,
            "temp_in_c": temp_in_c,
            "humidity_in": humidity_in,
            "in_dew_c": in_dew_c,
            "in_heat_c": in_heat_c,
            "in_emc": in_emc,
            "in_air_density": in_air_density,
            # Wind
            "wind_speed_mph": wind_speed_mph,
            "wind_speed_kmh": wind_speed_kmh,
            "wind_direction_deg": wind_direction_deg,
            "wind_speed_10min_avg_mph": wind_10min_avg_mph,
            "wind_speed_10min_avg_kmh": wind_10min_avg_kmh,
            "windspdmph_avg2m": windspdmph_avg2m,
            "windspdkmh_avg2m": windspdkmh_avg2m,
            "windgustmph_10m": windgustmph_10m,
            "windgustkmh_10m": windgustkmh_10m,
            "windgustdir_10m": windgustdir_10m,
            "wind_run_km": wind_run_km,
            # Thermal Comfort
            "heat_index_c": heat_index_c,
            "wind_chill_c": wind_chill_c,
            "thw_index_c": thw_index_c,
            # Degree Days
            "heat_dd": heat_dd,
            "cool_dd": cool_dd,
            # Rain
            "rain_rate_in_per_hr": rain_rate_in_hr,
            "rain_rate_mm_per_hr": rain_rate_mm_hr,
            "storm_rain_in": storm_rain_in,
            "storm_rain_mm": storm_rain_mm if storm_rain_mm is not None else 0.0,
            "storm_start_date": format_storm_date(storm_start_date_raw),
            "rain_day_in": day_rain_in,
            "rain_day_mm": day_rain_mm,
            "rain_month_in": month_rain_in,
            "rain_month_mm": month_rain_mm,
            "rain_year_in": year_rain_in,
            "rain_year_mm": year_rain_mm,
            # Solar & UV
            "solar_radiation_wm2": solar_radiation_wm2,
            "uv_index": uv_index,
            # Evapotranspiration
            "day_et_in": day_et_in,
            "month_et_in": month_et_in,
            "year_et_in": year_et_in,
            # Diagnostics & Astronomical
            "console_battery_v": console_battery_v,
            "tx_battery_status": tx_batt_status,
            "wind_samples": 225,
            "wind_tx": 1,
            "iss_recept": 100.0,
            "arc_int": 10,
            "sunrise": format_time_hm(sunrise_raw),
            "sunset": format_time_hm(sunset_raw),
            "forecast_icons": forecast_icons_raw,
            "forecast_rule": forecast_rule,
        }
    except Exception as e:
        log("Error parsing packet: {}".format(e))
        return None


def wakeup_console(sock, max_retries=2):
    """
    Wake up Davis Vantage Pro 2 console via newline sequence.
    Per Davis specification, console enters power-saving sleep after 10s of serial inactivity.
    Transmitting '\n' wakes it up, responding with '\n\r' within 1.2s.
    """
    for _ in range(max_retries):
        try:
            sock.sendall(b"\n")
            time.sleep(0.12)
            resp = sock.recv(64)
            if b"\n" in resp or b"\r" in resp or b"LOO" in resp:
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def query_station(host, port, timeout=3.5):
    """
    On-demand station query: requests 1x LOOP 1 and 1x LOOP 2 via LPS 3 2.
    Decodes consolidated live metrics (including Davis console hardware 10m gusts
    and 2m sustained wind), and immediately closes the TCP socket to free WeatherLink IP.
    Falls back gracefully to standard LOOP 1 if LOOP 2 is unavailable.
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except Exception:
            pass
        sock.settimeout(timeout)
        sock.connect((host, port))

        # Clear stale buffer
        try:
            sock.setblocking(False)
            while True:
                junk = sock.recv(1024)
                if not junk:
                    break
        except Exception:
            pass
        finally:
            try:
                sock.setblocking(True)
            except Exception:
                pass
            sock.settimeout(timeout)

        wakeup_console(sock)

        # Request 1x LOOP 1 + 1x LOOP 2
        sock.sendall(b"LPS 3 2\n")

        # Receive 1 ACK byte (0x06) + 2x 99-byte packets = 199 bytes
        buf = b""
        start_t = time.time()
        while len(buf) < 198 and (time.time() - start_t) < timeout:
            chunk = sock.recv(256)
            if not chunk:
                break
            buf += chunk

        if len(buf) < 99:
            return None

        # Strip leading ACK if present
        if buf.startswith(b"\x06"):
            buf = buf[1:]

        # Align to 'LOO'
        idx1 = buf.find(b"LOO")
        if idx1 == -1 or len(buf) < idx1 + 99:
            return None

        p1 = buf[idx1 : idx1 + 99]
        data1 = parse_packet(p1)
        if not data1:
            return None

        # Check for second packet (LOOP 2)
        rem = buf[idx1 + 99 :]
        idx2 = rem.find(b"LOO")
        if idx2 != -1 and len(rem) >= idx2 + 99:
            p2 = rem[idx2 : idx2 + 99]
            data2 = parse_packet(p2)
            if data2:
                # Merge LOOP 2 metrics into data1
                for k in (
                    "windgustkmh_10m", "windgustmph_10m", "windgustdir_10m",
                    "windspdkmh_avg2m", "windspdmph_avg2m",
                    "wind_speed_10min_avg_kmh", "wind_speed_10min_avg_mph"
                ):
                    if data2.get(k) is not None:
                        data1[k] = data2[k]
                if data1.get("wind_speed_10min_avg_kmh") is not None:
                    data1["wind_run_km"] = calculate_wind_run(data1["wind_speed_10min_avg_kmh"], 10)

        # Meteorologically, the 10m rolling peak gust must be >= current instantaneous wind
        curr_kmh = data1.get("wind_speed_kmh")
        if curr_kmh is not None:
            gust_kmh = data1.get("windgustkmh_10m")
            if gust_kmh is None or gust_kmh < curr_kmh:
                data1["windgustkmh_10m"] = curr_kmh
                data1["windgustdir_10m"] = data1.get("wind_direction_deg")
                data1["windgustmph_10m"] = data1.get("wind_speed_mph")

        # Ensure thermal indices and wet bulb are fully populated
        t_out = data1.get("temp_out_c")
        h_out = data1.get("humidity_out")
        w_spd = data1.get("wind_speed_kmh")
        if t_out is not None:
            if data1.get("wet_bulb_c") is None:
                data1["wet_bulb_c"] = calculate_wet_bulb(t_out, h_out)
            if data1.get("heat_index_c") is None:
                data1["heat_index_c"] = calculate_heat_index(t_out, h_out)
            if data1.get("wind_chill_c") is None:
                data1["wind_chill_c"] = calculate_wind_chill(t_out, w_spd)
            if data1.get("thw_index_c") is None:
                data1["thw_index_c"] = calculate_thw_index(t_out, h_out, w_spd)

        return data1
    except Exception as e:
        err_msg = str(e).lower()
        if "timed out" in err_msg or "10054" in err_msg or "10061" in err_msg:
            log("WeatherLink IP ocupado o sincronizando a nube...")
        else:
            log("Error consultando estacion: {}".format(e))
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


