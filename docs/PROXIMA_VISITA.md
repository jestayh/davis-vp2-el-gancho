# Próxima visita a la estación (casa de mis padres)

Pendiente desde el 8 de octubre de 2026. La ESP32 sigue con el firmware **v1.0.0**:
su OTA solo prueba GitHub directo y falla al arrancar por falta de RAM para TLS
(error `-15104`, visto en el log del 23 de septiembre). Por eso hay que instalar
la versión nueva **por cable**. Después de eso, las actualizaciones llegan solas.

## Antes de ir

- [ ] Notebook con el repo al día: `git pull`.
- [ ] `mpremote` instalado (`pip install mpremote`) y el cable USB de la estación.
- [ ] Revisar en [version.json](../version.json) cuál es la última versión publicada (al escribir esto: v1.0.4).

## En la casa

### 1. Instalar el lanzador nuevo y la última versión

Desenchufar la estación del router y conectarla al notebook (antes era `COM3`;
si cambió, `mpremote devs` lo muestra). Desde la carpeta del repo:

```
mpremote connect COM3 cp device/main.py :main.py + run tools/ota_rescue.py
```

- `cp device/main.py` instala el lanzador con **vuelta atrás automática** (no se actualiza por OTA).
- `ota_rescue.py` descarga la última versión desde el Worker por HTTP, verifica las huellas SHA-256, instala y reinicia.

**Si falla el WiFi o el Worker**, copiar los archivos directamente:

```
mpremote connect COM3 cp app.mpy :app.mpy + cp src/display.mpy :src/display.mpy + cp src/davis_reader.mpy :src/davis_reader.mpy + cp src/data_logger.mpy :src/data_logger.mpy + cp src/google_sheets.mpy :src/google_sheets.mpy + cp src/history_recovery.mpy :src/history_recovery.mpy + cp src/ota_updater.mpy :src/ota_updater.mpy
mpremote connect COM3 exec "import json;json.dump({'version':'1.0.4'},open('data/version.json','w'))" + reset
```

(Cambiar `1.0.4` por la versión que diga `version.json`.)

### 2. Mirar el arranque

```
mpremote connect COM3 repl
```

| Debería aparecer | Qué significa |
|---|---|
| `[OTA] Manifiesto obtenido via ...` | **Anotar la vía** (GitHub / Proxy HTTPS / Proxy HTTP). Lo esperado es *Proxy HTTP* |
| `[OTA] Firmware al dia (v1.0.4)` | La versión instalada coincide con la publicada |
| `[RELOJ] Consola desfasada -10800s ... Corrigiendo...` → `Hora de la consola corregida` | La consola estaba 3 h atrasada y quedó en hora |
| `[RELOJ] Hora de la consola OK` | (si ya estaba en hora) |

Salir del REPL con `Ctrl+]` (o `Ctrl+X`).

### 3. En la consola Davis

- [ ] Confirmar que la pantalla muestra la **hora correcta**.
- [ ] En el menú *Setup*, dejar el horario de verano (*Daylight Savings*) en **MANUAL / OFF**.
  La ESP32 se encarga de los cambios de hora de Chile; el modo automático de la consola usa reglas de EE. UU./Europa.

### 4. Dejarla como estaba

- [ ] Volver a enchufar la estación al **puerto USB del router**.
- [ ] Ver en la pantallita que sube datos (destello de WU, luz fija de Sheets).

## Después de la visita

- [ ] Contarle a Claude qué mostró el arranque (sobre todo la vía del OTA), para actualizar su memoria.
- [ ] Esa noche: la lluvia del día en el Google Sheet debe reiniciarse a las **00:00** (no a las 03:00).
- [ ] WeatherLink: los gráficos nuevos deben salir en hora (los registros viejos quedan desfasados).
- [ ] WeatherLink (web/app): zona horaria de la estación = **Santiago**.
- [ ] Google Sheet: *Archivo → Configuración → Zona horaria* = **Santiago**.

## Ideas pendientes (opcionales)

- **Watchdog de hardware**: la vuelta atrás protege contra versiones que se *caen* al arrancar,
  no contra una que se *cuelga*. Agregarlo requiere probarlo con la ESP32 conectada.
- **Cambio de hora de abril** (fin del horario de verano): revisar que la consola se corrija sola
  al día siguiente.
