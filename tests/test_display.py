"""OLED screen (src/display.py) drawn on a fake SSD1306, with a fake monotonic clock"""

import pytest

import src.display as dm
from oled import FakeOled


class Clock:
    def __init__(self):
        self.t = 1000.0

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(dm, "_ticks", lambda: int(c.t * 1000))
    # Each sleep also accounts for ~30 ms of I2C transfer per frame on the ESP32
    monkeypatch.setattr(dm, "_sleep_ms", lambda ms: c.advance((ms + 30) / 1000.0))
    return c


@pytest.fixture
def screen(clock):
    d = dm.WeatherDisplay()
    d.oled = FakeOled()
    d.has_oled = True
    return d


def show(d, clock, hhmm, data=None, day="2026-10-11", seconds=5):
    """One card of the rotation at local time hhmm, with a fresh console reading"""
    clock.advance(seconds)
    data = dict(data or {}, timestamp="{} {}:00".format(day, hhmm))
    d.show_weather(data, now_str="{} {}:01".format(day, hhmm))
    return d.oled


WEATHER = {"temp_out_c": 15.3, "humidity_out": 72, "wind_speed_kmh": 12.9, "wind_direction_deg": 22,
           "rain_rate_mm_per_hr": 0, "rain_day_mm": 0, "console_battery_v": 4.24, "tx_battery_status": 0}


# --- header, alerts and night mode ---

def test_day_header_is_thin_line_with_card_and_clock(screen, clock):
    oled = show(screen, clock, "14:00", WEATHER)
    assert [t[0] for t in oled.header()] == ["AHORA", "14:00"]
    assert oled.lit(0, 4) == 0 and oled.lit(14, 15) == 128   # no bar: just the line under the text
    assert ("contrast", dm.CONTRAST_DAY) in oled.cmds


def test_stale_console_data_turns_header_into_alert(screen, clock):
    data = dict(WEATHER, timestamp="2026-10-11 14:00:00")
    screen.show_weather(data, now_str="2026-10-11 14:00:01")
    clock.advance(180)
    screen.show_weather(data, now_str="2026-10-11 14:03:01")    # same reading 3 min later
    texts = screen.oled.header()
    assert [t[0] for t in texts] == ["SIN DATOS", "3m"] and texts[0][3] == 0
    assert screen.oled.lit(0, 4) == 128 * 4                     # inverted bar above the text


def test_failed_sheets_upload_shows_alert_until_next_success(screen, clock):
    screen.update_cloud_status(gdrive_ok=False, timestamp_str="2026-10-11 14:00:00")
    assert show(screen, clock, "14:00", WEATHER).header()[0][0] == "NO SHEETS"
    screen.update_cloud_status(gdrive_ok=True, timestamp_str="2026-10-11 14:10:00")
    assert show(screen, clock, "14:10", WEATHER).header()[0][0] != "NO SHEETS"


def test_night_turns_screen_off_unless_there_is_an_alert(screen, clock):
    show(screen, clock, "23:30", WEATHER)
    show(screen, clock, "23:31", WEATHER)
    assert screen.oled.cmds.count("off") == 1                   # not repeated every card
    frames = len(screen.oled.frames)
    data = dict(WEATHER, timestamp="2026-10-11 23:31:00")
    clock.advance(600)
    screen.show_weather(data, now_str="2026-10-11 23:41:01")     # console silent 10 min
    assert screen.oled.cmds[-2:] == ["on", ("contrast", dm.CONTRAST_NIGHT)]
    assert len(screen.oled.frames) > frames and screen.oled.header()[0][0] == "SIN DATOS"
    show(screen, clock, "07:00", WEATHER, day="2026-10-12")
    assert screen.oled.cmds[-1] == ("contrast", dm.CONTRAST_DAY)


def test_invalid_clock_disables_night_mode_and_header_time(screen, clock):
    oled = show(screen, clock, "03:00", WEATHER, day="2000-01-01")
    assert "off" not in oled.cmds and len(oled.header()) == 1


def test_wake_keeps_screen_on_at_night_for_a_while(screen, clock):
    screen.wake(dm.WAKE_SECONDS)
    show(screen, clock, "23:30", WEATHER)
    assert "off" not in screen.oled.cmds
    clock.advance(dm.WAKE_SECONDS - 60)
    show(screen, clock, "23:44", WEATHER)
    assert "off" not in screen.oled.cmds
    clock.advance(120)
    show(screen, clock, "23:46", WEATHER)
    assert screen.oled.cmds[-1] == "off"
    screen.wake(60)                                             # e.g. an OTA update found
    assert screen.oled.cmds[-1] == "on"


def test_format_age():
    assert dm._format_age(59) == "0m" and dm._format_age(5 * 3600) == "5h"
    assert dm._format_age(200 * 3600) == "99h+"


# --- cards ---

def test_rotation_alternates_big_temperature_and_forecast(screen, clock):
    titles = [show(screen, clock, "14:%02d" % i, WEATHER).header()[0][0] for i in range(12)]
    assert titles[0::2] == ["AHORA", "PRONOST."] * 3
    assert titles[1::2] == ["EXTERIOR", "VIENTO", "SOL/LUNA", "SISTEMA", "SUBIDAS", "EXTERIOR"]


def test_rain_card_only_while_it_rains(screen, clock):
    rain = dict(WEATHER, rain_rate_mm_per_hr=2.5, rain_day_mm=1.2)
    titles = [show(screen, clock, "15:%02d" % i, rain).header()[0][0] for i in range(12)]
    assert "LLUVIA" in titles
    assert all(a != b for a, b in zip(titles, titles[1:]))      # never the same card twice in a row


def test_big_temperature_card(screen, clock):
    oled = show(screen, clock, "14:00", WEATHER)
    assert oled.lit(18, 46) > 200                               # large digits
    assert any(t[0].strip() == "72% 13km/h NNE" for t in oled.last_texts())


def test_big_temperature_fits_with_three_digits_and_sign(screen, clock):
    show(screen, clock, "14:00", dict(WEATHER, temp_out_c=-12.3))   # strict FakeOled: stays on screen


def test_system_card(screen, clock):
    screen.set_system_info(fw="1.0.13", reset="Reset hardware")
    screen.set_iss_recept(99.6)
    screen.card_index = dm._ROTATION.index("sistema")
    screen.last_card_title = "AHORA"
    oled = show(screen, clock, "14:00", WEATHER)
    lines = [t[0] for t in oled.last_texts() if t[2] > 4]
    assert lines == ["FW: v1.0.13", "ISS 100% Tx OK", "Consola: 4.24V", "Inicio Reset HW"]


# --- forecast ---

ICONS = {"sol": 0x08, "parcial": 0x06, "nublado": 0x02, "lluvia": 0x03, "nieve": 0x12, "aguanieve": 0x13}


@pytest.mark.parametrize("kind", sorted(ICONS))
@pytest.mark.parametrize("hhmm,dark", [("14:00", False), ("21:00", True)])
def test_forecast_icons_stay_in_their_area(screen, clock, kind, hhmm, dark):
    assert dm._forecast_kind(ICONS[kind], dark)[0] == kind
    screen._forecast_turn = False                               # next "ahora" -> forecast card
    data = dict(WEATHER, forecast_icons=ICONS[kind], sunrise="07:12", sunset="19:58")
    oled = show(screen, clock, hhmm, data)
    assert len(oled.frames) == dm.FORECAST_FRAMES
    for px, texts in oled.frames:
        assert texts[0][0] == "PRONOST."
        icon = [(x, y) for y in range(64) for x in range(60) if px[y][x]]
        assert icon and all(15 <= y < 56 for x, y in icon if y > 14)   # below header, above ticker row


def test_every_forecast_rule_has_a_spanish_message():
    assert len(dm._FC_RULES) == 197
    msgs = [dm.forecast_message(r) for r in range(197)]
    assert all(m and m.isascii() for m in msgs)                 # the OLED font has no accents
    assert msgs[0] == "Despejado y mas frio."
    assert msgs[193] == "Pronostico requiere 3 h de datos."
    assert msgs[93] == "Nubes en aumento y mas frio. Lluvia y viento posibles en 6 h."
    assert dm.forecast_message(200) is None and dm.forecast_message(None) is None


@pytest.mark.parametrize("rule", [1, 178])
def test_forecast_message_scrolls_once_right_to_left(screen, clock, rule):
    screen._forecast_turn = False
    t0 = clock.t
    oled = show(screen, clock, "10:00", dict(WEATHER, forecast_icons=0x03, forecast_rule=rule), seconds=0)
    took = clock.t - t0
    assert dm.TICKER_MIN_S - 0.1 <= took <= dm.TICKER_MAX_S + 0.1
    xs = [t[1] for _, texts in oled.frames for t in texts if t[2] == 56]
    assert xs[0] > 100 and min(xs) < 8                          # enters right, leaves left
    assert not any(t[2] == 56 for t in oled.last_texts())       # ends off screen
    for px, texts in oled.frames:
        assert all(t[2] in (4, 20, 32, 44, 56) for t in texts)


def test_sun_and_moon_card(screen, clock):
    screen.card_index = dm._ROTATION.index("sol")
    screen.last_card_title = "AHORA"
    oled = show(screen, clock, "14:00", dict(WEATHER, sunrise="07:12", sunset="19:58"), day="2026-10-26")
    lines = [t[0] for t in oled.last_texts() if t[2] > 4]
    assert lines == ["Salida: 07:12", "Puesta: 19:58", "Luz: 12h 46m", "Luna: Llena"]
    assert not any("[" in t[0] for t in oled.last_texts())       # no more bracket "icons"


def test_moon_phase_on_known_dates():
    assert dm._moon_phase("2024-04-08") == "Nueva"               # total solar eclipse
    assert dm._moon_phase("2026-10-10") == "Nueva"
    assert dm._moon_phase("2026-10-26") == "Llena"
    assert dm._moon_phase("") is None and dm._day_length("--:--", "19:58") == "--"


def test_longest_forecast_message_scrolls_at_reading_speed():
    longest = max(len(dm.forecast_message(r)) for r in range(197))
    assert (128 + 8 * longest) / dm.TICKER_PX_PER_S <= dm.TICKER_MAX_S
    assert dm.TICKER_PX_PER_S <= 50                              # about 6 characters per second
