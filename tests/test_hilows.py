"""Today's extremes from the console's HILOWS block (src/davis_reader.parse_hilows)"""

import struct

import src.davis_reader as dr


def packet(lo_f=375, hi_f=648, t_lo=640, t_hi=1510, gust_mph=28, t_gust=1440, rate=90, t_rate=1200):
    """436-byte block laid out as in the Vantage serial reference (X.3), plus its CRC"""
    data = bytearray(dr.HILOWS_LEN)
    struct.pack_into("<hhHH", data, 47, lo_f, hi_f, t_lo, t_hi)
    data[16] = gust_mph
    struct.pack_into("<H", data, 17, t_gust)
    struct.pack_into("<HH", data, 116, rate, t_rate)
    return bytes(data) + struct.pack(">H", dr.calculate_crc(bytes(data)))


def test_today_extremes_in_metric_units():
    h = dr.parse_hilows(packet())
    assert h == {
        "today_temp_hi_c": 18.2, "today_temp_hi_time": "15:10",     # 64.8 F
        "today_temp_lo_c": 3.1, "today_temp_lo_time": "06:40",      # 37.5 F
        "today_gust_kmh": 45.1, "today_gust_time": "14:40",         # 28 mph
        "today_rain_rate_mmh": 22.9, "today_rain_rate_time": "12:00",
    }


def test_below_zero_and_dashed_values():
    h = dr.parse_hilows(packet(lo_f=280, hi_f=32767, t_hi=0xFFFF, gust_mph=255))
    assert h["today_temp_lo_c"] == -2.2
    assert h["today_temp_hi_c"] is None and h["today_temp_hi_time"] is None
    assert h["today_gust_kmh"] is None


def test_corrupted_or_short_block_is_rejected():
    p = bytearray(packet())
    p[50] ^= 0x01
    assert dr.parse_hilows(bytes(p)) is None
    assert dr.parse_hilows(packet()[:300]) is None
