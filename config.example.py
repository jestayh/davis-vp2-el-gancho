"""
Davis Vantage Pro 2 Weather Station Configuration Template
Copy this file to config.py and fill in your network and station details.
Compatible with both PC (CPython) and ESP32 (MicroPython).
"""

# ==============================================================================
# 1. Davis WeatherLink IP Settings
# ==============================================================================
DAVIS_HOST = "192.168.1.7"
DAVIS_PORT = 22222

# ==============================================================================
# 2. Weather Underground Credentials
# ==============================================================================
WU_STATION_ID = "YOUR_STATION_ID"
WU_API_KEY = "YOUR_WU_PASSWORD"

# ==============================================================================
# 3. Timing & Operational Settings (Seconds)
# ==============================================================================
# Seconds between consecutive Davis LOOP reads
READ_INTERVAL_SECONDS = 15

# Number of streamed packets per batch (5 packets @ ~2.5s = ~10-12s streaming window)
LOOP_BATCH_SIZE = 5

# Seconds between Weather Underground RapidFire uploads (typically 30s)
WU_UPLOAD_INTERVAL_SECONDS = 30

# Seconds between local CSV writes (300s = 5 minutes, flash protection)
CSV_LOG_INTERVAL_SECONDS = 300

# Socket timeout in seconds
SOCKET_TIMEOUT_SECONDS = 8

# ==============================================================================
# 4. Storage Settings
# ==============================================================================
CSV_FILEPATH = "data/weather.csv"

# ==============================================================================
# 5. WiFi Configuration (ESP32 only - ignored when running on PC)
# ==============================================================================
# Known WiFi networks (SSID, Password) - ESP32 connects to strongest match
WIFI_NETWORKS = [
    ("Home_WiFi", "password123"),
    ("Secondary_WiFi", "password456"),
]
MAMA_FALLBACK_PASSWORD = ""

# Legacy / default fallback credentials
WIFI_SSID = "Home_WiFi"
WIFI_PASSWORD = "password123"

# ==============================================================================
# 6. Google Sheets / Drive Webhook (Cloud)
# ==============================================================================
# Paste your Google Apps Script Web App URL here
# Leave empty "" to disable cloud sync
GOOGLE_APPS_SCRIPT_URL = ""

