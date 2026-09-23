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


def _format_forecast(icon_code):
    if icon_code is None:
        return "Estable"
    if (icon_code & 0x01) and (icon_code & 0x02):
        return "[!] Lluvia/Nub"
    if icon_code & 0x01:
        return "[!] Lluvia"
    if icon_code & 0x10:
        return "[*] Nieve"
    if icon_code & 0x02:
        return "[~] Nublado"
    if icon_code & 0x04:
        return "[*~] Parcial"
    if icon_code & 0x08:
        return "[*] Despejado"
    return "Estable"


def _center(s, width=16):
    s = str(s)
    if len(s) >= width:
        return s[:width]
    pad = (width - len(s)) // 2
    return " " * pad + s


_TARGET_Y = (18, 29, 41, 53)
_SHIFTS = (6, 14, 22, 30, 38, 48)
_FIRST_SHIFTS = (32, 20, 10, 0)


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
        self.last_card_lines = None
        self.last_card_title = None
        self.wu_tag = "WU: --"
        self.wu_status = "ESPERA"
        self.wu_datetime = "--/-- --:--:--"
        self.gdrive_status = "ESPERA"
        self.gdrive_datetime = "--/-- --:--:--"
        self.ip_addr = "192.168.1.6"
        self.wu_station_id = "ISANTA2468"

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
                self.show_message("DAVIS VP2", "Pantalla Lista", "Iniciando...")
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
            # Inverted top bar (fills the physical 16px yellow zone)
            self.oled.fill_rect(0, 0, 128, 16, 1)
            self.oled.text(_center("GOOGLE SHEETS"), 0, 4, 0)

            # Blue body (48px)
            self.oled.text(_center("GUARDADO EXITOSO"), 0, 22, 1)
            self.oled.text(_center("[OK] 41 COLUMNAS"), 0, 36, 1)
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
            # Inverted header bar (fills the physical 16px yellow zone)
            self.oled.fill_rect(0, 0, 128, 16, 1)
            self.oled.text(line1[:16], 2, 4, 0)

            if line2:
                self.oled.text(line2[:16], 2, 20, 1)
            if line3:
                self.oled.text(line3[:16], 2, 34, 1)
            if line4:
                self.oled.text(line4[:16], 2, 48, 1)
            self.oled.show()
        except Exception:
            pass

    def show_wifi_status(self, ssid, ip_addr=None):
        """Show WiFi connection progress"""
        if not self.has_oled:
            return
        if ip_addr:
            self.show_message("WIFI CONECTADO", ssid[:16], "IP:" + str(ip_addr)[:13], "NTP Sincronizado")
        else:
            self.show_message("CONECTANDO WIFI", ssid[:16], "Buscando red...")

    def _get_card_content(self, card_idx, data):
        """Return (card_title, [line1, line2, line3, line4]) for specified card (0 to 4)"""
        if card_idx == 0:
            # Card 0: AMBIENTE EXTERIOR
            t_out = data.get("temp_out_c")
            h_out = data.get("humidity_out")
            dp = data.get("dewpoint_c")
            bar = data.get("pressure_hpa")
            trend = data.get("barometer_trend") or "Estable"

            t_str = "{:.1f}C".format(t_out) if t_out is not None else "--.-C"
            h_str = "{}%".format(h_out) if h_out is not None else "--%"
            dp_str = "{:.1f}C".format(dp) if dp is not None else "--.-C"
            bar_str = "{:.1f}hPa".format(bar) if bar is not None else "--hPa"

            return "EXTERIOR", [
                "Temp: " + t_str + " " + h_str,
                "Rocio: " + dp_str,
                "Pres: " + bar_str,
                "Tend: " + trend[:10]
            ]

        elif card_idx == 1:
            # Card 1: VIENTO Y RAFAGAS (LOOP 2)
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

        elif card_idx == 2:
            # Card 2: PRECIPITACION & INDICES
            r_rate = data.get("rain_rate_mm_per_hr") or 0.0
            r_day = data.get("rain_day_mm") or 0.0
            r_month = data.get("rain_month_mm") or 0.0
            r_year = data.get("rain_year_mm") or 0.0

            return "LLUVIA", [
                "Tasa: {:.1f} mm/h".format(r_rate),
                "Hoy:  {:.1f} mm".format(r_day),
                "Mes:  {:.1f} mm".format(r_month),
                "Ano:  {:.1f} mm".format(r_year)
            ]

        elif card_idx == 3:
            # Card 3: SOL, ASTRONOMIA & PRONOSTICO DAVIS
            sr = data.get("sunrise") or "--:--"
            ss = data.get("sunset") or "--:--"
            f_desc = _format_forecast(data.get("forecast_icons"))

            return "SOL/PRON", [
                "Salida:  " + str(sr)[:8],
                "Puesta:  " + str(ss)[:8],
                "Pronostico:",
                "  " + f_desc[:14]
            ]

        elif card_idx == 4:
            # Card 4: IDENTIFICACION Y CREDENCIALES DE ESTACION
            return "ESTACION", [
                _center("Davis VP2"),
                _center("\"El Gancho\""),
                _center("ID: " + str(self.wu_station_id)),
                _center("by J. Estay")
            ]

        elif card_idx == 5:
            # Card 5: INTERIOR Y DIAGNOSTICO DE SISTEMA
            t_in = data.get("temp_in_c")
            h_in = data.get("humidity_in")
            c_volt = data.get("console_battery_v")
            iss_ok = "OK" if data.get("tx_battery_status") != 1 else "BAJA"

            t_in_str = "{:.1f}C".format(t_in) if t_in is not None else "--.-C"
            h_in_str = "{}%".format(h_in) if h_in is not None else "--%"
            c_str = "{:.2f}V".format(c_volt) if c_volt is not None else "--V"

            return "SISTEMA", [
                "Int:  " + t_in_str + " " + h_in_str,
                "Consola: " + c_str,
                "Transm:  " + iss_ok,
                "IP: " + str(self.ip_addr)[:12]
            ]

        else:
            # Card 6: ESTADO DE SUBIDAS CLOUD (WU & GOOGLE DRIVE CON FECHA-HORA)
            return "SUBIDAS", [
                "WU:     " + self.wu_status,
                "  " + self.wu_datetime,
                "Drive:  " + self.gdrive_status,
                "  " + self.gdrive_datetime
            ]

    def show_weather(self, data, cycle=1, uploaded_wu=None, uploaded_gdrive=None, animate=True):
        """
        Render a weather card with vertical slide-up transition.
        Header shows [CARD_TITLE        HH:MM] in the physical 16px yellow band.
        Cycles across 7 cards:
          0: EXTERIOR
          1: VIENTO
          2: LLUVIA
          3: SOL/PRON (Amanecer, Atardecer, Pronostico)
          4: ESTACION ("El Gancho" by J. Estay)
          5: SISTEMA
          6: SUBIDAS (WU & Google Drive status + timestamps)
        """
        if not self.has_oled or not self.oled:
            return

        try:
            # Update persistent cloud status if upload events occurred
            if uploaded_wu is not None or uploaded_gdrive is not None:
                self.update_cloud_status(
                    wu_ok=uploaded_wu,
                    gdrive_ok=uploaded_gdrive,
                    timestamp_str=data.get("timestamp")
                )

            # Get content for current card
            card_title, lines = self._get_card_content(self.card_index, data)

            # Advance card index for next cycle (0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 0)
            self.card_index = (self.card_index + 1) % 7

            # Extract current local time HH:MM for header bar
            ts = str(data.get("timestamp", ""))
            time_hm = ts[11:16] if len(ts) >= 16 else ""

            if animate and self.last_card_lines is not None:
                # Vertical slide-up animation: old card rolls up & out, new card rolls up & in
                n_lines = len(lines)
                for s in _SHIFTS:
                    self.oled.fill(0)
                    # Top header bar (fills the physical 16px yellow band)
                    self.oled.fill_rect(0, 0, 128, 16, 1)
                    active_title = card_title if s >= 24 else (self.last_card_title or card_title)
                    self.oled.text(active_title[:9], 2, 4, 0)
                    if time_hm:
                        self.oled.text(time_hm, 84, 4, 0)

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
                    self.oled.fill_rect(0, 0, 128, 16, 1)
                    self.oled.text(card_title[:9], 2, 4, 0)
                    if time_hm:
                        self.oled.text(time_hm, 84, 4, 0)
                    for i in range(n_lines):
                        y = _TARGET_Y[i] + off
                        if 17 <= y <= 56:
                            self.oled.text(lines[i][:16], 2, y, 1)
                    self.oled.show()
                    _sleep_ms(20)

            # Final static frame (100% stable in cyan zone)
            self.oled.fill(0)
            self.oled.fill_rect(0, 0, 128, 16, 1)
            self.oled.text(card_title[:9], 2, 4, 0)
            if time_hm:
                self.oled.text(time_hm, 84, 4, 0)
            for i in range(len(lines)):
                self.oled.text(lines[i][:16], 2, _TARGET_Y[i], 1)
            self.oled.show()

            self.last_card_lines = lines
            self.last_card_title = card_title
            # Check if 20-second Google Sheets LED timer should expire
            self.check_led_timer()

        except Exception:
            pass
