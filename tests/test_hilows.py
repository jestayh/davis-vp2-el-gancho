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


def loop_packet(kind):
    data = bytearray(97)
    data[0:3] = b"LOO"
    data[4] = kind                                      # 0 = LOOP, 1 = LOOP2
    struct.pack_into("<h", data, 12, 520)               # 52.0 F outside
    data[33] = 80                                       # 80 % humidity
    return bytes(data) + struct.pack(">H", dr.calculate_crc(bytes(data)))


class FakeConsole:
    """WeatherLink IP stand-in: answers wake-up, LPS and HILOWS; counts connections"""
    connections = 0

    def __init__(self, hilows_reply):
        self.hilows_reply = hilows_reply
        self.pending = b""
        self.blocking = True

    def __call__(self, *a):
        FakeConsole.connections += 1
        return self

    def sendall(self, cmd):
        if cmd == b"\n":
            self.pending += b"\n\r"
        elif cmd.startswith(b"LPS"):
            self.pending += b"\x06" + loop_packet(0) + loop_packet(1)
        elif cmd == b"HILOWS\n":
            self.pending += self.hilows_reply

    def recv(self, n):
        if not self.pending and not self.blocking:
            raise OSError("EAGAIN")
        out, self.pending = self.pending[:n], self.pending[n:]
        return out

    def setblocking(self, flag):
        self.blocking = flag

    def settimeout(self, t):
        self.blocking = True

    def connect(self, addr):
        pass

    def setsockopt(self, *a):
        pass

    def shutdown(self, how):
        pass

    def close(self):
        pass


def test_extremes_come_in_the_same_connection_as_the_live_reading(monkeypatch):
    FakeConsole.connections = 0
    monkeypatch.setattr(dr.socket, "socket", FakeConsole(b"\x06" + packet()))
    data = dr.query_station("console", 22222, timeout=1, hilows=True)
    assert FakeConsole.connections == 1
    assert data["temp_out_c"] == 11.1 and data["today_temp_hi_c"] == 18.2 and data["today_gust_time"] == "14:40"


def test_live_reading_survives_a_failed_extremes_request(monkeypatch):
    monkeypatch.setattr(dr.socket, "socket", FakeConsole(b"\x21"))          # NAK
    data = dr.query_station("console", 22222, timeout=1, hilows=True)
    assert data["temp_out_c"] == 11.1 and "today_temp_hi_c" not in data
    data = dr.query_station("console", 22222, timeout=1)                     # not asked: not sent
    assert "today_temp_hi_c" not in data


def test_corrupted_or_short_block_is_rejected():
    p = bytearray(packet())
    p[50] ^= 0x01
    assert dr.parse_hilows(bytes(p)) is None
    assert dr.parse_hilows(packet()[:300]) is None
