# Davis Vantage Pro 2 → WeatherStation (PC & ESP32 MicroPython)

Sistema de recolección en tiempo real de todas las variables de la consola **Davis Vantage Pro 2** (vía WeatherLink IP), guardado continuo en base de datos **CSV** y retransmisión a **Weather Underground** (RapidFire).

Diseñado en **Python puro sin dependencias externas**, lo que permite ejecutarlo de forma idéntica en:
1. **PC local** (Windows, Linux, macOS con Python 3).
2. **Placa ESP32** (con firmware estándar MicroPython).

---

## Arquitectura

```
Davis Vantage Pro 2 ISS
  ↓ (inalámbrico 868/915 MHz)
Consola Davis + WeatherLink IP (192.168.1.7:22222)
  ↓ (TCP Socket - Paquete LOOP 1 binario de 99 bytes)
Python Core (PC o ESP32 MicroPython)
  ├── 1. Parser completo (CRC-16, Magnus dew point, filtrado centinela)
  ├── 2. Base de datos local: data/weather.csv (39 campos)
  └── 3. Uploader Weather Underground: RapidFire HTTP (cada 30s)
```

---

## Variables Registradas (43 campos en CSV)

- **Barómetro:** Presión (`inHg` y `hPa`), tendencia barométrica (código y texto descriptivo).
- **Ambiente exterior:** Temperatura exterior (°F y °C), humedad (%), punto de rocío calculado (°F y °C).
- **Ambiente interior:** Temperatura interior (°F y °C), humedad (%).
- **Viento instantáneo y promedio:** Velocidad actual (`mph` y `km/h`), dirección en grados (0-360°), velocidad promedio de 10 minutos (`mph` y `km/h`).
- **Viento avanzado (Ventana móvil en memoria):**
  - Viento sostenido de 2 minutos (`windspdmph_avg2m`) y dirección vectorial (`winddir_avg2m`).
  - Ráfaga máxima de 10 minutos (`windgustmph_10m`) y dirección de la ráfaga (`windgustdir_10m`).
- **Precipitación:** Tasa de lluvia instantánea (`in/hr` y `mm/hr`), lluvia de la tormenta/evento actual (`in` y `mm`), fecha de inicio de tormenta (`YYYY-MM-DD`), acumulados del día (`in` y `mm`), del mes y del año.
- **Evapotranspiración (ET):** Acumulados del día, mes y año.
- **Radiación solar y UV:** Radiación solar ($W/m^2$) e índice UV (si ISS cuenta con los sensores).
- **Diagnóstico y Astronomía:** Voltaje de batería de la consola (V), estado de transmisor, hora de salida y puesta de sol (`HH:MM`), íconos y regla de pronóstico.
- **Estado de red:** Confirmación de subida a Weather Underground (`Yes`/`No`).

---

## Estructura del Repositorio

```
weatherstation/
├── main.py                 # Orquestador con auto-detección (PC vs ESP32) y fallback
├── config.py               # Host, puertos, credenciales WU, WiFi multi-red y temporizadores
├── requirements.txt        # Documentación de dependencias (librería estándar pura)
├── README.md               # Documentación y guía de despliegue
├── data/
│   ├── .gitkeep
│   └── weather.csv         # Base de datos generada automáticamente
└── src/
    ├── __init__.py
    ├── davis_reader.py     # Parser LOOP 1/2 y cliente TCP socket con CRC-16
    ├── weather_underground.py  # Uploader RapidFire compatible con PC/MicroPython
    ├── data_logger.py      # Logger CSV con protección de memoria flash y WindTracker
    ├── display.py          # Gestor de pantalla OLED SSD1306 128x64 (I2C)
    ├── ssd1306.py          # Driver I2C para pantalla OLED MicroPython
    └── google_sheets.py    # Uploader Google Sheets / Drive Webhook (Nube)
```

---

## Conexión Hardware (ESP32 + Pantalla OLED 128x64)

Si utilizas una pantalla OLED SSD1306 (I2C 128x64), conéctala al ESP32 según el siguiente diagrama de pines:

| Pin OLED SSD1306 | Pin ESP32 (DevKit) | Descripción |
| :--- | :--- | :--- |
| **GND** | **GND** | Tierra / Ground |
| **VCC** | **3V3** | Alimentación 3.3V |
| **SCL** | **GPIO 22** | Señal de reloj I2C |
| **SDA** | **GPIO 21** | Señal de datos I2C |

> **Nota:** Si la pantalla no está conectada o falla el bus I2C, el sistema continúa funcionando de forma transparente (failsafe), registrando datos y subiendo a Weather Underground sin detenerse.

---

## 1. Ejecución en PC

No requiere instalar ningún paquete de terceros:

```bash
# 1. Editar configuración si es necesario (IP de la consola, estación WU)
# config.py

# 2. Ejecutar
python main.py
```

Verás las lecturas en tiempo real en la consola, la generación de `data/weather.csv` y los reportes cada 30 segundos a Weather Underground.

---

## 2. Ejecución en ESP32 (MicroPython)

El mismo código corre en una placa ESP32 con MicroPython.

### Paso 1: Instalar MicroPython en el ESP32
Si tu placa no tiene MicroPython aún:
1. Descarga el firmware `.bin` oficial desde [micropython.org/download/esp32/](https://micropython.org/download/esp32/).
2. Flashea con `esptool`:
   ```bash
   pip install esptool
   esptool.py --port COM3 erase_flash
   esptool.py --port COM3 write_flash -z 0x1000 esp32-xxxx.bin
   ```

### Paso 2: Configurar WiFi
En [`config.py`](file:///d:/repo/weatherstation/config.py), asigna tus redes conocidas en la lista `WIFI_NETWORKS`:
```python
WIFI_NETWORKS = [
    ("Mi_Red_Principal", "password123"),
    ("Red_Secundaria", "password456"),
]
```
El ESP32 escaneará el espectro WiFi de 2.4 GHz al arrancar y se conectará automáticamente a la mejor red disponible.

### Paso 3: Subir los archivos a la placa
Puedes usar **Thonny IDE** o la herramienta oficial **`mpremote`**:
```bash
pip install mpremote

# Crear carpetas en el ESP32
mpremote fs mkdir src
mpremote fs mkdir data

# Copiar archivos raíz
mpremote fs cp config.py :config.py
mpremote fs cp main.py :main.py

# Copiar módulos src/
mpremote fs cp src/__init__.py :src/__init__.py
mpremote fs cp src/davis_reader.py :src/davis_reader.py
mpremote fs cp src/weather_underground.py :src/weather_underground.py
mpremote fs cp src/data_logger.py :src/data_logger.py
mpremote fs cp src/display.py :src/display.py
mpremote fs cp src/ssd1306.py :src/ssd1306.py
mpremote fs cp src/google_sheets.py :src/google_sheets.py
```

### Paso 4: Ejecución autónoma 24/7
Al energizar el ESP32:
1. MicroPython ejecuta `main.py` automáticamente al arrancar.
2. El script detecta `sys.platform == 'esp32'`, inicializa la pantalla OLED y se conecta al WiFi configurado sincronizando la hora con NTP.
3. Se conecta por socket a la consola Davis, actualiza el dashboard OLED en cada ciclo, escribe en `data/weather.csv` cada 5 minutos (protección contra desgaste de memoria flash) y envía a Weather Underground cada 30 segundos ininterrumpidamente.
