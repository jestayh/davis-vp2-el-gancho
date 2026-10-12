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
 *
 * Funciones para ejecutar a mano desde el editor:
 * - activarAvisos(): correo si la estación pasa 60 min sin enviar datos (y cuando vuelven), y
 *   avisos del tiempo: helada / riesgo de helada, ráfagas fuertes, lluvia intensa y baterías bajas
 *   (umbrales en FROST_C, FROST_WARN_C, GUST_KMH, RAIN_RATE_MMH, RAIN_DAY_MM, CONSOLE_BATTERY_V).
 * - importarConsola(): rellena el hueco del 08-oct-2026 con la pestaña "Consola".
 * - repararHoja(): borra filas con fecha inválida y duplicados, y vacía Muestras/Tx erróneos.
 * - ordenarPorFecha(): reordena toda la hoja.
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
  ["Recuperaciones consola", "recoveries"]
];
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
    }

    var headers = getHeaders_(sheet);
    var row = buildRow_(headers, data, key);
    upsertRow_(sheet, headers, row, key, !toConsole);

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
  var headers = getHeaders_(sheet);
  var row = headers.map(function (h) {
    for (var i = 0; i < STATUS_COLUMNS.length; i++) {
      if (STATUS_COLUMNS[i][0] === h) {
        return i === 0 ? key : v_(data[STATUS_COLUMNS[i][1]], "");
      }
    }
    return "";
  });
  sheet.appendRow(row);
  return "SUCCESS";
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
    "Reinicio"
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
  Logger.log("Avisos activados: se revisa cada " + ALERT_CHECK_EVERY_MINUTES + " min; correo a " +
             Session.getEffectiveUser().getEmail() + " tras " + ALERT_AFTER_MINUTES + " min sin datos.");
  revisarEstacion();
}

function desactivarAvisos() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === "revisarEstacion") ScriptApp.deleteTrigger(t);
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
    if (props.getProperty(prop) === period) return;
    MailApp.sendEmail(to, "Estación El Gancho: " + subject, body + "\n\nPlanilla: " + url);
    props.setProperty(prop, period);
    Logger.log("Aviso enviado: " + subject);
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
