/**
 * Davis WeatherStation "El Gancho" - Webhook Inteligente con Mapeo Dinámico
 *
 * Características:
 * 1. Mapeo Dinámico por Encabezado: Lee los títulos de la Fila 1 y coloca cada dato
 *    exactamente debajo de su columna correspondiente, sin importar el orden.
 * 2. Orden cronológico: cada fila se inserta en su lugar (las recuperadas de la consola
 *    llegan atrasadas) y una fecha repetida actualiza la fila existente en vez de duplicarla.
 * 3. Rechaza fechas inválidas (ESP32 sin hora: 1999/2000).
 * 4. Completa los acumulados de lluvia y la tendencia barométrica que los registros
 *    recuperados del archivo de la consola no traen.
 * 5. Auto-archivado anual automático en Google Drive el 1 de enero.
 * 6. Pestaña "Estado": un reporte diario de salud del ESP32 (versión, memoria, WiFi, fallas).
 * 7. Publica cada registro nuevo en Windy (estación WINDY_STATION_ID) si la propiedad del
 *    script WINDY_PASSWORD tiene la contraseña de la estación.
 *
 * Funciones para ejecutar a mano desde el editor:
 * - activarAvisos(): correo si la estación pasa 60 min sin enviar datos (y cuando vuelven), y
 *   avisos del tiempo: helada / riesgo de helada, ráfagas fuertes, lluvia intensa y baterías bajas
 *   (umbrales en FROST_C, FROST_WARN_C, GUST_KMH, RAIN_RATE_MMH, RAIN_DAY_MM, CONSOLE_BATTERY_V);
 *   de fallas silenciosas: reinicios repetidos, sensor congelado, mala recepción del transmisor,
 *   Weather Underground o Windy sin recibir; y el resumen diario de las 8:00 (resumenDiario).
 *   Más destinatarios del resumen: propiedad del script CORREOS_RESUMEN (separados por coma).
 * - importarConsola(): rellena el hueco del 08-oct-2026 con la pestaña "Consola".
 * - repararHoja(): borra filas con fecha inválida y duplicados, y vacía Muestras/Tx erróneos.
 * - ordenarPorFecha(): reordena toda la hoja.
 * - estadoWindy(): muestra si el envío a Windy está configurado y la respuesta del último envío.
 */

var MIN_VALID_YEAR = 2025;
var CONSOLE_SHEET_NAME = "Consola";
var STATUS_SHEET_NAME = "Estado";
var KEY_FORMAT = "yyyy-MM-dd HH:mm:ss";
// Daily health report of the ESP32: [column title, field sent by the firmware]
var STATUS_COLUMNS = [
  ["Fecha / Hora", "timestamp"],
  ["Firmware", "fw"],
  ["Encendido (h)", "uptime_h"],
  ["Último arranque", "reset_cause"],
  ["RAM libre (KB)", "mem_free_kb"],
  ["RAM mínima (KB)", "mem_min_kb"],
  ["WiFi (dBm)", "rssi"],
  ["Fallas consola", "err_console"],
  ["Fallas Sheets", "err_sheets"],
  ["Fallas WU", "err_wu"],
  ["Recuperaciones consola", "recoveries"],
  ["Errores pantalla", "err_display"],
  ["Memoria sistema mín (KB)", "sys_min_kb"],   // WiFi y conexiones: bajo ~8 KB ya no puede conectarse
  ["Red WiFi", "ssid"],
  ["Ruta OTA", "ota_route"],
  ["Registros ayer", "records_yesterday"]     // calculado aquí: filas del día anterior (lo normal, 144)
];
// Celdas del reporte que se marcan en rojo: [campo, función que decide si el valor es malo]
var STATUS_LIMITS = [
  ["reset_cause", function (v) { return v === "Watchdog" || v === "Memoria agotada"; }],
  ["mem_min_kb", function (v) { return v !== "" && v < 30; }],
  ["rssi", function (v) { return v !== "" && v < -80; }],
  ["err_console", function (v) { return v > 50; }],
  ["err_sheets", function (v) { return v > 3; }],
  ["err_wu", function (v) { return v > 144; }],              // 5 % de ~2.880 envíos al día
  ["err_display", function (v) { return v > 0; }],
  ["sys_min_kb", function (v) { return v !== "" && v < 12; }],
  ["records_yesterday", function (v) { return v !== "" && v < 140; }]
];
var STATUS_BAD_COLOR = "#f4cccc";
// Pluviómetro de 0,01" por golpe: los acumulados se suman en golpes para no arrastrar el redondeo a 0,1 mm
var RAIN_CLICK_MM = 0.254;

function doPost(e) {
  var lock = LockService.getScriptLock();
  lock.waitLock(25000);
  try {
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var data = JSON.parse(e.postData.contents);

    // Reporte diario del ESP32: su fecha trae el prefijo "ESTADO " para que una versión
    // anterior de este script lo rechace en vez de mezclarlo con los datos del clima
    if (data.destino === "estado") {
      return text_(appendStatus_(ss, data));
    }

    // Filas de respaldo de la consola: traen la hora de la consola con prefijo "CONSOLA "
    var toConsole = data.destino === "consola";
    var key = normalizeKey_(String(data.timestamp || "").replace(/^CONSOLA\s+/, ""));
    if (!key || Number(key.substring(0, 4)) < MIN_VALID_YEAR) {
      return text_("IGNORED: fecha invalida '" + data.timestamp + "'");
    }

    var sheet = toConsole ? getConsoleSheet_(ss) : getMainSheet_(ss);
    if (!toConsole) {
      handleNewYear_(ss, key);
      ensureHeader_(sheet, "Reinicio");
      ensureHeader_(sheet, "Fallos WU");
    }

    var headers = getHeaders_(sheet);
    var row = buildRow_(headers, data, key);
    upsertRow_(sheet, headers, row, key, !toConsole);
    if (!toConsole) enviarWindy_(sheet, headers, key, data);

    return text_("SUCCESS");
  } catch (err) {
    return text_("ERROR: " + err.toString());
  } finally {
    lock.releaseLock();
  }
}

function text_(msg) {
  return ContentService.createTextOutput(msg).setMimeType(ContentService.MimeType.TEXT);
}

// ---------------------------------------------------------------------------------------
// Hojas y encabezados
// ---------------------------------------------------------------------------------------

function getMainSheet_(ss) {
  var sheets = ss.getSheets();
  for (var i = 0; i < sheets.length; i++) {
    var name = sheets[i].getName();
    if (name !== CONSOLE_SHEET_NAME && name !== STATUS_SHEET_NAME) return sheets[i];
  }
  return sheets[0];
}

/** Appends one health report to the "Estado" tab (created at the end on first use) */
function appendStatus_(ss, data) {
  var key = normalizeKey_(String(data.timestamp || "").replace(/^ESTADO\s+/, ""));
  if (!key || Number(key.substring(0, 4)) < MIN_VALID_YEAR) {
    return "IGNORED: fecha invalida '" + data.timestamp + "'";
  }
  var sheet = ss.getSheetByName(STATUS_SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(STATUS_SHEET_NAME, ss.getSheets().length);
    var titles = STATUS_COLUMNS.map(function (c) { return c[0]; });
    sheet.getRange(1, 1, 1, titles.length).setValues([titles]);
    sheet.getRange(1, 1, 1, titles.length).setFontWeight("bold").setBackground("#202124").setFontColor("#FFFFFF");
    sheet.setFrozenRows(1);
  }
  // Columnas agregadas en versiones posteriores van al final de una pestaña ya existente
  STATUS_COLUMNS.forEach(function (c) { ensureHeader_(sheet, c[0]); });
  var headers = getHeaders_(sheet);
  var tz = ss.getSpreadsheetTimeZone();
  data.records_yesterday = recordsOfDay_(getMainSheet_(ss), tz, shiftKey_(key, -1440).substring(0, 10));
  var fields = headers.map(function (h) {
    for (var i = 0; i < STATUS_COLUMNS.length; i++) {
      if (STATUS_COLUMNS[i][0] === h) return STATUS_COLUMNS[i][1];
    }
    return null;
  });
  var row = fields.map(function (f, i) {
    return i === 0 ? key : f ? v_(data[f], "") : "";
  });
  sheet.appendRow(row);

  var rowNum = sheet.getLastRow();
  STATUS_LIMITS.forEach(function (lim) {
    var c = fields.indexOf(lim[0]);
    if (c >= 0 && row[c] !== "" && lim[1](row[c])) {
      sheet.getRange(rowNum, c + 1).setBackground(STATUS_BAD_COLOR);
    }
  });
  return "SUCCESS";
}

/** Filas de la hoja principal con fecha `day` ("yyyy-MM-dd"), mirando solo las últimas 400 */
function recordsOfDay_(sheet, tz, day) {
  var lastRow = sheet.getLastRow();
  var n = Math.min(TAIL_ROWS, lastRow - 1);
  if (n < 1) return 0;
  var count = 0;
  sheet.getRange(lastRow - n + 1, 1, n, 1).getValues().forEach(function (r) {
    var k = keyOf_(r[0], tz);
    if (k && k.substring(0, 10) === day) count++;
  });
  return count;
}

function getConsoleSheet_(ss) {
  var sheet = ss.getSheetByName(CONSOLE_SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(CONSOLE_SHEET_NAME, ss.getSheets().length);
    createHeaders(sheet);
  }
  return sheet;
}

function getHeaders_(sheet) {
  var numCols = sheet.getLastColumn();
  if (numCols === 0 || sheet.getLastRow() === 0) {
    createHeaders(sheet);
    numCols = sheet.getLastColumn();
  }
  return sheet.getRange(1, 1, 1, numCols).getValues()[0];
}

function ensureHeader_(sheet, name) {
  var headers = getHeaders_(sheet);
  for (var i = 0; i < headers.length; i++) {
    if (String(headers[i]).toLowerCase().trim() === name.toLowerCase()) return;
  }
  var cell = sheet.getRange(1, headers.length + 1);
  cell.setValue(name).setFontWeight("bold").setBackground("#202124").setFontColor("#FFFFFF");
}

function createHeaders(sheet) {
  if (!sheet) {
    sheet = getMainSheet_(SpreadsheetApp.getActiveSpreadsheet());
  }
  var headers = [
    "Fecha / Hora",
    "Temp Ext (°C)",
    "Temp Máx 10m (°C)",
    "Temp Mín 10m (°C)",
    "Humedad Ext (%)",
    "Punto Rocío (°C)",
    "Bulbo Húmedo (°C)",
    "Viento Actual (km/h)",
    "Dirección Viento (°)",
    "Viento Sost 2m (km/h)",
    "Viento Med 10m (km/h)",
    "Recorrido Viento (km)",
    "Ráfaga 10m (km/h)",
    "Dir Ráfaga (°)",
    "Viento Frío (°C)",
    "Índice Calor (°C)",
    "Índice THW (°C)",
    "Presión (hPa)",
    "Tendencia Baro",
    "Lluvia Int (mm)",
    "Tasa Lluvia (mm/h)",
    "Lluvia Día (mm)",
    "Lluvia Tormenta (mm)",
    "Lluvia Mes (mm)",
    "Lluvia Año (mm)",
    "Grados Día Calef (°C-d)",
    "Grados Día Enfr (°C-d)",
    "Temp Int (°C)",
    "Humedad Int (%)",
    "Punto Rocío Int (°C)",
    "Índice Calor Int (°C)",
    "EMC Int (%)",
    "Densidad Aire Int (kg/m³)",
    "Muestras Viento",
    "Tx Viento",
    "Recepción ISS (%)",
    "Intervalo Archivo (min)",
    "Batería Consola (V)",
    "Batería Transmisor",
    "Radiación Solar (W/m²)",
    "Índice UV",
    "Reinicio",
    "Fallos WU"
  ];
  sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  sheet.getRange(1, 1, 1, headers.length).setFontWeight("bold").setBackground("#202124").setFontColor("#FFFFFF");
  sheet.setFrozenRows(1);
}

/** Índice (0-based) de la primera columna cuyo encabezado contiene todos los fragmentos */
function colIndex_(headers, fragments) {
  for (var i = 0; i < headers.length; i++) {
    var h = String(headers[i]).toLowerCase();
    var ok = true;
    for (var j = 0; j < fragments.length; j++) {
      if (h.indexOf(fragments[j]) === -1) { ok = false; break; }
    }
    if (ok) return i;
  }
  return -1;
}

// ---------------------------------------------------------------------------------------
// Mapeo de datos a columnas
// ---------------------------------------------------------------------------------------

function v_(value, fallback) {
  return value !== undefined && value !== null && value !== "" ? value : fallback;
}

function buildRow_(headers, data, key) {
  var row = [];
  for (var i = 0; i < headers.length; i++) {
    var h = headers[i].toString().toLowerCase().trim();
    var val = "";

    if (h.indexOf("fecha") !== -1 || h.indexOf("hora") !== -1) {
      val = key;
      // Antes que las reglas genéricas de "viento": sus encabezados también contienen esa palabra
    } else if (h.indexOf("muestras") !== -1 || h.indexOf("wind samp") !== -1) {
      val = v_(data.wind_samples, "");
    } else if (h.indexOf("tx") !== -1 && h.indexOf("viento") !== -1) {
      val = v_(data.wind_tx, "");
    } else if (h.indexOf("reinicio") !== -1) {
      val = v_(data.reset_cause, "");
    } else if (h.indexOf("fallos wu") !== -1) {
      // Envíos a Weather Underground que fallaron en el intervalo (de ~20)
      val = v_(data.wu_err, "");
    } else if (h.indexOf("temp max") !== -1 || h.indexOf("temp máx") !== -1 || h.indexOf("hi temp") !== -1) {
      val = v_(data.temp_out_hi_c, "");
    } else if (h.indexOf("temp min") !== -1 || h.indexOf("temp mín") !== -1 || h.indexOf("low temp") !== -1) {
      val = v_(data.temp_out_low_c, "");
    } else if (h.indexOf("frio") !== -1 || h.indexOf("frío") !== -1 || h.indexOf("chill") !== -1) {
      val = v_(data.wind_chill_c, "");
    } else if (h.indexOf("calor int") !== -1 || h.indexOf("in heat") !== -1) {
      val = v_(data.in_heat_c, "");
    } else if (h.indexOf("calor") !== -1 || h.indexOf("heat") !== -1) {
      val = v_(data.heat_index_c, "");
    } else if (h.indexOf("thw") !== -1) {
      val = v_(data.thw_index_c, "");
    } else if (h.indexOf("rocio int") !== -1 || h.indexOf("rocío int") !== -1 || h.indexOf("in dew") !== -1) {
      val = v_(data.in_dew_c, "");
    } else if (h.indexOf("rocio") !== -1 || h.indexOf("rocío") !== -1 || h.indexOf("dew") !== -1) {
      val = v_(data.dewpoint_c, "");
    } else if (h.indexOf("bulbo") !== -1 || h.indexOf("wet") !== -1) {
      val = v_(data.wet_bulb_c, "");
    } else if (h.indexOf("temp ext") !== -1 || (h.indexOf("temp") !== -1 && h.indexOf("ext") !== -1)) {
      val = v_(data.temp_out_c, "");
    } else if (h.indexOf("humedad ext") !== -1 || (h.indexOf("hum") !== -1 && h.indexOf("ext") !== -1)) {
      val = v_(data.humidity_out, "");
    } else if (h.indexOf("viento sost") !== -1 || h.indexOf("2m") !== -1) {
      val = v_(data.windspdkmh_avg2m, "");
    } else if (h.indexOf("viento med") !== -1 || h.indexOf("10m avg") !== -1 || (h.indexOf("10m") !== -1 && h.indexOf("med") !== -1)) {
      val = v_(data.wind_speed_10min_avg_kmh, v_(data.wind_speed_kmh, ""));
    } else if (h.indexOf("recorrido") !== -1 || h.indexOf("wind run") !== -1) {
      val = v_(data.wind_run_km, 0.0);
    } else if (h.indexOf("dir") !== -1 && (h.indexOf("raf") !== -1 || h.indexOf("ráf") !== -1)) {
      val = v_(data.windgustdir_10m, "");
    } else if (h.indexOf("rafaga") !== -1 || h.indexOf("ráfaga") !== -1 || h.indexOf("gust") !== -1) {
      val = v_(data.windgustkmh_10m, "");
    } else if (h.indexOf("dir") !== -1 && h.indexOf("viento") !== -1) {
      val = v_(data.wind_direction_deg, "");
    } else if (h.indexOf("viento actual") !== -1 || h.indexOf("viento") !== -1 || h.indexOf("wind") !== -1) {
      val = v_(data.wind_speed_kmh, "");
    } else if (h.indexOf("tendencia") !== -1) {
      val = v_(data.barometer_trend, "");
    } else if (h.indexOf("presion") !== -1 || h.indexOf("presión") !== -1 || h.indexOf("baro") !== -1) {
      val = v_(data.pressure_hpa, "");
    } else if (h.indexOf("lluvia int") !== -1 || h.indexOf("rain int") !== -1) {
      // Vacía en el primer registro tras un reinicio: se deduce de la lluvia del día en fillDerived_
      val = v_(data.rain_interval_mm, "");
    } else if (h.indexOf("tasa") !== -1 && h.indexOf("lluvia") !== -1) {
      val = v_(data.rain_rate_mm_per_hr, 0.0);
      // Acumulados: vacíos si no vienen (registros de archivo), se completan en fillDerived_
    } else if (h.indexOf("tormenta") !== -1) {
      val = v_(data.storm_rain_mm, "");
    } else if (h.indexOf("lluvia") !== -1 && (h.indexOf("mes") !== -1 || h.indexOf("month") !== -1)) {
      val = v_(data.rain_month_mm, "");
    } else if (h.indexOf("lluvia") !== -1 && (h.indexOf("ano") !== -1 || h.indexOf("año") !== -1 || h.indexOf("year") !== -1)) {
      val = v_(data.rain_year_mm, "");
    } else if (h.indexOf("lluvia") !== -1 && (h.indexOf("dia") !== -1 || h.indexOf("día") !== -1 || h.indexOf("day") !== -1)) {
      val = v_(data.rain_day_mm, "");
    } else if (h.indexOf("calef") !== -1 || h.indexOf("heat d-d") !== -1) {
      val = v_(data.heat_dd, 0.0);
    } else if (h.indexOf("enfr") !== -1 || h.indexOf("cool d-d") !== -1) {
      val = v_(data.cool_dd, 0.0);
    } else if (h.indexOf("temp int") !== -1 || (h.indexOf("temp") !== -1 && h.indexOf("int") !== -1)) {
      val = v_(data.temp_in_c, "");
    } else if (h.indexOf("humedad int") !== -1 || (h.indexOf("hum") !== -1 && h.indexOf("int") !== -1)) {
      val = v_(data.humidity_in, "");
    } else if (h.indexOf("emc") !== -1) {
      val = v_(data.in_emc, "");
    } else if (h.indexOf("densidad") !== -1 || h.indexOf("air density") !== -1) {
      val = v_(data.in_air_density, "");
    } else if (h.indexOf("recept") !== -1 || h.indexOf("recepcion") !== -1 || h.indexOf("recepción") !== -1) {
      val = v_(data.iss_recept, "");
    } else if (h.indexOf("intervalo") !== -1 || h.indexOf("arc int") !== -1 || h.indexOf("arc. int") !== -1) {
      val = v_(data.arc_int, 10);
    } else if (h.indexOf("consola") !== -1 || (h.indexOf("bat") !== -1 && h.indexOf("v") !== -1)) {
      val = v_(data.console_battery_v, "");
    } else if (h.indexOf("transmisor") !== -1 || h.indexOf("iss") !== -1) {
      val = data.tx_battery_status === 0 ? "OK" : (data.tx_battery_status === 1 ? "Baja" : v_(data.tx_battery_status, ""));
    } else if (h.indexOf("solar") !== -1 || h.indexOf("radiacion") !== -1 || h.indexOf("radiación") !== -1) {
      val = v_(data.solar_radiation_wm2, "");
    } else if (h.indexOf("uv") !== -1) {
      val = v_(data.uv_index, "");
    }
    row.push(val);
  }
  return row;
}

// ---------------------------------------------------------------------------------------
// Fechas: todas se comparan como texto "yyyy-MM-dd HH:mm:ss" (independiente de zonas horarias)
// ---------------------------------------------------------------------------------------

function pad2_(n) {
  return (n < 10 ? "0" : "") + n;
}

/** Convierte "2026-10-08 9:40" / "2026-10-08T09:40:00" a "2026-10-08 09:40:00"; null si no es fecha */
function normalizeKey_(s) {
  var m = String(s).trim().match(/^(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?/);
  if (!m) return null;
  return m[1] + "-" + pad2_(+m[2]) + "-" + pad2_(+m[3]) + " " + pad2_(+m[4]) + ":" + m[5] + ":" + pad2_(+(m[6] || 0));
}

/** Clave de una celda de la columna Fecha (Sheets convierte el texto en fecha) */
function keyOf_(value, tz) {
  if (value instanceof Date) return Utilities.formatDate(value, tz, KEY_FORMAT);
  return value === "" || value === null ? null : normalizeKey_(value);
}

/** Minutos desde 1970 de una clave (aritmética pura sobre la hora local escrita) */
function keyMinutes_(key) {
  return Date.UTC(+key.substring(0, 4), +key.substring(5, 7) - 1, +key.substring(8, 10),
    +key.substring(11, 13), +key.substring(14, 16)) / 60000;
}

function shiftKey_(key, minutes) {
  return Utilities.formatDate(new Date((keyMinutes_(key) + minutes) * 60000), "UTC", KEY_FORMAT);
}

function readKeys_(sheet, tz) {
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return [];
  var vals = sheet.getRange(2, 1, lastRow - 1, 1).getValues();
  var keys = [];
  for (var i = 0; i < vals.length; i++) keys.push(keyOf_(vals[i][0], tz));
  return keys;
}

// Filas que se leen desde el final para ubicar un registro nuevo (~2,8 días de datos cada 10 min)
var TAIL_ROWS = 400;
// Filas anteriores que fillDerived_ necesita conocer (lluvia de las últimas 24 h, presión de hace 3 h)
var CONTEXT_ROWS = 150;

/**
 * Como readKeys_, pero leyendo solo las últimas TAIL_ROWS filas cuando el registro va al final
 * (lo normal: la hoja crece ~52.000 filas al año y leerla entera en cada envío la hacía lenta).
 * Las posiciones no leídas quedan vacías (undefined). Si la fecha cae antes de la ventana, o tan
 * cerca de su comienzo que faltarían filas previas para completar acumulados, lee la hoja entera.
 */
function readKeysNear_(sheet, tz, key) {
  var n = sheet.getLastRow() - 1;
  if (n <= TAIL_ROWS) return readKeys_(sheet, tz);
  var offset = n - TAIL_ROWS;
  var vals = sheet.getRange(offset + 2, 1, TAIL_ROWS, 1).getValues();
  var keys = new Array(n);
  for (var i = 0; i < TAIL_ROWS; i++) keys[offset + i] = keyOf_(vals[i][0], tz);
  var guard = keys[offset + CONTEXT_ROWS];
  if (!guard || key <= guard) return readKeys_(sheet, tz);
  return keys;
}

// ---------------------------------------------------------------------------------------
// Inserción ordenada sin duplicados
// ---------------------------------------------------------------------------------------

/**
 * Inserta la fila en su lugar cronológico; si la fecha ya existe, la reemplaza.
 * Con fillMissing completa acumulados de lluvia y tendencia vacíos a partir de las filas previas.
 */
function upsertRow_(sheet, headers, row, key, fillMissing) {
  var tz = sheet.getParent().getSpreadsheetTimeZone();
  var keys = readKeysNear_(sheet, tz, key);

  // Posición: índice de la fila igual, o de la primera posterior (se busca desde el final)
  var pos = keys.length;
  var exists = false;
  for (var i = keys.length - 1; i >= 0; i--) {
    if (keys[i] === key) { pos = i; exists = true; break; }
    if (keys[i] !== null && keys[i] < key) { pos = i + 1; break; }
    if (i === 0) pos = 0;
  }

  if (fillMissing) fillDerived_(sheet, headers, row, key, keys, pos);

  if (exists) {
    sheet.getRange(pos + 2, 1, 1, row.length).setValues([row]);
  } else if (pos >= keys.length) {
    sheet.appendRow(row);
  } else {
    sheet.insertRowBefore(pos + 2);
    sheet.getRange(pos + 2, 1, 1, row.length).setValues([row]);
  }

  // La fila siguiente (ahora en pos + 3) pudo quedar sin "Lluvia Int" por no tener la anterior
  if (fillMissing) fillNextInterval_(sheet, headers, row, key, exists ? keys[pos + 1] : keys[pos], pos + 3);
}

/**
 * Lluvia del intervalo a partir de la lluvia del día: solo si la fila anterior es exactamente
 * 10 min antes (si hubo un hueco, la diferencia incluiría la lluvia de los registros faltantes).
 * Si la lluvia del día bajó, la consola reinició el día (a su medianoche, que no siempre fue la
 * de la hoja), así que todo lo del día cayó en el intervalo. Se resta sobre los valores redondeados
 * para que la suma de "Lluvia Int" de un día calce exacto con su "Lluvia Día".
 */
function intervalFromDay_(key, day, prevKey, prevDay) {
  if (!prevKey || day === "" || prevDay === "" || keyMinutes_(key) - keyMinutes_(prevKey) !== 10) return "";
  var now = num_(day), before = num_(prevDay);
  return round1_(now >= before ? now - before : now);
}

function rainClicks_(mm) {
  return Math.round(num_(mm) / RAIN_CLICK_MM);
}

function clicksToMm_(clicks) {
  return round1_(clicks * RAIN_CLICK_MM);
}

/** Suma de dos cantidades de lluvia en golpes del pluviómetro */
function addRain_(a, b) {
  return clicksToMm_(rainClicks_(a) + rainClicks_(b));
}

function fillNextInterval_(sheet, headers, row, key, nextKey, nextRowNum) {
  var cInt = colIndex_(headers, ["lluvia int"]);
  var cDay = colIndex_(headers, ["lluvia d"]);
  if (!nextKey || cInt < 0 || cDay < 0 || nextRowNum > sheet.getLastRow()) return;
  var intCell = sheet.getRange(nextRowNum, cInt + 1);
  if (intCell.getValue() !== "") return;
  var value = intervalFromDay_(nextKey, sheet.getRange(nextRowNum, cDay + 1).getValue(), key, row[cDay]);
  if (value !== "") intCell.setValue(value);
}

function num_(x) {
  var n = parseFloat(x);
  return isNaN(n) ? 0 : n;
}

function round1_(x) {
  return Math.round(x * 10) / 10;
}

/**
 * Completa Lluvia Día/Tormenta/Mes/Año y Tendencia Baro cuando vienen vacíos, continuando
 * desde las filas anteriores (hasta 24 h): el día, mes y año se reinician al cambiar de fecha;
 * la tormenta termina tras 24 h sin lluvia, como en la consola Davis.
 */
function fillDerived_(sheet, headers, row, key, keys, pos) {
  var cInt = colIndex_(headers, ["lluvia int"]);
  var cDay = colIndex_(headers, ["lluvia d"]);
  var cStorm = colIndex_(headers, ["tormenta"]);
  var cMonth = colIndex_(headers, ["lluvia mes"]);
  var cYear = colIndex_(headers, ["lluvia a"]);
  var cTrend = colIndex_(headers, ["tendencia"]);
  var cPres = colIndex_(headers, ["presi"]);

  var needInt = cInt >= 0 && row[cInt] === "" && cDay >= 0 && row[cDay] !== "";
  var needRain = [cDay, cStorm, cMonth, cYear].some(function (c) { return c >= 0 && row[c] === ""; });
  var needTrend = cTrend >= 0 && row[cTrend] === "" && cPres >= 0 && row[cPres] !== "";
  if ((!needInt && !needRain && !needTrend) || pos === 0) return;

  var first = Math.max(0, pos - 150);
  var prevRows = sheet.getRange(first + 2, 1, pos - first, headers.length).getValues();
  var prev = prevRows[prevRows.length - 1];
  var prevKey = keys[pos - 1];
  if (!prevKey) return;
  var nowMin = keyMinutes_(key);

  if (needInt) row[cInt] = intervalFromDay_(key, row[cDay], prevKey, prev[cDay]);

  if (needRain && nowMin - keyMinutes_(prevKey) <= 24 * 60) {
    var rain = cInt >= 0 ? num_(row[cInt]) : 0;
    if (cDay >= 0 && row[cDay] === "") {
      row[cDay] = addRain_(prevKey.substring(0, 10) === key.substring(0, 10) ? prev[cDay] : 0, rain);
    }
    if (cMonth >= 0 && row[cMonth] === "") {
      row[cMonth] = addRain_(prevKey.substring(0, 7) === key.substring(0, 7) ? prev[cMonth] : 0, rain);
    }
    if (cYear >= 0 && row[cYear] === "") {
      row[cYear] = addRain_(prevKey.substring(0, 4) === key.substring(0, 4) ? prev[cYear] : 0, rain);
    }
    if (cStorm >= 0 && row[cStorm] === "") {
      var rainedLast24h = rain > 0;
      for (var i = prevRows.length - 1; i >= 0 && !rainedLast24h; i--) {
        var k = keys[first + i];
        if (!k || nowMin - keyMinutes_(k) >= 24 * 60) break;
        if (num_(prevRows[i][cInt]) > 0) rainedLast24h = true;
      }
      row[cStorm] = rainedLast24h ? addRain_(prev[cStorm], rain) : 0;
    }
  }

  if (needTrend) {
    // Cambio de presión en 3 h, con los umbrales de la consola Davis (0,02 y 0,06 inHg)
    for (var j = prevRows.length - 1; j >= 0; j--) {
      var kj = keys[first + j];
      if (!kj) continue;
      var age = nowMin - keyMinutes_(kj);
      if (age > 190) break;
      if (age >= 170 && prevRows[j][cPres] !== "") {
        var d = num_(row[cPres]) - num_(prevRows[j][cPres]);
        row[cTrend] = d <= -2.0 ? "Bajando Rápido" : d <= -0.7 ? "Bajando Lento" :
          d < 0.7 ? "Estable" : d < 2.0 ? "Subiendo Lento" : "Subiendo Rápido";
        break;
      }
    }
  }
}

// ---------------------------------------------------------------------------------------
// Windy (stations.windy.com, API v2)
// ---------------------------------------------------------------------------------------

var WINDY_STATION_ID = "NPLL5k7";
var WINDY_URL = "https://stations.windy.com/api/v2/observation/update";
// Solo registros recientes: los recuperados de la consola llegan con horas de atraso
var WINDY_MAX_AGE_MINUTES = 30;

/**
 * Publica el registro en Windy. La contraseña de la estación va en la propiedad del script
 * WINDY_PASSWORD (nunca en el código: el repositorio es público) y viaja en la cabecera
 * Authorization. Nunca lanza errores: una falla de Windy no debe impedir guardar la fila.
 * La respuesta queda en la propiedad WINDY_ULTIMO (ver estadoWindy()).
 */
function enviarWindy_(sheet, headers, key, data) {
  var props = PropertiesService.getScriptProperties();
  try {
    var password = props.getProperty("WINDY_PASSWORD");
    if (!password) return;
    var tz = sheet.getParent().getSpreadsheetTimeZone();
    var nowKey = Utilities.formatDate(new Date(), tz, KEY_FORMAT);
    var age = keyMinutes_(nowKey) - keyMinutes_(key);
    if (age > WINDY_MAX_AGE_MINUTES || age < -10) return;

    var params = windyParams_(data, rainLastHour_(sheet, headers, tz, key));
    params.id = WINDY_STATION_ID;
    params.ts = Math.round(Date.now() / 1000) - age * 60;
    var query = Object.keys(params).map(function (k) {
      return k + "=" + encodeURIComponent(params[k]);
    }).join("&");

    var resp = UrlFetchApp.fetch(WINDY_URL + "?" + query, {
      method: "get",
      headers: { Authorization: "Bearer " + password },
      muteHttpExceptions: true
    });
    var code = resp.getResponseCode();
    props.setProperty("WINDY_ULTIMO", key + " -> HTTP " + code +
      (code === 200 ? "" : ": " + String(resp.getContentText()).slice(0, 150)));
    contarWindy_(props, key, code === 200);
  } catch (err) {
    props.setProperty("WINDY_ULTIMO", key + " -> " + String(err).slice(0, 150));
    contarWindy_(props, key, false);
  }
}

/** Envíos a Windy aceptados y fallidos por día (últimos 3 días), y hora del último aceptado */
function contarWindy_(props, key, ok) {
  var day = key.substring(0, 10);
  var counts = JSON.parse(props.getProperty("WINDY_CONTEO") || "{}");
  var c = counts[day] || [0, 0];
  c[ok ? 0 : 1]++;
  counts[day] = c;
  Object.keys(counts).sort().slice(0, -3).forEach(function (d) { delete counts[d]; });
  props.setProperty("WINDY_CONTEO", JSON.stringify(counts));
  if (ok) props.setProperty("WINDY_ULTIMO_OK", key);
}

/** Parámetros de Windy en sus unidades: °C, m/s, Pa, mm en la última hora, W/m² */
function windyParams_(data, precip) {
  var p = {};
  var put = function (name, value, factor, decimals) {
    if (value === undefined || value === null || value === "" || isNaN(Number(value))) return;
    var f = Math.pow(10, decimals);
    p[name] = Math.round(Number(value) * factor * f) / f;
  };
  put("temp", data.temp_out_c, 1, 1);
  put("dewpoint", data.dewpoint_c, 1, 1);
  put("humidity", data.humidity_out, 1, 0);
  put("wind", v_(data.wind_speed_10min_avg_kmh, data.wind_speed_kmh), 1 / 3.6, 1);
  put("gust", data.windgustkmh_10m, 1 / 3.6, 1);
  put("winddir", data.wind_direction_deg, 1, 0);
  put("pressure", data.pressure_hpa, 100, 0);
  put("precip", precip, 1, 1);
  put("uv", data.uv_index, 1, 1);
  put("solarradiation", data.solar_radiation_wm2, 1, 0);
  if (p.winddir !== undefined) p.winddir = p.winddir % 360;
  return p;
}

/** Lluvia de la hora que termina en key (suma de "Lluvia Int"); "" si no hay datos */
function rainLastHour_(sheet, headers, tz, key) {
  var cInt = colIndex_(headers, ["lluvia int"]);
  var n = Math.min(12, sheet.getLastRow() - 1);
  if (cInt < 0 || n < 1) return "";
  var vals = sheet.getRange(sheet.getLastRow() - n + 1, 1, n, cInt + 1).getValues();
  var end = keyMinutes_(key), clicks = 0, found = false;
  for (var i = 0; i < vals.length; i++) {
    var k = keyOf_(vals[i][0], tz);
    if (!k || vals[i][cInt] === "") continue;
    var age = end - keyMinutes_(k);
    if (age >= 0 && age < 60) { clicks += rainClicks_(vals[i][cInt]); found = true; }
  }
  return found ? clicksToMm_(clicks) : "";
}

/** Para ejecutar a mano: muestra si Windy está configurado y cómo respondió el último envío */
function estadoWindy() {
  var props = PropertiesService.getScriptProperties();
  Logger.log("Estación Windy: " + WINDY_STATION_ID);
  Logger.log("Contraseña (WINDY_PASSWORD): " + (props.getProperty("WINDY_PASSWORD") ? "configurada" : "FALTA"));
  Logger.log("Último envío: " + (props.getProperty("WINDY_ULTIMO") || "ninguno todavía"));
}

// ---------------------------------------------------------------------------------------
// Archivo anual
// ---------------------------------------------------------------------------------------

function handleNewYear_(ss, key) {
  var incomingYear = Number(key.substring(0, 4));
  var props = PropertiesService.getScriptProperties();
  var lastRecordedYear = parseInt(props.getProperty("LAST_RECORDED_YEAR"), 10);

  // Un año guardado inválido (p. ej. 1999 por un ESP32 sin hora) no debe disparar el archivado
  if (isNaN(lastRecordedYear) || lastRecordedYear < MIN_VALID_YEAR) {
    props.setProperty("LAST_RECORDED_YEAR", String(incomingYear));
    return;
  }
  if (incomingYear > lastRecordedYear) {
    archiveCurrentYear(ss, String(lastRecordedYear));
    props.setProperty("LAST_RECORDED_YEAR", String(incomingYear));
  }
}

function archiveCurrentYear(ss, year) {
  var currentFile = DriveApp.getFileById(ss.getId());
  var parentFolder = currentFile.getParents().hasNext() ? currentFile.getParents().next() : DriveApp.getRootFolder();
  var archiveName = currentFile.getName().replace(/ \d{4}$/, "") + " - " + year;
  currentFile.makeCopy(archiveName, parentFolder);

  var sheet = getMainSheet_(ss);
  var lastRow = sheet.getLastRow();
  if (lastRow > 1) {
    sheet.deleteRows(2, lastRow - 1);
  }
}

// ---------------------------------------------------------------------------------------
// Funciones manuales
// ---------------------------------------------------------------------------------------

// ---------------------------------------------------------------------------------------
// Aviso por correo cuando la estación deja de enviar datos
// ---------------------------------------------------------------------------------------

var ALERT_AFTER_MINUTES = 60;
var ALERT_CHECK_EVERY_MINUTES = 30;

/**
 * Ejecutar UNA vez desde el editor: programa revisarEstacion() cada 30 min y la corre de inmediato.
 * Google pedirá permiso para enviar correos y ejecutar tareas programadas.
 */
function activarAvisos() {
  desactivarAvisos();
  ScriptApp.newTrigger("revisarEstacion").timeBased().everyMinutes(ALERT_CHECK_EVERY_MINUTES).create();
  ScriptApp.newTrigger("resumenDiario").timeBased().everyDays(1).atHour(SUMMARY_HOUR).create();
  Logger.log("Avisos activados: se revisa cada " + ALERT_CHECK_EVERY_MINUTES + " min; correo a " +
             Session.getEffectiveUser().getEmail() + " tras " + ALERT_AFTER_MINUTES + " min sin datos. " +
             "Resumen diario a las " + SUMMARY_HOUR + ":00.");
  revisarEstacion();
}

function desactivarAvisos() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    var fn = t.getHandlerFunction();
    if (fn === "revisarEstacion" || fn === "resumenDiario") ScriptApp.deleteTrigger(t);
  });
}

/**
 * Manda un correo si el último registro tiene más de ALERT_AFTER_MINUTES, y otro cuando vuelven
 * los datos. Solo avisa una vez por corte (el estado se guarda en las propiedades del script).
 */
function revisarEstacion() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var tz = ss.getSpreadsheetTimeZone();
  var sheet = getMainSheet_(ss);
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return;

  // Fecha más reciente de las últimas filas (la hoja está ordenada; se ignoran celdas no válidas)
  var n = Math.min(30, lastRow - 1);
  var lastKey = null;
  sheet.getRange(lastRow - n + 1, 1, n, 1).getValues().forEach(function (r) {
    var k = keyOf_(r[0], tz);
    if (k && Number(k.substring(0, 4)) >= MIN_VALID_YEAR && (!lastKey || k > lastKey)) lastKey = k;
  });
  if (!lastKey) return;

  var nowKey = Utilities.formatDate(new Date(), tz, KEY_FORMAT);
  var silent = Math.round(keyMinutes_(nowKey) - keyMinutes_(lastKey));
  var props = PropertiesService.getScriptProperties();
  var alertedSince = props.getProperty("ALERTA_SIN_DATOS_DESDE");
  var to = Session.getEffectiveUser().getEmail();
  var url = ss.getUrl();

  if (silent >= ALERT_AFTER_MINUTES && !alertedSince) {
    MailApp.sendEmail(to, "Estación El Gancho: sin datos desde las " + lastKey.substring(11, 16),
      "La estación no envía datos a la planilla desde " + lastKey + " (hace " + silent + " min).\n\n" +
      "Causas probables: se cortó internet en la casa, la estación está desenchufada, o se apagó el router.\n" +
      "No hace falta hacer nada con los datos: al volver la conexión, la estación recupera sola lo que " +
      "falte desde la consola Davis (hasta 7 días).\n\n" +
      "Recibirás otro correo cuando vuelvan los datos.\n\nPlanilla: " + url);
    props.setProperty("ALERTA_SIN_DATOS_DESDE", lastKey);
    Logger.log("Aviso enviado: sin datos desde " + lastKey);
  } else if (silent < ALERT_AFTER_MINUTES && alertedSince) {
    MailApp.sendEmail(to, "Estación El Gancho: volvieron los datos",
      "La estación volvió a enviar datos. Último registro: " + lastKey + ".\n" +
      "Estuvo sin enviar desde " + alertedSince + ". Los registros de ese tramo se recuperan solos " +
      "desde la consola en los próximos minutos.\n\nPlanilla: " + url);
    props.deleteProperty("ALERTA_SIN_DATOS_DESDE");
    Logger.log("Aviso de vuelta enviado; último registro " + lastKey);
  } else {
    Logger.log("Último registro " + lastKey + " (hace " + silent + " min)" + (alertedSince ? "; aviso ya enviado" : ""));
  }

  revisarClima_(sheet, tz, nowKey, props, to, url);
  revisarSistema_(sheet, tz, nowKey, props, to, url);
}

// ---------------------------------------------------------------------------------------
// Avisos de fallas silenciosas (los revisa revisarEstacion cada 30 min), uno por día cada uno
// ---------------------------------------------------------------------------------------

var REBOOTS_PER_DAY = 3;          // reinicios en 24 h que indican un fallo (bucle de reinicios)
var FROZEN_HOURS = 6;             // temperatura y humedad idénticas este tiempo: sensor trabado
var ISS_LOW_PCT = 80;             // recepción media del transmisor en 3 h
var UPLOAD_DOWN_MINUTES = 120;    // Weather Underground o Windy sin recibir este tiempo
var WU_FAILS_DOWN = 15;           // de ~20 envíos a WU por intervalo de 10 min

/**
 * Arranque tras instalar una actualización OTA ("Actualizacion v1.0.19"): reinicio esperado, no
 * una falla. Con "(arranque N)" la versión nueva se volvió a reiniciar antes de funcionar: sí cuenta.
 */
function esActualizacion_(cause) {
  return /^Actualizaci[oó]n v[\d.]+$/.test(String(cause));
}

function revisarSistema_(sheet, tz, nowKey, props, to, url) {
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return;
  var headers = getHeaders_(sheet);
  var n = Math.min(160, lastRow - 1);
  var col = {
    temp: colIndex_(headers, ["temp ext"]), hum: colIndex_(headers, ["humedad ext"]),
    reset: colIndex_(headers, ["reinicio"]), recept: colIndex_(headers, ["recepci"]),
    wu: colIndex_(headers, ["fallos wu"])
  };
  var nowMin = keyMinutes_(nowKey);
  var today = nowKey.substring(0, 10);
  var recent = [];   // filas de las últimas 24 h: { k, age (min), r }
  sheet.getRange(lastRow - n + 1, 1, n, headers.length).getValues().forEach(function (r) {
    var k = keyOf_(r[0], tz);
    if (!k) return;
    var age = nowMin - keyMinutes_(k);
    if (age >= 0 && age <= 24 * 60) recent.push({ k: k, age: age, r: r });
  });
  if (!recent.length) return;   // sin datos: ya avisa revisarEstacion
  function within(minutes, c) {
    return recent.filter(function (x) { return x.age <= minutes && (c === undefined || x.r[c] !== ""); });
  }

  if (col.reset >= 0) {
    var boots = within(24 * 60, col.reset).filter(function (x) { return !esActualizacion_(x.r[col.reset]); });
    if (boots.length >= REBOOTS_PER_DAY) {
      avisar_(props, to, url, "AVISO_REINICIOS", today, "se reinició " + boots.length + " veces en 24 h",
        "Reinicios: " + boots.map(function (x) { return x.r[col.reset] + " " + x.k.substring(5, 16); }).join(", ") +
        ".\nVarios reinicios seguidos suelen indicar un fallo del programa o de la alimentación del ESP32. " +
        "La pestaña Estado muestra la memoria libre y la causa de cada arranque.");
    }
  }

  if (col.temp >= 0 && col.hum >= 0) {
    var win = within(FROZEN_HOURS * 60, col.temp);
    var t0 = win.length ? win[0].r[col.temp] : "", h0 = win.length ? win[0].r[col.hum] : "";
    if (win.length >= FROZEN_HOURS * 6 - 4 && win.every(function (x) {
      return x.r[col.temp] === t0 && x.r[col.hum] === h0;
    })) {
      avisar_(props, to, url, "AVISO_SENSOR", today, "temperatura y humedad sin cambios hace " + FROZEN_HOURS + " h",
        "La temperatura (" + t0 + " °C) y la humedad (" + h0 + " %) no cambiaron en " + FROZEN_HOURS + " h. " +
        "Puede ser el sensor o el transmisor (ISS) trabado: la consola repite el último valor recibido.");
    }
  }

  if (col.recept >= 0) {
    var rec = within(180, col.recept);
    if (rec.length >= 12) {
      var avg = rec.reduce(function (s, x) { return s + num_(x.r[col.recept]); }, 0) / rec.length;
      if (avg < ISS_LOW_PCT) {
        avisar_(props, to, url, "AVISO_RECEPCION", today, "recepción del transmisor " + Math.round(avg) + " %",
          "En las últimas 3 h la consola recibió en promedio el " + Math.round(avg) + " % de los paquetes del " +
          "transmisor (lo normal es más de 95 %). Causas: batería o panel solar del ISS, antena, algo que tapa la señal.");
      }
    }
  }

  if (col.wu >= 0) {
    var wu = within(UPLOAD_DOWN_MINUTES, col.wu);
    if (wu.length >= 10 && wu.every(function (x) { return num_(x.r[col.wu]) >= WU_FAILS_DOWN; })) {
      avisar_(props, to, url, "AVISO_WU", today, "Weather Underground no recibe datos",
        "En las últimas " + (UPLOAD_DOWN_MINUTES / 60) + " h fallaron casi todos los envíos a Weather Underground " +
        "(la planilla sí recibe). Revisa la clave de la estación en wunderground.com o si WU tiene problemas.");
    }
  }

  var lastOk = props.getProperty("WINDY_ULTIMO_OK");
  if (props.getProperty("WINDY_PASSWORD") && lastOk && within(30).length &&
      nowMin - keyMinutes_(lastOk) > UPLOAD_DOWN_MINUTES) {
    avisar_(props, to, url, "AVISO_WINDY", today, "Windy no recibe datos desde las " + lastOk.substring(11, 16),
      "Windy rechaza o no responde los envíos. Última respuesta: " + props.getProperty("WINDY_ULTIMO") +
      ".\nEjecuta estadoWindy() en el editor para ver el detalle.");
  }
}

// ---------------------------------------------------------------------------------------
// Resumen diario por correo (activador diario a las SUMMARY_HOUR, ver activarAvisos)
// ---------------------------------------------------------------------------------------

var SUMMARY_HOUR = 8;
var EXPECTED_RECORDS = 144;
var WU_TRIES_PER_RECORD = 20;     // un envío cada 30 s en 10 min
var MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
var RUMBOS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"];

function resumenDiario() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var tz = ss.getSpreadsheetTimeZone();
  var nowKey = Utilities.formatDate(new Date(), tz, KEY_FORMAT);
  var r = resumenDe_(ss, tz, shiftKey_(nowKey, -1440).substring(0, 10), nowKey);
  var to = [Session.getEffectiveUser().getEmail()];
  var extra = PropertiesService.getScriptProperties().getProperty("CORREOS_RESUMEN");
  if (extra) to = to.concat(extra.split(",").map(function (s) { return s.trim(); }).filter(String));
  MailApp.sendEmail(to.join(","), r.subject, r.body + "\n\nPlanilla: " + ss.getUrl());
  Logger.log("Resumen enviado: " + r.subject);
}

/** Número con coma decimal */
function fmt_(x, decimals) {
  return Number(x).toFixed(decimals).replace(".", ",");
}

/** Asunto y texto del resumen del día `day` ("yyyy-MM-dd"); `nowKey` delimita la parte de sistema */
function resumenDe_(ss, tz, day, nowKey) {
  var sheet = getMainSheet_(ss);
  var headers = getHeaders_(sheet);
  var lastRow = sheet.getLastRow();
  var n = Math.min(TAIL_ROWS, lastRow - 1);
  var rows = n < 1 ? [] : sheet.getRange(lastRow - n + 1, 1, n, headers.length).getValues().filter(function (r) {
    var k = keyOf_(r[0], tz);
    if (k) r[0] = k;
    return k && k.substring(0, 10) === day;
  });
  var c = function (fragments) { return colIndex_(headers, fragments); };
  var col = {
    t: c(["temp ext"]), hi: c(["temp máx"]), lo: c(["temp mín"]), day: c(["lluvia d"]), month: c(["lluvia mes"]),
    year: c(["lluvia a"]), gust: c(["ráfaga 10m"]), gustDir: c(["dir ráfaga"]), reset: c(["reinicio"]),
    recept: c(["recepci"]), batC: c(["bater", "consola"]), batTx: c(["bater", "transmisor"]), wu: c(["fallos wu"])
  };
  var label = +day.substring(8, 10) + "-" + MESES[+day.substring(5, 7) - 1];
  var problems = [];
  var lines = [];

  // ---- Tiempo ----
  var hi = null, lo = null, gust = null;
  rows.forEach(function (r) {
    var vh = r[col.hi >= 0 && r[col.hi] !== "" ? col.hi : col.t], vl = r[col.lo >= 0 && r[col.lo] !== "" ? col.lo : col.t];
    if (vh !== "" && (!hi || vh > hi.v)) hi = { v: vh, k: r[0] };
    if (vl !== "" && (!lo || vl < lo.v)) lo = { v: vl, k: r[0] };
    if (col.gust >= 0 && r[col.gust] !== "" && (!gust || r[col.gust] > gust.v)) {
      gust = { v: r[col.gust], k: r[0], dir: r[col.gustDir] };
    }
  });
  var last = rows.length ? rows[rows.length - 1] : null;
  var rain = last && col.day >= 0 && last[col.day] !== "" ? num_(last[col.day]) : null;
  lines.push("TIEMPO (" + label + ")");
  if (lo && hi) {
    lines.push("  Mínima  " + fmt_(lo.v, 1) + " °C a las " + lo.k.substring(11, 16) +
      "     Máxima  " + fmt_(hi.v, 1) + " °C a las " + hi.k.substring(11, 16));
  }
  if (rain !== null) {
    lines.push("  Lluvia  " + fmt_(rain, 1) + " mm   (mes " + fmt_(num_(last[col.month]), 1) +
      " · año " + fmt_(num_(last[col.year]), 1) + ")");
  }
  if (gust) {
    var dir = gust.dir !== "" && gust.dir !== undefined ? " del " + RUMBOS[Math.round(num_(gust.dir) / 22.5) % 16] : "";
    lines.push("  Ráfaga  " + fmt_(gust.v, 0) + " km/h" + dir + " a las " + gust.k.substring(11, 16));
  }

  // ---- Estación ----
  lines.push("", "ESTACIÓN");
  var missing = EXPECTED_RECORDS - rows.length;
  lines.push("  Datos       " + rows.length + "/" + EXPECTED_RECORDS + " registros (" +
    Math.round(rows.length / EXPECTED_RECORDS * 100) + " %)");
  if (rows.length < EXPECTED_RECORDS - 4) problems.push("Faltan " + missing + " de " + EXPECTED_RECORDS + " registros del día");

  var starts = col.reset >= 0 ? rows.filter(function (r) { return r[col.reset] !== ""; }) : [];
  var updates = starts.filter(function (r) { return esActualizacion_(r[col.reset]); });
  var boots = starts.filter(function (r) { return !esActualizacion_(r[col.reset]); });
  var bootText = boots.map(function (r) { return r[col.reset] + " " + r[0].substring(11, 16); }).join(", ");
  var watchdog = boots.some(function (r) { return r[col.reset] === "Watchdog"; });
  if (boots.length >= 2 || watchdog) problems.push(boots.length + (boots.length === 1 ? " reinicio (" : " reinicios (") + bootText + ")");

  var st = estadoDe_(ss, tz, day + " 00:00:00", nowKey);
  lines.push("  Firmware    " + (st.fw ? "v" + st.fw : "?") +
    (updates.length ? " · " + updates.length + (updates.length === 1 ? " actualización" : " actualizaciones") : "") +
    " · " + boots.length + " reinicios" +
    (st.memMin !== null ? " · RAM mínima " + fmt_(st.memMin, 0) + " KB" : ""));
  if (!st.reports) problems.push("El ESP32 no mandó su reporte de salud (pestaña Estado)");
  if (st.memMin !== null && st.memMin < 30) problems.push("Memoria libre baja en el ESP32: " + fmt_(st.memMin, 0) + " KB");
  if (st.errDisplay > 0) problems.push(st.errDisplay + " errores de la pantalla");

  var rec = col.recept >= 0 ? rows.filter(function (r) { return r[col.recept] !== ""; }) : [];
  var recAvg = rec.length ? rec.reduce(function (s, r) { return s + num_(r[col.recept]); }, 0) / rec.length : null;
  var txLow = col.batTx >= 0 && rows.some(function (r) { return r[col.batTx] === "Baja"; });
  lines.push("  Transmisor  recepción " + (recAvg !== null ? Math.round(recAvg) + " %" : "?") +
    " · batería " + (txLow ? "BAJA" : "OK"));
  if (recAvg !== null && recAvg < 90) problems.push("Recepción del transmisor " + Math.round(recAvg) + " % (lo normal es más de 95 %)");
  if (txLow) problems.push("Batería del transmisor baja");

  var batC = col.batC >= 0 ? rows.filter(function (r) { return r[col.batC] !== ""; }).map(function (r) { return num_(r[col.batC]); }) : [];
  if (batC.length) {
    var minBat = Math.min.apply(null, batC);
    lines.push("  Consola     " + fmt_(minBat, 2) + " V");
    if (minBat < CONSOLE_BATTERY_V) problems.push("Batería de la consola baja (" + fmt_(minBat, 2) + " V)");
  }

  var sends = ["Planilla " + (missing <= 4 ? "✅" : "⚠️")];
  var wuRows = col.wu >= 0 ? rows.filter(function (r) { return r[col.wu] !== ""; }) : [];
  if (wuRows.length) {
    var wuFails = wuRows.reduce(function (s, r) { return s + num_(r[col.wu]); }, 0);
    var wuTries = wuRows.length * WU_TRIES_PER_RECORD;
    var wuBad = wuFails > wuTries * 0.05;
    sends.push("WU " + (wuBad ? "⚠️" : "✅") + (wuFails ? " (" + wuFails + " fallos de " + wuTries + ")" : ""));
    if (wuBad) problems.push("Weather Underground: " + wuFails + " envíos fallidos de " + wuTries);
  }
  var props = PropertiesService.getScriptProperties();
  if (props.getProperty("WINDY_PASSWORD")) {
    var wc = JSON.parse(props.getProperty("WINDY_CONTEO") || "{}")[day] || [0, 0];
    var windyBad = wc[0] === 0 || wc[1] > (wc[0] + wc[1]) * 0.2;
    sends.push("Windy " + (windyBad ? "⚠️" : "✅") + (wc[1] ? " (" + wc[1] + " fallos de " + (wc[0] + wc[1]) + ")" : ""));
    if (windyBad) problems.push("Windy: " + wc[0] + " envíos aceptados y " + wc[1] + " fallidos");
  }
  lines.push("  Envíos      " + sends.join("  "));

  if (problems.length) {
    lines.push("", "⚠️ PROBLEMAS");
    problems.forEach(function (p) { lines.push("  - " + p); });
  }

  var subject = "El Gancho " + label;
  if (!rows.length) {
    subject += " · ⚠️ sin datos";
  } else {
    if (lo && hi) subject += " · " + fmt_(lo.v, 1) + " / " + fmt_(hi.v, 1) + " °C";
    if (rain !== null) subject += " · " + fmt_(rain, 1) + " mm";
    subject += problems.length ? " · ⚠️ " + problems.length + (problems.length === 1 ? " problema" : " problemas")
      : " · ✅ todo bien";
  }
  return { subject: subject, body: lines.join("\n"), problems: problems };
}

/** Reportes de la pestaña Estado entre `fromKey` y `toKey`: firmware, RAM mínima, errores de pantalla */
function estadoDe_(ss, tz, fromKey, toKey) {
  var out = { reports: 0, fw: null, memMin: null, errDisplay: 0 };
  var sheet = ss.getSheetByName(STATUS_SHEET_NAME);
  if (!sheet || sheet.getLastRow() < 2) return out;
  var headers = getHeaders_(sheet);
  var cFw = headers.indexOf("Firmware"), cMem = headers.indexOf("RAM mínima (KB)"), cDisp = headers.indexOf("Errores pantalla");
  var n = Math.min(20, sheet.getLastRow() - 1);
  sheet.getRange(sheet.getLastRow() - n + 1, 1, n, headers.length).getValues().forEach(function (r) {
    var k = keyOf_(r[0], tz);
    if (!k || k < fromKey || k > toKey) return;
    out.reports++;
    if (cFw >= 0 && r[cFw] !== "") out.fw = r[cFw];
    if (cMem >= 0 && r[cMem] !== "" && (out.memMin === null || r[cMem] < out.memMin)) out.memMin = num_(r[cMem]);
    if (cDisp >= 0) out.errDisplay += num_(r[cDisp]);
  });
  return out;
}

// ---------------------------------------------------------------------------------------
// Avisos del tiempo (los revisa revisarEstacion cada 30 min). Umbrales editables:
// ---------------------------------------------------------------------------------------

var FROST_WARN_C = 2;          // riesgo de helada: temperatura igual o menor
var FROST_C = 0;               // helada
var GUST_KMH = 50;             // ráfaga fuerte
var RAIN_RATE_MMH = 10;        // lluvia intensa (intensidad)
var RAIN_DAY_MM = 20;          // lluvia intensa (acumulado del día)
var CONSOLE_BATTERY_V = 3.5;   // batería de la consola baja
var WEATHER_LOOKBACK_MINUTES = 180;   // solo filas recientes (no avisar por registros recuperados viejos)

/** Manda el aviso solo si no se mandó ya en este período (noche, día o semana) */
function avisar_(props, to, url, prop, period, subject, body) {
  if (props.getProperty(prop) === period) return;
  MailApp.sendEmail(to, "Estación El Gancho: " + subject, body + "\n\nPlanilla: " + url);
  props.setProperty(prop, period);
  Logger.log("Aviso enviado: " + subject);
}

/**
 * Un correo por evento: helada / riesgo de helada una vez por noche, ráfaga y lluvia una vez por
 * día, baterías una vez por semana mientras sigan bajas. Lo ya avisado se guarda en propiedades.
 */
function revisarClima_(sheet, tz, nowKey, props, to, url) {
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return;
  var headers = getHeaders_(sheet);
  var n = Math.min(24, lastRow - 1);
  var rows = sheet.getRange(lastRow - n + 1, 1, n, headers.length).getValues();
  var col = {
    temp: colIndex_(headers, ["temp ext"]), gust: colIndex_(headers, ["ráfaga 10m"]),
    gustDir: colIndex_(headers, ["dir ráfaga"]), rate: colIndex_(headers, ["tasa lluvia"]),
    day: colIndex_(headers, ["lluvia d"]), batC: colIndex_(headers, ["bater", "consola"]),
    batTx: colIndex_(headers, ["bater", "transmisor"])
  };
  var since = keyMinutes_(nowKey) - WEATHER_LOOKBACK_MINUTES;
  var minT = null, maxGust = null, maxRate = null, maxDay = null, batC = null, txLow = null;
  rows.forEach(function (r) {
    var k = keyOf_(r[0], tz);
    if (!k || keyMinutes_(k) < since) return;
    var t = r[col.temp], g = r[col.gust], rate = r[col.rate], day = r[col.day];
    if (t !== "" && (!minT || t < minT.v)) minT = { v: t, k: k };
    if (g !== "" && (!maxGust || g > maxGust.v)) maxGust = { v: g, k: k, dir: r[col.gustDir] };
    if (rate !== "" && (!maxRate || rate > maxRate.v)) maxRate = { v: rate, k: k };
    if (day !== "" && (!maxDay || day > maxDay.v)) maxDay = { v: day, k: k };
    if (col.batC >= 0 && r[col.batC] !== "") batC = { v: r[col.batC], k: k };
    if (col.batTx >= 0 && r[col.batTx] !== "") txLow = { low: r[col.batTx] === "Baja", k: k };
  });

  function once(prop, period, subject, body) {
    avisar_(props, to, url, prop, period, subject, body);
  }
  function hm(k) { return k.substring(11, 16); }
  // La noche de una helada se identifica por la fecha de 12 h antes (03:00 del 13 -> noche del 12)
  function night(k) { return "noche " + shiftKey_(k, -720).substring(0, 10); }
  function week(k) { return "semana " + shiftKey_(k, -((Math.floor(keyMinutes_(k) / 1440) + 3) % 7) * 1440).substring(0, 10); }

  if (minT && minT.v <= FROST_C) {
    props.setProperty("AVISO_RIESGO_HELADA", night(minT.k));
    once("AVISO_HELADA", night(minT.k), "helada (" + minT.v + " °C a las " + hm(minT.k) + ")",
      "La temperatura bajó a " + minT.v + " °C a las " + hm(minT.k) + " (" + minT.k.substring(0, 10) + ").");
  } else if (minT && minT.v <= FROST_WARN_C) {
    once("AVISO_RIESGO_HELADA", night(minT.k), "riesgo de helada (" + minT.v + " °C a las " + hm(minT.k) + ")",
      "La temperatura bajó a " + minT.v + " °C a las " + hm(minT.k) + ". Si sigue bajando puede helar esta noche.");
  }
  if (maxGust && maxGust.v >= GUST_KMH) {
    once("AVISO_RAFAGA", maxGust.k.substring(0, 10), "ráfagas de " + maxGust.v + " km/h",
      "Ráfaga de " + maxGust.v + " km/h a las " + hm(maxGust.k) +
      (maxGust.dir !== "" ? " desde " + maxGust.dir + "°" : "") + ".");
  }
  if ((maxRate && maxRate.v >= RAIN_RATE_MMH) || (maxDay && maxDay.v >= RAIN_DAY_MM)) {
    var ref = maxRate && maxRate.v >= RAIN_RATE_MMH ? maxRate.k : maxDay.k;
    once("AVISO_LLUVIA", ref.substring(0, 10), "lluvia intensa (" + (maxDay ? maxDay.v : 0) + " mm hoy)",
      "Intensidad máxima " + (maxRate ? maxRate.v : 0) + " mm/h; acumulado del día " + (maxDay ? maxDay.v : 0) + " mm.");
  }
  if (batC && batC.v < CONSOLE_BATTERY_V) {
    once("AVISO_BATERIA_CONSOLA", week(batC.k), "batería de la consola baja (" + batC.v + " V)",
      "La batería de la consola Davis marca " + batC.v + " V. Conviene cambiar las pilas en la próxima visita " +
      "(si se corta la luz, la consola deja de guardar datos).");
  }
  if (txLow && txLow.low) {
    once("AVISO_BATERIA_TX", week(txLow.k), "batería del transmisor baja",
      "La consola informa batería baja en el transmisor (ISS). Conviene revisarla en la próxima visita.");
  }
}

/**
 * Función para ordenar manualmente la hoja por Fecha / Hora ascendente
 */
function ordenarPorFecha() {
  var sheet = getMainSheet_(SpreadsheetApp.getActiveSpreadsheet());
  var lastRow = sheet.getLastRow();
  if (lastRow > 2) {
    sheet.getRange(2, 1, lastRow - 1, sheet.getLastColumn()).sort({ column: 1, ascending: true });
  }
}

/**
 * Rellena el hueco del 08-oct-2026 con los registros de la pestaña "Consola" (archivo de la
 * consola Davis, guardado con el reloj de la consola, que iba ~3 h atrasado).
 *
 * 1. Prueba desfases de -12 h a +12 h cada 10 min y elige el que mejor hace calzar temperatura,
 *    humedad y presión de la consola con las filas reales de la hoja principal.
 * 2. Si el calce no es bueno, se detiene sin tocar nada.
 * 3. Inserta solo las fechas que faltan en la hoja principal (no reemplaza datos existentes).
 * Ver el resultado en Ver > Registros de ejecución.
 */
function importarConsola() {
  conBloqueo_(importarConsola_);
}

function importarConsola_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var tz = ss.getSpreadsheetTimeZone();
  moverFilasConsola_(ss);
  var main = getMainSheet_(ss);
  var cons = ss.getSheetByName(CONSOLE_SHEET_NAME);
  if (!cons || cons.getLastRow() < 2) {
    Logger.log("No hay datos en la pestaña '" + CONSOLE_SHEET_NAME + "' todavía.");
    return;
  }

  var mh = getHeaders_(main);
  var ch = getHeaders_(cons);
  var mVals = main.getRange(2, 1, main.getLastRow() - 1, mh.length).getValues();
  var cVals = cons.getRange(2, 1, cons.getLastRow() - 1, ch.length).getValues();

  var mT = colIndex_(mh, ["temp ext"]), mH = colIndex_(mh, ["humedad ext"]), mP = colIndex_(mh, ["presi"]);
  var cT = colIndex_(ch, ["temp ext"]), cH = colIndex_(ch, ["humedad ext"]), cP = colIndex_(ch, ["presi"]);

  var mainByKey = {};
  mVals.forEach(function (r) {
    var k = keyOf_(r[0], tz);
    if (k && Number(k.substring(0, 4)) >= MIN_VALID_YEAR) mainByKey[k] = r;
  });
  var consRows = [];
  cVals.forEach(function (r) {
    var k = keyOf_(r[0], tz);
    if (k && r[cT] !== "") consRows.push({ key: k, row: r });
  });
  consRows.sort(function (a, b) { return a.key < b.key ? -1 : a.key > b.key ? 1 : 0; });

  var cands = [];
  for (var off = -720; off <= 720; off += 10) {
    var n = 0, err = 0;
    for (var i = 0; i < consRows.length; i++) {
      var m = mainByKey[shiftKey_(consRows[i].key, off)];
      if (!m || m[mT] === "") continue;
      var c = consRows[i].row;
      err += Math.abs(num_(m[mT]) - num_(c[cT])) + Math.abs(num_(m[mH]) - num_(c[cH])) / 5 +
        Math.abs(num_(m[mP]) - num_(c[cP]));
      n++;
    }
    if (n >= 12) cands.push({ off: off, score: err / n, n: n });
  }
  if (!cands.length) {
    Logger.log("No hay suficientes horas en común entre la consola y la hoja para calcular el desfase.");
    return;
  }
  cands.sort(function (a, b) { return a.score - b.score; });
  var best = cands[0];
  // Desfases vecinos (±50 min) calzan casi igual porque el tiempo cambia lento: se compara
  // con el mejor desfase claramente distinto para descartar ambigüedad
  var second = null;
  for (var s = 1; s < cands.length; s++) {
    if (Math.abs(cands[s].off - best.off) >= 60) { second = cands[s]; break; }
  }
  Logger.log("Mejor desfase: " + (best.off >= 0 ? "+" : "") + best.off + " min (error medio " +
    best.score.toFixed(2) + " en " + best.n + " filas comunes)" +
    (second ? "; siguiente: " + second.off + " min (error " + second.score.toFixed(2) + ")" : ""));
  if (best.score > 1.0 || (second && second.score < best.score * 1.5)) {
    Logger.log("El calce no es concluyente: no se modificó la hoja.");
    return;
  }

  // Copiar por nombre de columna; acumulados de lluvia y tendencia se recalculan al insertar
  var map = mh.map(function (h) { return ch.indexOf(h); });
  var cDay = colIndex_(mh, ["lluvia d"]), cStorm = colIndex_(mh, ["tormenta"]);
  var cMonth = colIndex_(mh, ["lluvia mes"]), cYear = colIndex_(mh, ["lluvia a"]);
  var cTrend = colIndex_(mh, ["tendencia"]);
  var inserted = 0, first = null, last = null;
  for (var j = 0; j < consRows.length; j++) {
    var key = shiftKey_(consRows[j].key, best.off);
    if (mainByKey[key]) continue;
    var row = map.map(function (ci) { return ci >= 0 ? consRows[j].row[ci] : ""; });
    row[0] = key;
    [cDay, cStorm, cMonth, cYear, cTrend].forEach(function (ci) { if (ci >= 0) row[ci] = ""; });
    upsertRow_(main, mh, row, key, true);
    mainByKey[key] = row;
    inserted++;
    first = first || key;
    last = key;
  }
  Logger.log("Filas insertadas desde la consola: " + inserted + (inserted ? " (" + first + " a " + last + ")" : ""));
}

/**
 * Limpieza única de la hoja principal:
 * - borra filas con fecha inválida (antes de 2025, p. ej. 1999/2000 de un ESP32 sin hora) o no reconocible
 * - borra duplicados de fecha, conservando la fila más completa
 * - vacía "Muestras Viento" y "Tx Viento" donde repetían la velocidad del viento (error del mapeo anterior)
 */
function repararHoja() {
  conBloqueo_(repararHoja_);
}

function repararHoja_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  moverFilasConsola_(ss);  // antes de borrar fechas no reconocibles
  var tz = ss.getSpreadsheetTimeZone();
  var sheet = getMainSheet_(ss);
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return;
  var headers = getHeaders_(sheet);
  var values = sheet.getRange(2, 1, lastRow - 1, headers.length).getValues();

  var toDelete = [];
  var byKey = {};
  for (var i = 0; i < values.length; i++) {
    var k = keyOf_(values[i][0], tz);
    if (!k || Number(k.substring(0, 4)) < MIN_VALID_YEAR) {
      toDelete.push(i + 2);
      continue;
    }
    var score = values[i].filter(function (x) { return x !== "" && x !== null; }).length;
    if (byKey[k] === undefined) {
      byKey[k] = { row: i + 2, score: score };
    } else if (score > byKey[k].score) {
      toDelete.push(byKey[k].row);
      byKey[k] = { row: i + 2, score: score };
    } else {
      toDelete.push(i + 2);
    }
  }

  // Muestras/Tx iguales a Viento Actual = valor mal mapeado (las filas se procesan antes de borrar)
  var cWind = colIndex_(headers, ["viento actual"]);
  var cSamp = colIndex_(headers, ["muestras"]);
  var cTx = colIndex_(headers, ["tx", "viento"]);
  var blanked = 0;
  if (cWind >= 0 && cSamp >= 0 && cTx >= 0) {
    var samp = sheet.getRange(2, cSamp + 1, values.length, 1).getValues();
    var tx = sheet.getRange(2, cTx + 1, values.length, 1).getValues();
    for (var r = 0; r < values.length; r++) {
      var w = values[r][cWind];
      if (w !== "" && samp[r][0] === w && tx[r][0] === w) {
        samp[r][0] = "";
        tx[r][0] = "";
        blanked++;
      }
    }
    sheet.getRange(2, cSamp + 1, values.length, 1).setValues(samp);
    sheet.getRange(2, cTx + 1, values.length, 1).setValues(tx);
  }

  toDelete.sort(function (a, b) { return b - a; });
  for (var d = 0; d < toDelete.length; d++) {
    sheet.deleteRow(toDelete[d]);
  }
  ordenarPorFecha();
  var fixedInt = recalcularLluviaInt_(sheet, headers, tz);
  Logger.log("Filas borradas (fecha inválida o duplicada): " + toDelete.length +
    "; filas con Muestras/Tx vaciados: " + blanked + "; Lluvia Int corregida en " + fixedInt + " filas");
}

/**
 * Las filas que llegaron con fecha "CONSOLA ..." mientras corría la versión anterior del script
 * quedaron al final de la hoja principal: se pasan a la pestaña "Consola" sin el prefijo.
 */
function moverFilasConsola_(ss) {
  var main = getMainSheet_(ss);
  var lastRow = main.getLastRow();
  if (lastRow < 2) return 0;
  var mh = getHeaders_(main);
  var vals = main.getRange(2, 1, lastRow - 1, mh.length).getValues();
  var found = [];
  for (var i = 0; i < vals.length; i++) {
    if (typeof vals[i][0] === "string" && /^CONSOLA\s+/.test(vals[i][0])) found.push(i);
  }
  if (!found.length) return 0;

  var cons = getConsoleSheet_(ss);
  var ch = getHeaders_(cons);
  var tz = ss.getSpreadsheetTimeZone();
  var existing = {};
  readKeys_(cons, tz).forEach(function (k) { if (k) existing[k] = true; });
  var map = ch.map(function (h) { return mh.indexOf(h); });
  var out = [];
  found.forEach(function (i) {
    var key = normalizeKey_(vals[i][0].replace(/^CONSOLA\s+/, ""));
    if (!key || existing[key]) return;
    existing[key] = true;
    var row = map.map(function (mi) { return mi >= 0 ? vals[i][mi] : ""; });
    row[0] = key;
    out.push(row);
  });
  if (out.length) {
    cons.getRange(cons.getLastRow() + 1, 1, out.length, ch.length).setValues(out);
    cons.getRange(2, 1, cons.getLastRow() - 1, ch.length).sort(1);
  }
  // Borrar de abajo hacia arriba, por bloques contiguos
  for (var j = found.length - 1; j >= 0;) {
    var end = found[j], start = end;
    while (j > 0 && found[j - 1] === start - 1) { j--; start--; }
    main.deleteRows(start + 2, end - start + 1);
    j--;
  }
  Logger.log("Filas 'CONSOLA' movidas de la hoja principal a la pestaña '" + CONSOLE_SHEET_NAME + "': " +
    found.length + " (" + out.length + " nuevas)");
  return found.length;
}

/** Recalcula "Lluvia Int" desde la lluvia del día donde la fila anterior está 10 min antes */
function recalcularLluviaInt_(sheet, headers, tz) {
  var cInt = colIndex_(headers, ["lluvia int"]);
  var cDay = colIndex_(headers, ["lluvia d"]);
  var lastRow = sheet.getLastRow();
  if (cInt < 0 || cDay < 0 || lastRow < 3) return 0;
  var keys = readKeys_(sheet, tz);
  var days = sheet.getRange(2, cDay + 1, lastRow - 1, 1).getValues();
  var ints = sheet.getRange(2, cInt + 1, lastRow - 1, 1).getValues();
  var changed = 0;
  for (var i = 1; i < keys.length; i++) {
    if (!keys[i]) continue;
    var v = intervalFromDay_(keys[i], days[i][0], keys[i - 1], days[i - 1][0]);
    if (v === "" || (ints[i][0] !== "" && num_(ints[i][0]) === v)) continue;
    ints[i][0] = v;
    changed++;
  }
  if (changed) sheet.getRange(2, cInt + 1, lastRow - 1, 1).setValues(ints);
  return changed;
}

/**
 * Una sola vez: importarConsola y repararHoja corrieron al mismo tiempo el 11-oct-2026 y dejaron
 * mal los acumulados de lluvia de las filas importadas del 08-oct y perdieron 3 filas.
 * Borra las filas importadas (10:40 a 20:30, sin batería), las vuelve a importar, repone la fila
 * de 10-09 05:00 (no está en el archivo de la consola) y recalcula "Lluvia Int".
 */
function repararImportacion08Oct() {
  conBloqueo_(repararImportacion08Oct_);
}

function repararImportacion08Oct_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var tz = ss.getSpreadsheetTimeZone();
  var main = getMainSheet_(ss);
  var mh = getHeaders_(main);
  var cBat = colIndex_(mh, ["bater", "consola"]);
  var keys = readKeys_(main, tz);
  var bats = main.getRange(2, cBat + 1, keys.length, 1).getValues();
  var removed = 0;
  for (var i = keys.length - 1; i >= 0; i--) {
    if (keys[i] >= "2026-10-08 10:40:00" && keys[i] <= "2026-10-08 20:30:00" && bats[i][0] === "") {
      main.deleteRow(i + 2);
      removed++;
    }
  }
  Logger.log("Filas importadas borradas: " + removed);

  importarConsola_();

  var lost = "2026-10-09 05:00:00,7.2,7.2,7.2,93,6.1,6.4,0,263,0,0,0,0,270,7.2,7.2,7.2,1016.4,Bajando Lento," +
    "0,0,0,7.9,68.6,422.4,0.077,0,15.7,52,5.9,14.7,9.7,1.2222,,,100,10,4.24,OK,,";
  var names = ["Fecha / Hora", "Temp Ext (°C)", "Temp Máx 10m (°C)", "Temp Mín 10m (°C)", "Humedad Ext (%)",
    "Punto Rocío (°C)", "Bulbo Húmedo (°C)", "Viento Actual (km/h)", "Dirección Viento (°)",
    "Viento Sost 2m (km/h)", "Viento Med 10m (km/h)", "Recorrido Viento (km)", "Ráfaga 10m (km/h)",
    "Dir Ráfaga (°)", "Viento Frío (°C)", "Índice Calor (°C)", "Índice THW (°C)", "Presión (hPa)",
    "Tendencia Baro", "Lluvia Int (mm)", "Tasa Lluvia (mm/h)", "Lluvia Día (mm)", "Lluvia Tormenta (mm)",
    "Lluvia Mes (mm)", "Lluvia Año (mm)", "Grados Día Calef (°C-d)", "Grados Día Enfr (°C-d)", "Temp Int (°C)",
    "Humedad Int (%)", "Punto Rocío Int (°C)", "Índice Calor Int (°C)", "EMC Int (%)",
    "Densidad Aire Int (kg/m³)", "Muestras Viento", "Tx Viento", "Recepción ISS (%)",
    "Intervalo Archivo (min)", "Batería Consola (V)", "Batería Transmisor", "Radiación Solar (W/m²)", "Índice UV"];
  var vals = lost.split(",");
  var byName = {};
  names.forEach(function (n, j) {
    var v = vals[j];
    byName[n] = v === "" || isNaN(Number(v)) ? v : Number(v);
  });
  var lostKey = vals[0];
  if (readKeys_(main, tz).indexOf(lostKey) < 0) {
    upsertRow_(main, mh, mh.map(function (h) { return byName[h] !== undefined ? byName[h] : ""; }), lostKey, false);
    Logger.log("Fila repuesta: " + lostKey);
  }

  var fixed = recalcularLluviaInt_(main, mh, tz);
  Logger.log("Lluvia Int corregida en " + fixed + " filas. Listo.");
}

/**
 * Las funciones manuales tardan minutos: con el bloqueo, una segunda ejecución (u otra función)
 * no corre al mismo tiempo sobre la hoja. Mientras tanto la estación espera o reintenta.
 */
function conBloqueo_(fn) {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) {
    Logger.log("Otra función está modificando la hoja. Espera a que termine y vuelve a ejecutar.");
    return;
  }
  try {
    fn();
  } finally {
    lock.releaseLock();
  }
}

/** Alias de la versión anterior */
function eliminarDuplicados() {
  repararHoja();
}

/**
 * Rellena automáticamente las celdas vacías de "Viento Med 10m (km/h)"
 * tomando el valor de "Viento Actual (km/h)" (que es el promedio de 10m de la consola).
 * No altera las celdas de batería para permitir identificar registros de archivo.
 */
function rellenarVientoMedio() {
  var sheet = getMainSheet_(SpreadsheetApp.getActiveSpreadsheet());
  var lastRow = sheet.getLastRow();
  var numCols = sheet.getLastColumn();
  if (lastRow <= 1) return;

  var headers = sheet.getRange(1, 1, 1, numCols).getValues()[0];
  var colVientoActual = -1;
  var colVientoMed10 = -1;

  for (var c = 0; c < headers.length; c++) {
    var h = headers[c].toString().toLowerCase();
    if (h.indexOf("viento actual") !== -1 || (h.indexOf("viento") !== -1 && h.indexOf("actual") !== -1)) {
      colVientoActual = c + 1;
    } else if (h.indexOf("viento med") !== -1 || (h.indexOf("10m") !== -1 && h.indexOf("med") !== -1)) {
      colVientoMed10 = c + 1;
    }
  }

  if (colVientoActual !== -1 && colVientoMed10 !== -1) {
    var rangeActual = sheet.getRange(2, colVientoActual, lastRow - 1, 1).getValues();
    var rangeMed10 = sheet.getRange(2, colVientoMed10, lastRow - 1, 1).getValues();
    var modWind = false;
    for (var i = 0; i < rangeMed10.length; i++) {
      if (rangeMed10[i][0] === "" || rangeMed10[i][0] === null || rangeMed10[i][0] === undefined) {
        rangeMed10[i][0] = rangeActual[i][0];
        modWind = true;
      }
    }
    if (modWind) {
      sheet.getRange(2, colVientoMed10, lastRow - 1, 1).setValues(rangeMed10);
    }
  }
}
