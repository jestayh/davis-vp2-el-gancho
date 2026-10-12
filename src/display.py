"""
OLED Display & Status LED Manager for Davis Weather Station
Compatible with SSD1306 128x64 I2C OLED on ESP32 & PC simulation
Features smooth vertical slide-up animations and multi-card metric carousel.
Gracefully handles absent display hardware without crashing.
"""

import sys
import time

IS_ESP32 = sys.platform == "esp32"


def _sleep_ms(ms):
    if IS_ESP32:
        time.sleep_ms(ms)
    else:
        time.sleep(ms / 1000.0)


def _ticks():
    """Monotonic milliseconds: unlike time.time(), it does not jump when NTP sets the clock at boot"""
    return time.ticks_ms() if IS_ESP32 else int(time.time() * 1000)


def _since_s(t0):
    return (time.ticks_diff(_ticks(), t0) if IS_ESP32 else _ticks() - t0) / 1000.0


def _format_datetime_short(ts_str):
    """Convert 'YYYY-MM-DD HH:MM:SS' to 'DD/MM HH:MM:SS' (14 chars)"""
    if not ts_str or len(ts_str) < 19:
        return "--/-- --:--:--"
    try:
        day = ts_str[8:10]
        month = ts_str[5:7]
        time_part = ts_str[11:19]
        return "{}/{} {}".format(day, month, time_part)
    except Exception:
        return "--/-- --:--:--"


def _get_compass_point(deg):
    if deg is None:
        return ""
    sectors = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
               "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    idx = int((deg + 11.25) / 22.5) % 16
    return sectors[idx]


_MOON_NAMES = ("Nueva", "Creciente", "Cuarto crec", "Gibosa crec", "Llena", "Gibosa meng",
               "Cuarto meng", "Menguante")


def _days_from_civil(y, m, d):
    """Days since 1970-01-01 (proleptic Gregorian), integer math only"""
    y -= m <= 2
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _moon_phase(date_str):
    """Moon phase name for 'YYYY-MM-DD', from the mean synodic month and the new moon of 2000-01-06"""
    try:
        days = _days_from_civil(int(date_str[0:4]), int(date_str[5:7]), int(date_str[8:10])) + 0.5
    except (ValueError, TypeError):
        return None
    age = (days - 10962.76) % 29.530589          # 10962.76 = 2000-01-06 18:14 UTC
    return _MOON_NAMES[int(age / 29.530589 * 8 + 0.5) % 8]


def _day_length(sunrise, sunset):
    """'12h 46m' between two console times 'HH:MM'"""
    try:
        minutes = (int(sunset[:2]) * 60 + int(sunset[3:5])) - (int(sunrise[:2]) * 60 + int(sunrise[3:5]))
    except (ValueError, TypeError):
        return "--"
    return "{}h {:02d}m".format(minutes // 60, minutes % 60) if minutes > 0 else "--"


def _center(s, width=16):
    s = str(s)
    if len(s) >= width:
        return s[:width]
    pad = (width - len(s)) // 2
    return " " * pad + s


_TARGET_Y = (18, 29, 41, 53)
_SHIFTS = (6, 14, 22, 30, 38, 48)
_FIRST_SHIFTS = (32, 20, 10, 0)

# OLED pixels wear out with use: the header is drawn as text over a thin line (a solid bar would
# stay lit 24/7) and is only inverted to flag a problem. At night the screen goes dark unless
# there is a problem, which is then shown at low brightness.
NIGHT_START_HOUR = 23
NIGHT_END_HOUR = 7
CONTRAST_DAY = 0x60
CONTRAST_NIGHT = 0x01
# After a boot (an OTA install ends in one) or when an update is found, the screen stays on this long
WAKE_SECONDS = 15 * 60
# The console is polled every 30 s: with no new reading for this long the data on screen is stale
STALE_DATA_SECONDS = 120

# The rotation shows the weather; the big temperature ("ahora", alternating with the forecast)
# comes back between the others. "problema" only appears when something is wrong, and "sol"
# every other round. SISTEMA and SUBIDAS are for visits: shown while diagnostics are on (BOOT).
_ROTATION = ("ahora", "hoy", "ahora", "viento", "ahora", "lluvia",
             "ahora", "aire", "ahora", "problema", "ahora", "sol")
_DIAG_ROTATION = ("sistema", "subidas")
DIAG_SECONDS = 120
# Seconds each card stays (the main loop waits for it); the forecast takes as long as its message
CARD_SECONDS = {"ahora": 6, "hoy": 10, "viento": 8, "lluvia": 8, "aire": 8, "sol": 8,
                "problema": 10, "sistema": 10, "subidas": 10}
RAIN_CARD_SECONDS = 12             # LLUVIA while it is raining
# Weather alerts in the header (daytime only: they never light the screen at night)
FROST_WARN_C = 2.0
GUST_ALERT_KMH = 50
ISS_RECEPT_LOW = 80
CONSOLE_BATTERY_LOW_V = 3.5
_RESET_SHORT = {"Reset hardware": "Reset HW", "Reinicio software": "Reset SW"}

# Sun rays: unit directions x10 (integer math)
_RAYS = ((10, 0), (7, 7), (0, 10), (-7, 7), (-10, 0), (-7, -7), (0, -10), (7, -7))
FORECAST_FRAMES = 10
FORECAST_FRAME_MS = 90
# Scrolling forecast message: pixels per second, and how long the card may take for one pass
TICKER_PX_PER_S = 45          # ~5-6 characters per second: readable at a glance
TICKER_MIN_S = 5.0
TICKER_MAX_S = 22.0           # the longest message (~100 characters) still runs at full speed

# Davis forecast messages (console byte "forecast rule", 0-196), rebuilt in Spanish from short
# pieces to keep RAM low. Source: Davis rule table published by Ocean Controls (KTA-282).
_FC_PIECES = {
    "A": "Despejado y mas frio.",
    "B": "Despejado, temp. estable.",
    "C": "Despejado por 12 h, temp. estable.",
    "D": "Despejado de 12 a 24 h y mas frio.",
    "E": "Parcialmente nublado y mas frio.",
    "F": "Parcialmente nublado, temp. estable.",
    "G": "Despejado y mas calido.",
    "H": "Nubes en aumento y mas calido.",
    "I": "Nubes en aumento, temp. estable.",
    "J": "Nubes en aumento y mas frio.",
    "K": "Despejando y mas frio.",
    "L": "Nublado y mas frio.",
    "M": "Nublado, temp. estable.",
    "N": "Despejando, mas frio y ventoso.",
    "a": "Lluvia posible en 6 h.",
    "b": "Lluvia posible en 6 a 12 h.",
    "c": "Lluvia posible en 12 h.",
    "d": "Lluvia posible en 12 a 24 h.",
    "e": "Lluvia posible en 24 h.",
    "f": "Lluvia posible en 24 a 48 h.",
    "g": "Lluvia posible en 48 h.",
    "h": "Lluvia probable.",
    "i": "Lluvia continua.",
    "j": "Lluvia termina en 6 h.",
    "k": "Lluvia termina en 12 h.",
    "l": "Lluvia termina en 12 a 24 h.",
    "m": "A ratos intensa.",
    "n": "Lluvia a ratos intensa, termina en 12 h.",
    "o": "Lluvia y viento posibles en 6 h.",
    "w": "Viento en aumento.",
    "x": "Ventoso.",
    "y": "Posible giro del viento al O/NO/N.",
    "z": "Ventoso, posible giro al O/NO/N.",
    "X": "Pronostico requiere 3 h de datos.",
}
# Rule number -> pieces (index = rule number, 0 to 196)
_FC_RULES = (
    "A,B,C,D,B,E,F,F,G,F,"          # 0-9
    "F,B,Hf,F,B,Ie,B,F,B,Ic,"       # 10-19
    "B,F,B,He,Gw,F,B,Hcw,Gw,H,"     # 20-29
    "F,B,Hcw,Gw,H,F,B,Hcw,F,B,"     # 30-39
    "Gg,G,F,B,If,I,F,B,Hd,F,"       # 40-49
    "B,Hdx,F,B,Hdx,F,B,Hb,F,B,"     # 50-59
    "Hbx,F,B,Hdx,F,B,Hc,F,B,Hh,"    # 60-69
    "Kj,F,Kj,B,Kj,E,F,A,Kj,B,"      # 70-79
    "Kj,A,F,B,Ie,Li,F,B,Lh,Mi,"     # 80-89
    "Mh,F,B,Jo,Io,Liw,F,B,Lhw,Miw," # 90-99
    "Mhw,F,B,Jdy,Idy,F,B,Jay,Iay,Lky,"  # 100-109
    "Ly,Mky,My,Lky,F,B,Ley,Mky,Mey,Nj,"  # 110-119
    "N,Ljz,Lz,N,F,B,Mcx,F,B,Jcmx,"  # 120-129
    "Ljx,F,B,Lcx,Ll,L,Limx,F,B,Lbx," # 130-139
    "Mimx,F,B,Mbx,F,B,Icmx,Lx,Limx,F,"  # 140-149
    "B,Lhmx,Mimx,Mhmx,F,B,Jax,Iax,Jiz,F,"  # 150-159
    "B,Lhz,Miz,Mhz,Jaz,F,B,Jay,Iaz,Iay,"  # 160-169
    "F,B,Jaz,Iaz,F,B,Jdz,Idz,Lnz,F,"  # 170-179
    "B,Lbmz,Mkz,Mbmz,Li,F,B,Lhz,Mi,Mh,"  # 180-189
    "F,B,Lcmx,X,A,A,A"              # 190-196
).split(",")


def forecast_message(rule):
    """Spanish forecast message for a Davis forecast rule number (None if unknown)"""
    if rule is None or not 0 <= rule < len(_FC_RULES):
        return None
    return " ".join(_FC_PIECES[p] for p in _FC_RULES[rule])


def _forecast_kind(icons, night):
    """(icon kind, label) from the Davis forecast icon bits: 1 rain, 2 cloud, 4 partly cloudy, 8 sun, 16 snow"""
    if icons is None:
        return None, "Sin dato"
    if icons & 0x01 and icons & 0x10:
        return "aguanieve", "Lluv+Niv"
    if icons & 0x10:
        return "nieve", "Nieve"
    if icons & 0x01:
        return "lluvia", "Lluvia"
    if icons & 0x04:
        return "parcial", "Parcial"
    if icons & 0x02:
        return "nublado", "Nublado"
    if icons & 0x08:
        return "sol", "Despej." if night else "Soleado"
    return None, "Sin dato"


def _is_dark(now_str, data):
    """Before sunrise or after sunset (console times "HH:MM"); falls back to 20:00-07:00"""
    if not now_str or now_str[:4] < "2025":
        return False
    hm = now_str[11:16]
    rise = str(data.get("sunrise") or "07:00")
    sset = str(data.get("sunset") or "20:00")
    if len(rise) != 5 or len(sset) != 5:
        rise, sset = "07:00", "20:00"
    return hm < rise or hm >= sset


def _is_night(hour):
    if NIGHT_START_HOUR > NIGHT_END_HOUR:
        return hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR
    return NIGHT_START_HOUR <= hour < NIGHT_END_HOUR


def _format_age(seconds):
    minutes = int(seconds // 60)
    if minutes < 60:
        return "{}m".format(minutes)
    hours = minutes // 60
    return "{}h".format(hours) if hours < 100 else "99h+"


class WeatherDisplay:
    def __init__(self, sda_pin=21, scl_pin=22, oled_addr=0x3C, width=128, height=64, led_pin=2, led_mode="HEARTBEAT"):
        self.width = width
        self.height = height
        self.oled = None
        self.has_oled = False
        self.led = None
        self.led_mode = led_mode
        self.led_until = 0
        self.card_index = 0
        self.errors = 0          # cards that failed to draw (shown on screen, see show_weather)
        self.last_error = None
        self.last_card_lines = None
        self.last_card_title = None
        self.wu_tag = "WU: --"
        self.wu_status = "ESPERA"
        self.wu_datetime = "--/-- --:--:--"
        self.gdrive_status = "ESPERA"
        self.gdrive_datetime = "--/-- --:--:--"
        self.ip_addr = "192.168.1.6"
        self.wu_station_id = "ISANTA2468"
        self._last_data_ts = None
        self._last_data_change = _ticks()
        self._awake_from = 0
        self._awake_for = 0
        self._screen_off = False
        self._contrast = None
        self.fw_version = None
        self.reset_cause = None
        self.iss_recept = None
        self._forecast_turn = True    # the first "ahora" shows the big temperature
        self._round = 0               # completed rotations (SOL/LUNA every other one)
        self._diag_from = 0
        self._diag_on = False
        self._diag_index = 0
        self._card_from = None        # when the current card appeared, and for how long it stays
        self._card_hold = 0

        if not IS_ESP32:
            return

        # Initialize Status LED on ESP32
        if led_pin is not None:
            try:
                from machine import Pin
                self.led = Pin(led_pin, Pin.OUT)
                self.led.value(0)
            except Exception:
                self.led = None

        try:
            from machine import Pin, I2C
            from src.ssd1306 import SSD1306_I2C

            self.i2c = I2C(0, scl=Pin(scl_pin), sda=Pin(sda_pin), freq=400000)
            devices = self.i2c.scan()
            if oled_addr in devices:
                self.oled = SSD1306_I2C(self.width, self.height, self.i2c, addr=oled_addr)
                self.has_oled = True
            elif 0x3D in devices:
                self.oled = SSD1306_I2C(self.width, self.height, self.i2c, addr=0x3D)
                self.has_oled = True

            if self.has_oled:
                self.show_message("DAVIS VP2", "\"El Gancho\"", "by J. Estay", "Iniciando...")
        except Exception:
            self.has_oled = False

    def heartbeat(self):
        """Execute heartbeat pulse on status LED"""
        if not self.led:
            return
        try:
            if self.led_mode == "TOGGLE":
                self.led.value(1 - self.led.value())
            else:
                # Classic medical double-pulse ('lub-dub')
                self.led.value(1)
                _sleep_ms(50)
                self.led.value(0)
                _sleep_ms(70)
                self.led.value(1)
                _sleep_ms(50)
                self.led.value(0)
        except Exception:
            pass

    def led_on_for(self, seconds=20):
        """Keep LED solid ON for specified duration in seconds (non-blocking)"""
        if not self.led:
            return
        try:
            self.led.value(1)
            self.led_until = time.time() + seconds
        except Exception:
            pass

    def check_led_timer(self):
        """Check if timed solid LED should be turned off (non-blocking)"""
        if not self.led or self.led_until == 0:
            return
        try:
            now = time.time()
            if now >= self.led_until or (self.led_until - now > 60):
                self.led.value(0)
                self.led_until = 0
            else:
                self.led.value(1)
        except Exception:
            pass

    def sheets_success_pulse(self):
        """Keep status LED solid ON for 20 seconds upon Google Sheets upload"""
        self.led_on_for(20)

    def wu_flicker(self):
        """Quick flickering blink sequence on LED to signal a Weather Underground upload"""
        if not self.led:
            return
        # If the 20s Sheets LED is currently on, do not interrupt it
        if self.led_until > 0 and time.time() < self.led_until:
            return
        try:
            for _ in range(3):
                self.led.value(1)
                _sleep_ms(35)
                self.led.value(0)
                _sleep_ms(45)
            self.led.value(0)
        except Exception:
            pass

    def show_sheets_success(self, timestamp_str=None):
        """Display dedicated notification on OLED when data is successfully saved to Google Sheets"""
        if not self.has_oled or not self.oled:
            return
        try:
            t_str = str(timestamp_str)[11:19] if timestamp_str and len(str(timestamp_str)) >= 19 else ""
            self.oled.fill(0)
            self._draw_header(_center("GOOGLE SHEETS"))

            # Blue body (48px)
            self.oled.text(_center("GUARDADO EXITOSO"), 0, 22, 1)
            self.oled.text(_center("[OK] Registro"), 0, 36, 1)
            if t_str:
                self.oled.text(_center("Hora: " + t_str), 0, 50, 1)
            else:
                self.oled.text(_center("100% SINCRONIZADO"), 0, 50, 1)
            self.oled.show()
        except Exception:
            pass

    def led_on(self):
        if self.led:
            try:
                self.led.value(1)
            except Exception:
                pass

    def led_off(self):
        if self.led:
            try:
                self.led.value(0)
            except Exception:
                pass

    def set_ip(self, ip_str):
        """Set local IP address for display"""
        if ip_str:
            self.ip_addr = str(ip_str)

    def set_station_id(self, st_id):
        """Set active Weather Underground station ID"""
        if st_id:
            self.wu_station_id = str(st_id)

    def update_cloud_status(self, wu_ok=None, gdrive_ok=None, timestamp_str=None):
        """Update last upload status and timestamps for WU and Google Drive"""
        dt_formatted = _format_datetime_short(timestamp_str) if timestamp_str else "--/-- --:--:--"

        if wu_ok is True:
            self.wu_status = "OK"
            self.wu_tag = "WU: OK"
            if timestamp_str:
                self.wu_datetime = dt_formatted
        elif wu_ok is False:
            self.wu_status = "ERR"
            self.wu_tag = "WU: ERR"
            if timestamp_str:
                self.wu_datetime = dt_formatted

        if gdrive_ok is True:
            self.gdrive_status = "OK"
            if timestamp_str:
                self.gdrive_datetime = dt_formatted
        elif gdrive_ok is False:
            self.gdrive_status = "ERR"
            if timestamp_str:
                self.gdrive_datetime = dt_formatted

    def show_message(self, line1, line2="", line3="", line4=""):
        """Show a simple centered/boxed status message"""
        if not self.has_oled or not self.oled:
            return
        try:
            self.oled.fill(0)
            self._draw_header(line1[:16])

            if line2:
                self.oled.text(line2[:16], 2, 20, 1)
            if line3:
                self.oled.text(line3[:16], 2, 34, 1)
            if line4:
                self.oled.text(line4[:16], 2, 48, 1)
            self.oled.show()
        except Exception:
            pass

    def _draw_header(self, left, right="", alert=False):
        """Header in the physical 16px yellow band: text over a thin line, or an inverted bar for alerts"""
        if alert:
            self.oled.fill_rect(0, 0, 128, 16, 1)
        else:
            self.oled.hline(0, 14, 128, 1)
        color = 0 if alert else 1
        self.oled.text(left, 2, 4, color)
        if right:
            self.oled.text(right, 126 - 8 * len(right), 4, color)

    def wake(self, seconds):
        """Keep the screen on (at night too) for the next seconds, e.g. after a boot or an update"""
        self._awake_from = _ticks()
        self._awake_for = seconds
        if self.has_oled and self.oled:
            try:
                self._set_power(True, self._contrast or CONTRAST_DAY)
            except Exception:
                pass

    def is_awake(self):
        return self._awake_for > 0 and _since_s(self._awake_from) < self._awake_for

    def _set_power(self, on, contrast):
        if not on:
            if not self._screen_off:
                self.oled.poweroff()
                self._screen_off = True
            return
        if self._screen_off:
            self.oled.poweron()
            self._screen_off = False
        if contrast != self._contrast:
            self.oled.contrast(contrast)
            self._contrast = contrast

    def _status_header(self, card_title, data, now_str):
        """(left, right, alert, night) for the card header, from data freshness, uploads and clock"""
        ts = data.get("timestamp")
        if ts != self._last_data_ts:
            self._last_data_ts = ts
            self._last_data_change = _ticks()

        clock = now_str if now_str and now_str[:4] >= "2025" else None
        night = clock is not None and _is_night(int(clock[11:13])) and not self.is_awake()
        time_hm = clock[11:16] if clock else ""

        stale_for = _since_s(self._last_data_change)
        if stale_for >= STALE_DATA_SECONDS:
            return "SIN DATOS", _format_age(stale_for), True, night
        if self.gdrive_status == "ERR":
            return "NO SHEETS", time_hm, True, night
        if not night:
            # Weather alerts while they last (by day only: they must not light the room at night)
            t = data.get("temp_out_c")
            gust = data.get("windgustkmh_10m")
            if t is not None and t <= FROST_WARN_C:
                return ("HELADA" if t <= 0 else "HELADA?"), "{:.1f}C".format(t), True, night
            if gust is not None and gust >= GUST_ALERT_KMH:
                return "RAFAGA", "{:.0f}km/h".format(gust), True, night
        return card_title[:9], time_hm, False, night

    def show_wifi_status(self, ssid, ip_addr=None):
        """Show WiFi connection progress"""
        if not self.has_oled:
            return
        if ip_addr:
            self.show_message("WIFI CONECTADO", ssid[:16], "IP:" + str(ip_addr)[:13], "NTP Sincronizado")
        else:
            self.show_message("CONECTANDO WIFI", ssid[:16], "Buscando red...")

    def set_system_info(self, fw=None, reset=None):
        """Firmware version and why the ESP32 last started, for the SISTEMA card"""
        self.fw_version = fw
        self.reset_cause = reset

    def set_iss_recept(self, pct):
        """ISS reception (%) of the last logged interval, from the console's wind sample count"""
        self.iss_recept = pct

    def start_diagnostics(self):
        """BOOT button: show SISTEMA and SUBIDAS (and keep the screen on) for DIAG_SECONDS"""
        self._diag_from = _ticks()
        self._diag_on = True
        self.wake(DIAG_SECONDS)

    def _in_diagnostics(self):
        if self._diag_on and _since_s(self._diag_from) >= DIAG_SECONDS:
            self._diag_on = False
        return self._diag_on

    def problems(self, data):
        """Short descriptions (<= 16 chars) of what is wrong right now; empty when all is fine"""
        out = []
        if self.gdrive_status == "ERR":
            out.append("Planilla: falla")
        if self.wu_status == "ERR":
            out.append("WU: falla")
        if data.get("tx_battery_status") == 1:
            out.append("Bat. transm baja")
        volt = data.get("console_battery_v")
        if volt is not None and volt < CONSOLE_BATTERY_LOW_V:
            out.append("Bat.consola {:.1f}V".format(volt))
        if self.iss_recept is not None and self.iss_recept < ISS_RECEPT_LOW:
            out.append("Recepcion {:.0f}%".format(self.iss_recept))
        if self.last_error:
            out.append("Pantalla: error")
        return out

    def card_seconds_left(self, default):
        """Seconds the main loop should still wait so the current card stays its CARD_SECONDS"""
        if self._card_from is None:
            return default
        return self._card_hold - _since_s(self._card_from)

    def _hold(self, card, data):
        if card == "lluvia" and (data.get("rain_rate_mm_per_hr") or 0) > 0:
            return RAIN_CARD_SECONDS
        return CARD_SECONDS.get(card, 8)

    def _next_card(self, data):
        """Next card id in _ROTATION; the big temperature is never shown twice in a row"""
        if self._in_diagnostics():
            card = _DIAG_ROTATION[self._diag_index % len(_DIAG_ROTATION)]
            self._diag_index += 1
            return card
        for _ in range(len(_ROTATION)):
            card = _ROTATION[self.card_index]
            self.card_index = (self.card_index + 1) % len(_ROTATION)
            if self.card_index == 0:
                self._round += 1
            if card == "problema" and not self.problems(data):
                continue
            if card == "sol" and self._round % 2 == 0:
                continue
            if card == "ahora" and self.last_card_title in ("AHORA", "PRONOST."):
                continue
            return card
        return "ahora"

    def _draw_big_text(self, s, x, y, scale):
        """8x8 font scaled up (the SSD1306 framebuffer only has the small font)"""
        import framebuf
        w = 8 * len(s)
        fb = framebuf.FrameBuffer(bytearray(w), w, 8, framebuf.MONO_HLSB)
        fb.text(s, 0, 0, 1)
        for yy in range(8):
            for xx in range(w):
                if fb.pixel(xx, yy):
                    self.oled.fill_rect(x + xx * scale, y + yy * scale, scale, scale, 1)

    def _draw_now_card(self, data):
        """Big outdoor temperature, with humidity and wind below"""
        t_out = data.get("temp_out_c")
        num = "{:.1f}".format(t_out) if t_out is not None else "--.-"
        scale = 3 if 8 * 3 * len(num) + 13 <= 128 else 2
        width = 8 * scale * len(num) + 13          # number + gap, degree sign and "C"
        x = (128 - width) // 2
        try:
            self._draw_big_text(num, x, 20, scale)
        except ImportError:
            # PC simulation without framebuf: plain text
            self.oled.text(num, x, 28, 1)
        x_unit = x + 8 * scale * len(num) + 2
        self.oled.rect(x_unit, 20, 3, 3, 1)
        self.oled.text("C", x_unit + 3, 22, 1)

        h_out = data.get("humidity_out")
        w_spd = data.get("wind_speed_kmh")
        w_dir = data.get("wind_direction_deg")
        r_day = float(data.get("rain_day_mm") or 0)
        bottom = "{}%".format(h_out) if h_out is not None else "--%"
        if r_day > 0:
            # It rained today: the day total replaces the wind (shown on the VIENTO card)
            bottom += " Lluv {:.1f}mm".format(r_day)
        elif w_spd is not None:
            bottom += " {:.0f}km/h".format(w_spd)
            if w_spd > 0 and w_dir is not None:
                bottom += " " + _get_compass_point(w_dir)
        self.oled.text(_center(bottom), 0, 52, 1)

    # --- Forecast icons (drawn with framebuf primitives, animated a few frames) ---

    def _disc(self, cx, cy, r, c):
        for dy in range(-r, r + 1):
            dx = int((r * r - dy * dy) ** 0.5)
            self.oled.hline(cx - dx, cy + dy, 2 * dx + 1, c)

    def _sun(self, cx, cy, r, frame):
        self._disc(cx, cy, r, 1)
        ray = 3 if frame % 2 else 5                      # pulsing rays
        for dx, dy in _RAYS:
            x0 = cx + dx * (r + 2) // 10
            y0 = cy + dy * (r + 2) // 10
            self.oled.line(x0, y0, x0 + dx * ray // 10, y0 + dy * ray // 10, 1)

    def _moon(self, cx, cy, frame):
        self._disc(cx, cy, 9, 1)
        self._disc(cx + 5, cy - 3, 8, 0)                 # crescent
        for i, (sx, sy) in enumerate(((cx + 14, cy - 8), (cx + 17, cy + 4), (cx - 14, cy - 9))):
            if (frame + i) % 3:                          # twinkling stars
                self.oled.pixel(sx, sy, 1)
                if (frame + i) % 3 == 1:
                    self.oled.hline(sx - 1, sy, 3, 1)
                    self.oled.vline(sx, sy - 1, 3, 1)

    def _cloud(self, cx, cy):
        # Black margin first so a cloud in front of the sun/moon keeps its outline
        for c, grow in ((0, 2), (1, 0)):
            self._disc(cx - 8, cy + 2, 7 + grow, c)
            self._disc(cx + 1, cy - 3, 9 + grow, c)
            self._disc(cx + 10, cy + 2, 7 + grow, c)
            self.oled.fill_rect(cx - 8, cy + 2 - grow, 18, 8 + 2 * grow, c)

    def _draw_icon(self, kind, night, frame, cx=28, cy=34):
        # Fits between the header line (y=14) and the ticker row (y=56)
        drift = (0, 1, 2, 1)[frame % 4]                  # clouds sway slowly
        if kind == "sol":
            if night:
                self._moon(cx, cy, frame)
            else:
                self._sun(cx, cy, 8, frame)
        elif kind == "parcial":
            if night:
                self._moon(cx + 4, cy - 9, frame)
                self._cloud(cx - 5 + drift, cy + 8)
            else:
                self._sun(cx + 7, cy - 6, 6, frame)
                self._cloud(cx - 3 + drift, cy + 6)
        elif kind == "nublado":
            self._cloud(cx + 6 - drift, cy - 4)
            self._cloud(cx - 4 + drift, cy + 4)
        else:
            # Rain and/or snow under a cloud, falling a little each frame
            self._cloud(cx + drift - 1, cy - 4)
            for i in range(5):
                x = cx - 11 + 5 * i
                y = cy + 10 + (frame * 3 + i * 5) % 7
                if kind == "nieve" or (kind == "aguanieve" and i % 2):
                    self.oled.fill_rect(x, y, 2, 2, 1)
                else:
                    self.oled.vline(x, y, 4, 1)

    def _draw_forecast_card(self, data, night, frame, ticker=None, ticker_x=0):
        """Animated Davis forecast icon on the left; forecast, temperature and humidity on the right;
        the console's forecast message scrolls along the bottom row"""
        kind, label = _forecast_kind(data.get("forecast_icons"), night)
        if kind:
            self._draw_icon(kind, night, frame)
        else:
            self.oled.text("?", 24, 30, 1)
        t_out = data.get("temp_out_c")
        h_out = data.get("humidity_out")
        self.oled.text(label[:8], 62, 20, 1)
        self.oled.text("{:.1f}C".format(t_out) if t_out is not None else "--.-C", 62, 32, 1)
        self.oled.text("{}%".format(h_out) if h_out is not None else "--%", 62, 44, 1)
        if ticker:
            # framebuf clips text drawn partly off-screen: only the visible slice is passed
            first = max(0, -ticker_x // 8)
            visible = ticker[first:first + 17]
            if visible:
                self.oled.text(visible, ticker_x + 8 * first, 56, 1)

    def _get_card_content(self, card, data):
        """Return (card_title, [line1, line2, line3, line4]) for a text card"""
        if card == "hoy":
            # TODAY'S EXTREMES: from the console (HILOWS, kept over ESP32 reboots), corrected with
            # the live reading when it already went beyond them
            now_hm = str(data.get("timestamp") or "")[11:16] or "--:--"
            t = data.get("temp_out_c")
            hi, hi_t = data.get("today_temp_hi_c"), data.get("today_temp_hi_time")
            lo, lo_t = data.get("today_temp_lo_c"), data.get("today_temp_lo_time")
            if t is not None and (hi is None or t > hi):
                hi, hi_t = t, now_hm
            if t is not None and (lo is None or t < lo):
                lo, lo_t = t, now_hm
            gust, gust_t = data.get("today_gust_kmh"), data.get("today_gust_time")

            def temp_line(label, v, at):
                return label + ("{:6.1f}C ".format(v) if v is not None else "  --.-C ") + (at or "--:--")

            return "HOY", [
                temp_line("Max", hi, hi_t),
                temp_line("Min", lo, lo_t),
                ("Raf{:4.0f}kmh ".format(gust) + (gust_t or "--:--")) if gust is not None else "Raf   --kmh",
                "Lluvia {:.1f} mm".format(float(data.get("rain_day_mm") or 0))
            ]

        elif card == "aire":
            # AIR: feels-like (THW), dew point, pressure and its 3 h trend
            def c(v):
                return "{:.1f}C".format(v) if v is not None else "--.-C"
            bar = data.get("pressure_hpa")
            return "AIRE", [
                "Sens:  " + c(data.get("thw_index_c")),
                "Rocio: " + c(data.get("dewpoint_c")),
                "Pres: " + ("{:.1f}hPa".format(bar) if bar is not None else "--hPa"),
                "Tend: " + (data.get("barometer_trend") or "--")[:10]
            ]

        elif card == "problema":
            lines = self.problems(data)
            if len(lines) > 4:
                lines = lines[:3] + ["+{} mas".format(len(lines) - 3)]
            return "PROBLEMA", lines

        elif card == "viento":
            # VIENTO Y RAFAGAS (LOOP 2)
            w_spd = data.get("wind_speed_kmh") or 0.0
            w_dir = data.get("wind_direction_deg")
            w_gst = data.get("windgustkmh_10m") or w_spd
            w_sost = data.get("windspdkmh_avg2m") or w_spd

            dir_str = "{} ({})".format(w_dir, _get_compass_point(w_dir)) if w_dir is not None else "--"
            g_str = "{:.1f} km/h".format(w_gst)
            s_str = "{:.1f} km/h".format(w_sost)

            return "VIENTO", [
                "Vel:  {:.1f} km/h".format(w_spd),
                "Dir:  " + dir_str[:10],
                "Raf:  " + g_str[:10],
                "Sost: " + s_str[:10]
            ]

        elif card == "lluvia":
            # PRECIPITACION: always shown (month/year totals matter on dry days too); the
            # intensity line only while it rains, the current storm otherwise
            r_rate = float(data.get("rain_rate_mm_per_hr") or 0)
            first = ("Tasa: {:.1f} mm/h".format(r_rate) if r_rate > 0 else
                     "Torm: {:.1f} mm".format(float(data.get("storm_rain_mm") or 0)))
            return "LLUVIA", [
                first,
                "Hoy:  {:.1f} mm".format(float(data.get("rain_day_mm") or 0)),
                "Mes:  {:.1f} mm".format(float(data.get("rain_month_mm") or 0)),
                "Ano:  {:.1f} mm".format(float(data.get("rain_year_mm") or 0))
            ]

        elif card == "sol":
            # SOL Y LUNA (the forecast has its own animated card)
            sr = str(data.get("sunrise") or "--:--")
            ss = str(data.get("sunset") or "--:--")
            moon = _moon_phase(str(data.get("timestamp") or "")) or "--"

            return "SOL/LUNA", [
                "Salida: " + sr[:5],
                "Puesta: " + ss[:5],
                "Luz: " + _day_length(sr, ss),
                "Luna: " + moon[:10]
            ]

        elif card == "sistema":
            # DIAGNOSTICO: firmware, recepcion del transmisor, baterias, ultimo arranque
            c_volt = data.get("console_battery_v")
            tx_batt = "OK" if data.get("tx_battery_status") != 1 else "BAJA"
            rec = "{:.0f}%".format(self.iss_recept) if self.iss_recept is not None else "--"
            c_str = "{:.2f}V".format(c_volt) if c_volt is not None else "--V"
            reset = _RESET_SHORT.get(self.reset_cause, self.reset_cause) or "--"

            return "SISTEMA", [
                "FW: v" + str(self.fw_version or "--"),
                "ISS " + rec + " Tx " + tx_batt,
                "Consola: " + c_str,
                "Inicio " + reset[:8]
            ]

        else:
            # ESTADO DE SUBIDAS CLOUD (WU & GOOGLE DRIVE CON FECHA-HORA)
            return "SUBIDAS", [
                "WU:     " + self.wu_status,
                "  " + self.wu_datetime,
                "Drive:  " + self.gdrive_status,
                "  " + self.gdrive_datetime
            ]

    def show_weather(self, data, cycle=1, uploaded_wu=None, uploaded_gdrive=None, animate=True, now_str=None):
        """
        Render a weather card with vertical slide-up transition.
        Header shows [CARD_TITLE        HH:MM] in the physical 16px yellow band, with HH:MM from
        now_str (ESP32 local clock). It turns into an inverted alert when the console has not
        answered for STALE_DATA_SECONDS ([SIN DATOS 5m]) or the last Sheets upload failed.
        Between NIGHT_START_HOUR and NIGHT_END_HOUR the screen is off unless there is an alert.
        Cards follow _ROTATION: the big temperature (AHORA, alternating with the forecast) between
        HOY, VIENTO, LLUVIA, AIRE, PROBLEMA (only if something is wrong) and SOL/LUNA; each stays
        CARD_SECONDS (see card_seconds_left). By day the header also flags frost and strong gusts.
        A card that fails to draw shows ERROR PANTALLA with the exception.
        """
        if not self.has_oled or not self.oled:
            return

        card = "?"
        try:
            # Update persistent cloud status if upload events occurred
            if uploaded_wu is not None or uploaded_gdrive is not None:
                self.update_cloud_status(
                    wu_ok=uploaded_wu,
                    gdrive_ok=uploaded_gdrive,
                    timestamp_str=data.get("timestamp")
                )

            card = self._next_card(data)
            if card == "ahora":
                # Every other time, the animated forecast instead of the big temperature
                self._forecast_turn = not self._forecast_turn
                card_title, lines = ("PRONOST." if self._forecast_turn else "AHORA"), None
            else:
                card_title, lines = self._get_card_content(card, data)

            left, right, alert, night = self._status_header(card_title, data, now_str)
            if night and not alert:
                self._set_power(False, None)
                self.last_card_lines = None  # gentle entrance when the screen comes back on
                self._card_from = None
                return
            self._set_power(True, CONTRAST_NIGHT if night else CONTRAST_DAY)
            self._card_from = _ticks()
            # The forecast card already lasts as long as its scrolling message
            self._card_hold = 0 if card_title == "PRONOST." else self._hold(card, data)

            if lines is None and card_title == "PRONOST.":
                dark = _is_dark(now_str, data)
                ticker = forecast_message(data.get("forecast_rule"))
                if ticker:
                    # One full pass of the message, right to left; the icon animates meanwhile
                    travel = 128 + 8 * len(ticker)
                    duration = min(TICKER_MAX_S, max(TICKER_MIN_S, travel / TICKER_PX_PER_S))
                    t0 = _ticks()
                    while True:
                        elapsed = _since_s(t0)
                        done = elapsed >= duration
                        pos = travel if done else int(travel * elapsed / duration)
                        self.oled.fill(0)
                        self._draw_header(left, right, alert)
                        self._draw_forecast_card(data, dark, int(elapsed * 1000) // FORECAST_FRAME_MS,
                                                 ticker, 128 - pos)
                        self.oled.show()
                        if done:
                            break
                        _sleep_ms(10)
                else:
                    for frame in range(FORECAST_FRAMES):
                        self.oled.fill(0)
                        self._draw_header(left, right, alert)
                        self._draw_forecast_card(data, dark, frame)
                        self.oled.show()
                        if frame < FORECAST_FRAMES - 1:
                            _sleep_ms(FORECAST_FRAME_MS)
                self.last_card_lines = None
                self.last_card_title = card_title
                self.check_led_timer()
                return

            if lines is None:
                # Big-number card: drawn at once; the next text card slides in gently
                self.oled.fill(0)
                self._draw_header(left, right, alert)
                self._draw_now_card(data)
                self.oled.show()
                self.last_card_lines = None
                self.last_card_title = card_title
                self.check_led_timer()
                return

            if animate and self.last_card_lines is not None:
                # Vertical slide-up animation: old card rolls up & out, new card rolls up & in
                n_lines = len(lines)
                old_left = left if alert else (self.last_card_title or card_title)[:9]
                for s in _SHIFTS:
                    self.oled.fill(0)
                    self._draw_header(left if s >= 24 else old_left, right, alert)

                    # Old lines scrolling up and disappearing under Y=17 (cyan boundary)
                    for i in range(len(self.last_card_lines)):
                        y = _TARGET_Y[i] - s
                        if 17 <= y <= 56:
                            self.oled.text(self.last_card_lines[i][:16], 2, y, 1)

                    # New lines scrolling up and into view from below
                    for i in range(n_lines):
                        y = _TARGET_Y[i] + 48 - s
                        if 17 <= y <= 56:
                            self.oled.text(lines[i][:16], 2, y, 1)

                    self.oled.show()
                    _sleep_ms(15)

            elif animate and self.last_card_lines is None:
                # First appearance: gentle slide-up within cyan zone
                n_lines = len(lines)
                for off in _FIRST_SHIFTS:
                    self.oled.fill(0)
                    self._draw_header(left, right, alert)
                    for i in range(n_lines):
                        y = _TARGET_Y[i] + off
                        if 17 <= y <= 56:
                            self.oled.text(lines[i][:16], 2, y, 1)
                    self.oled.show()
                    _sleep_ms(20)

            # Final static frame (100% stable in cyan zone)
            self.oled.fill(0)
            self._draw_header(left, right, alert)
            for i in range(len(lines)):
                self.oled.text(lines[i][:16], 2, _TARGET_Y[i], 1)
            self.oled.show()

            self.last_card_lines = lines
            self.last_card_title = card_title
            # Check if 20-second Google Sheets LED timer should expire
            self.check_led_timer()

        except Exception as e:
            # Never silently: a card that fails to draw would just never appear on screen
            self.errors += 1
            self.last_error = "{}: {}".format(card, repr(e))[:64]
            print("[OLED] Error dibujando tarjeta " + self.last_error)
            try:
                self.oled.fill(0)
                self.oled.text("ERROR PANTALLA", 0, 0, 1)
                msg = self.last_error
                for i in range(4):
                    self.oled.text(msg[16 * i:16 * i + 16], 0, 20 + 11 * i, 1)
                self.oled.show()
                self.last_card_lines = None
            except Exception:
                pass
