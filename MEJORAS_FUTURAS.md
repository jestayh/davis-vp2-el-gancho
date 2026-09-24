# Hoja de Ruta y Mejoras Futuras 🚀
### Estación Meteorológica Davis Vantage Pro 2 — El Gancho (Putaendo, Chile)

Este documento recopila las propuestas técnicas, optimizaciones y nuevas funcionalidades identificadas durante la puesta en marcha del proyecto, para ser abordadas en futuras visitas o iteraciones de desarrollo.

---

## 📌 Resumen de Prioridades

| Prioridad | Mejora | Componente | Dificultad |
| :---: | :--- | :--- | :---: |
| 🟢 **Alta** | Rectificación automática del reloj de la consola (`SETTIME`) | Firmware / Davis Reader | Media |
| 🟢 **Alta** | Habilitar actualización remota (OTA) vía Cloudflare Worker | Red / Firmware OTA | Media |
| 🟡 **Media** | Migración a hardware compacto (**ESP32-C3 SuperMini**) | Hardware / Electrónica | Baja |
| 🟡 **Media** | Modo nocturno / Salvaspantallas para display OLED SSD1306 | Pantalla / Firmware | Baja |
| 🔵 **Baja** | Alertas automáticas de batería de consola e ISS vía Telegram | Cloud / Notificaciones | Baja |
| 🔵 **Baja** | Mini servidor Web local en el ESP32 | Firmware / Interfaz | Media |

---

## 1. 🕒 Rectificación Automática de Hora en la Consola Davis (`SETTIME`)

### Contexto:
La consola Davis Vantage Pro 2 cuenta con un reloj de tiempo real (RTC) interno respaldado por baterías. Aunque es muy estable, tiene una desviación natural de 15 a 30 segundos por mes. Además, cuando ocurra el cambio de hora oficial en Chile (primer domingo de abril a horario de invierno UTC-4), la consola quedará desfasada por 1 hora a menos que se ajuste.

### Propuesta Técnica:
- Crear la función `sync_console_time()` en `src/davis_reader.py`.
- **Funcionamiento:**
  1. El ESP32 obtiene la hora legal exacta vía NTP atómico de Internet y aplica el cálculo oficial de horario chileno (`get_chile_offset_hours()`).
  2. Consulta la hora actual de la consola mediante el comando serie `GETTIME\n`.
  3. Si la diferencia es mayor a **30 segundos** (o ante un cambio de huso horario):
     - Envía el comando `SETTIME\n`.
     - Transmite la trama binaria de 6 bytes (`segundo`, `minuto`, `hora`, `día`, `mes`, `año-1900`) junto a los 2 bytes de checksum **CRC-16-CCITT**.
- **Frecuencia:** Ejecutar esta comprobación una vez al día (por ejemplo a las 03:00 AM) para no saturar la consola.

---

## 2. 🌐 Actualización Remota Inalámbrica (OTA) vía Cloudflare Worker

### Contexto:
El módulo actualizador OTA (`src/ota_updater.py`) ya está programado en el firmware, pero al intentar conectarse directamente a `raw.githubusercontent.com:443`, la librería SSL de MicroPython devuelve el error `MBEDTLS_ERR_PK_INVALID_PUBKEY` (-15104) debido a que GitHub utiliza certificados modernos con curvas elípticas que el ESP32 no valida nativamente.

### Propuesta Técnica:
- Utilizar el mismo **Cloudflare Worker** que ya proxyfica con éxito las subidas a Google Sheets.
- **Ruta propuesta:** `https://davis-proxy.tu-worker.workers.dev/ota/version.json` y `/ota/app.mpy`.
- **Beneficio:** Cloudflare entrega certificados RSA / TLS 1.2 estándar que MicroPython negocia en milisegundos sin errores de clave pública.
- **Resultado:** Podrás publicar nuevas versiones desde tu PC en cualquier parte del mundo (`python ota_publish.py "descripcion"`), y el ESP32 en la casa de tus padres se actualizará solo de manera remota e inalámbrica.

---

## 3. 📦 Migración a Hardware Ultra-Compacto (ESP32-C3 SuperMini)

### Contexto:
La placa actual es un ESP32 DevKit de 30/38 pines con procesador dual-core. Como el sistema requiere únicamente 2 pines de I2C y 1 de LED, este módulo puede recuperarse para proyectos más complejos y ser reemplazado por un microcontrolador más pequeño, económico y de menor consumo.

### Propuesta Técnica:
- **Placa sugerida:** **ESP32-C3 SuperMini** (procesador RISC-V a 160 MHz, 400 KB SRAM, 4 MB Flash, puerto USB-C nativo, dimensiones: 22.5 mm × 18 mm).
- **Consumo:** ~30% menor que el ESP32 clásico; se alimenta perfectamente desde el puerto USB del router.
- **Compatibilidad de software:** 100% compatible con los archivos `.py` y `.mpy` de este repositorio.
- **Ajustes requeridos:** Solo redefinir los pines en `config.py`:
  - `SDA = 8`, `SCL = 9` (o pines I2C elegidos).
  - `STATUS_LED_PIN = 8` (LED azul integrado en la SuperMini).
- **Carcasa 3D:** Modelar e imprimir una pequeña cajita tipo llavero para alojar la placa y la pantalla OLED, dejándola lista y protegida junto al router.

---

## 4. 🌙 Protección de Pantalla OLED (Modo Nocturno / Salvaspantallas)

### Contexto:
Aunque el código rota 7 pantallas diferentes cada 5 segundos para mover los píxeles, las pantallas OLED SSD1306 sufren degradación de fósforo azul (*burn-in*) si permanecen encendidas 24/7 durante años.

### Propuesta Técnica:
- **Atenuación nocturna (*Dimming*):**
  - Entre las 23:00 y las 06:30, reducir el contraste del OLED al nivel mínimo visible (`oled.contrast(1)` en lugar de 255).
- **Apagado nocturno opcional:**
  - Apagar la pantalla completamente (`oled.poweroff()`) durante la noche y encenderla automáticamente al amanecer (`oled.poweron()`).
  - Opcional: habilitar un pin táctil capacitivo del ESP32 (*Touch Pin*) para encender la pantalla por 60 segundos si alguien toca un cable o tornillo exterior.

---

## 5. 🔔 Alertas de Salud de Hardware y Baterías (Telegram / WhatsApp)

### Contexto:
El paquete LOOP 1 y LOOP 2 de Davis entrega en cada ciclo el voltaje de las baterías de la consola (`console_battery_v`) y el estado del transmisor inalámbrico del ISS (`tx_battery_status`).

### Propuesta Técnica:
- Cuando la consola funcione a pilas o tras un corte de luz prolongado, si `console_battery_v < 4.2V`, disparar una alerta a través de un bot gratuito de Telegram.
- Si el transmisor solar de campo reporta batería baja (`tx_battery_status != 0`), enviar una notificación de mantenimiento preventivo para revisar el condensador o la pila de litio CR123A del ISS en el huerto.

---

## 6. 📱 Mini Dashboard Web Local en el ESP32

### Contexto:
Estando en la misma red WiFi del router (casa de tus padres), sería útil poder abrir el navegador del teléfono y ver el estado de la estación sin necesidad de entrar a Weather Underground ni a Google Sheets.

### Propuesta Técnica:
- Levantar un servidor socket HTTP mínimo en el puerto 80 del ESP32 (`http://192.168.1.6/`).
- Servir una página HTML responsiva ultra-ligera en modo oscuro que muestre:
  - Tarjeta meteorológica en vivo (temperatura, humedad, viento, lluvia).
  - Estado de las subidas (último envío a WU y Google Sheets).
  - Botón de reinicio o sincronización forzada.

---

*Documento creado el 24 de Septiembre de 2026 para el repositorio [davis-vp2-el-gancho](https://github.com/jestayh/davis-vp2-el-gancho).*
