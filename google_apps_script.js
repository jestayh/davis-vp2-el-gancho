/**
 * Davis WeatherStation "El Gancho" - Webhook Inteligente con Mapeo Dinámico
 * 
 * Características:
 * 1. Mapeo Dinámico por Encabezado: Lee los títulos de la Fila 1 y coloca cada dato
 *    exactamente debajo de su columna correspondiente, sin importar el orden.
 * 2. Ordenamiento cronológico automático ascendente (más antiguo a más reciente).
 * 3. Auto-archivado anual automático en Google Drive el 1 de enero.
 */

function doPost(e) {
  try {
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sheet = ss.getActiveSheet();
    var data = JSON.parse(e.postData.contents);
    
    // Año de la medición entrante
    var dateObj = data.timestamp ? new Date(data.timestamp.replace(" ", "T")) : new Date();
    var incomingYear = dateObj.getFullYear();
    
    var props = PropertiesService.getScriptProperties();
    var lastRecordedYear = props.getProperty("LAST_RECORDED_YEAR");
    
    // 1. Si la hoja no tiene encabezados, crearlos
    var numCols = sheet.getLastColumn();
    if (numCols === 0 || sheet.getLastRow() === 0) {
      createHeaders(sheet);
      numCols = sheet.getLastColumn();
      props.setProperty("LAST_RECORDED_YEAR", incomingYear.toString());
      lastRecordedYear = incomingYear.toString();
    }
    
    // 2. Detección de Año Nuevo
    if (lastRecordedYear && incomingYear > parseInt(lastRecordedYear)) {
      archiveCurrentYear(ss, lastRecordedYear);
      props.setProperty("LAST_RECORDED_YEAR", incomingYear.toString());
    }
    
    // 3. MAPEO DINÁMICO: Leer los títulos de la Fila 1 y asignar el dato exacto
    var headers = sheet.getRange(1, 1, 1, numCols).getValues()[0];
    var row = [];
    
    for (var i = 0; i < headers.length; i++) {
      var h = headers[i].toString().toLowerCase().trim();
      var val = "";
      
      if (h.indexOf("fecha") !== -1 || h.indexOf("hora") !== -1) {
        val = data.timestamp || "";
      } else if (h.indexOf("temp max") !== -1 || h.indexOf("temp máx") !== -1 || h.indexOf("hi temp") !== -1) {
        val = data.temp_out_hi_c !== undefined && data.temp_out_hi_c !== null ? data.temp_out_hi_c : "";
      } else if (h.indexOf("temp min") !== -1 || h.indexOf("temp mín") !== -1 || h.indexOf("low temp") !== -1) {
        val = data.temp_out_low_c !== undefined && data.temp_out_low_c !== null ? data.temp_out_low_c : "";
      } else if (h.indexOf("frio") !== -1 || h.indexOf("frío") !== -1 || h.indexOf("chill") !== -1) {
        val = data.wind_chill_c !== undefined && data.wind_chill_c !== null ? data.wind_chill_c : "";
      } else if (h.indexOf("calor int") !== -1 || h.indexOf("in heat") !== -1) {
        val = data.in_heat_c !== undefined && data.in_heat_c !== null ? data.in_heat_c : "";
      } else if (h.indexOf("calor") !== -1 || h.indexOf("heat") !== -1) {
        val = data.heat_index_c !== undefined && data.heat_index_c !== null ? data.heat_index_c : "";
      } else if (h.indexOf("thw") !== -1) {
        val = data.thw_index_c !== undefined && data.thw_index_c !== null ? data.thw_index_c : "";
      } else if (h.indexOf("rocio int") !== -1 || h.indexOf("rocío int") !== -1 || h.indexOf("in dew") !== -1) {
        val = data.in_dew_c !== undefined && data.in_dew_c !== null ? data.in_dew_c : "";
      } else if (h.indexOf("rocio") !== -1 || h.indexOf("rocío") !== -1 || h.indexOf("dew") !== -1) {
        val = data.dewpoint_c !== undefined && data.dewpoint_c !== null ? data.dewpoint_c : "";
      } else if (h.indexOf("bulbo") !== -1 || h.indexOf("wet") !== -1) {
        val = data.wet_bulb_c !== undefined && data.wet_bulb_c !== null ? data.wet_bulb_c : "";
      } else if (h.indexOf("temp ext") !== -1 || (h.indexOf("temp") !== -1 && h.indexOf("ext") !== -1)) {
        val = data.temp_out_c !== undefined && data.temp_out_c !== null ? data.temp_out_c : "";
      } else if (h.indexOf("humedad ext") !== -1 || (h.indexOf("hum") !== -1 && h.indexOf("ext") !== -1)) {
        val = data.humidity_out !== undefined && data.humidity_out !== null ? data.humidity_out : "";
      } else if (h.indexOf("viento sost") !== -1 || h.indexOf("2m") !== -1) {
        val = data.windspdkmh_avg2m !== undefined && data.windspdkmh_avg2m !== null ? data.windspdkmh_avg2m : "";
      } else if (h.indexOf("viento med") !== -1 || h.indexOf("10m avg") !== -1 || (h.indexOf("10m") !== -1 && h.indexOf("med") !== -1)) {
        var w10 = (data.wind_speed_10min_avg_kmh !== undefined && data.wind_speed_10min_avg_kmh !== null && data.wind_speed_10min_avg_kmh !== "") ? data.wind_speed_10min_avg_kmh : data.wind_speed_kmh;
        val = w10 !== undefined && w10 !== null ? w10 : "";
      } else if (h.indexOf("recorrido") !== -1 || h.indexOf("wind run") !== -1) {
        val = data.wind_run_km !== undefined && data.wind_run_km !== null ? data.wind_run_km : 0.0;
      } else if (h.indexOf("dir") !== -1 && (h.indexOf("raf") !== -1 || h.indexOf("ráf") !== -1)) {
        val = data.windgustdir_10m !== undefined && data.windgustdir_10m !== null ? data.windgustdir_10m : "";
      } else if (h.indexOf("rafaga") !== -1 || h.indexOf("ráfaga") !== -1 || h.indexOf("gust") !== -1) {
        val = data.windgustkmh_10m !== undefined && data.windgustkmh_10m !== null ? data.windgustkmh_10m : "";
      } else if (h.indexOf("dir") !== -1 && h.indexOf("viento") !== -1) {
        val = data.wind_direction_deg !== undefined && data.wind_direction_deg !== null ? data.wind_direction_deg : "";
      } else if (h.indexOf("viento actual") !== -1 || h.indexOf("viento") !== -1 || h.indexOf("wind") !== -1) {
        val = data.wind_speed_kmh !== undefined && data.wind_speed_kmh !== null ? data.wind_speed_kmh : "";
      } else if (h.indexOf("tendencia") !== -1) {
        val = data.barometer_trend || "Estable";
      } else if (h.indexOf("presion") !== -1 || h.indexOf("presión") !== -1 || h.indexOf("baro") !== -1) {
        val = data.pressure_hpa !== undefined && data.pressure_hpa !== null ? data.pressure_hpa : "";
      } else if (h.indexOf("lluvia int") !== -1 || h.indexOf("rain int") !== -1) {
        val = data.rain_interval_mm !== undefined && data.rain_interval_mm !== null ? data.rain_interval_mm : 0.0;
      } else if (h.indexOf("tasa") !== -1 && h.indexOf("lluvia") !== -1) {
        val = data.rain_rate_mm_per_hr !== undefined && data.rain_rate_mm_per_hr !== null ? data.rain_rate_mm_per_hr : 0.0;
      } else if (h.indexOf("tormenta") !== -1) {
        val = data.storm_rain_mm !== undefined && data.storm_rain_mm !== null ? data.storm_rain_mm : 0.0;
      } else if (h.indexOf("lluvia") !== -1 && (h.indexOf("mes") !== -1 || h.indexOf("month") !== -1)) {
        val = data.rain_month_mm !== undefined && data.rain_month_mm !== null ? data.rain_month_mm : 0.0;
      } else if (h.indexOf("lluvia") !== -1 && (h.indexOf("ano") !== -1 || h.indexOf("año") !== -1 || h.indexOf("year") !== -1)) {
        val = data.rain_year_mm !== undefined && data.rain_year_mm !== null ? data.rain_year_mm : 0.0;
      } else if (h.indexOf("lluvia") !== -1 && (h.indexOf("dia") !== -1 || h.indexOf("día") !== -1 || h.indexOf("day") !== -1)) {
        val = data.rain_day_mm !== undefined && data.rain_day_mm !== null ? data.rain_day_mm : 0.0;
      } else if (h.indexOf("calef") !== -1 || h.indexOf("heat d-d") !== -1) {
        val = data.heat_dd !== undefined && data.heat_dd !== null ? data.heat_dd : 0.0;
      } else if (h.indexOf("enfr") !== -1 || h.indexOf("cool d-d") !== -1) {
        val = data.cool_dd !== undefined && data.cool_dd !== null ? data.cool_dd : 0.0;
      } else if (h.indexOf("temp int") !== -1 || (h.indexOf("temp") !== -1 && h.indexOf("int") !== -1)) {
        val = data.temp_in_c !== undefined && data.temp_in_c !== null ? data.temp_in_c : "";
      } else if (h.indexOf("humedad int") !== -1 || (h.indexOf("hum") !== -1 && h.indexOf("int") !== -1)) {
        val = data.humidity_in !== undefined && data.humidity_in !== null ? data.humidity_in : "";
      } else if (h.indexOf("emc") !== -1) {
        val = data.in_emc !== undefined && data.in_emc !== null ? data.in_emc : "";
      } else if (h.indexOf("densidad") !== -1 || h.indexOf("air density") !== -1) {
        val = data.in_air_density !== undefined && data.in_air_density !== null ? data.in_air_density : "";
      } else if (h.indexOf("muestras") !== -1 || h.indexOf("wind samp") !== -1) {
        val = data.wind_samples !== undefined && data.wind_samples !== null ? data.wind_samples : 225;
      } else if (h.indexOf("tx") !== -1) {
        val = data.wind_tx !== undefined && data.wind_tx !== null ? data.wind_tx : 1;
      } else if (h.indexOf("recept") !== -1 || h.indexOf("recepcion") !== -1 || h.indexOf("recepción") !== -1) {
        val = data.iss_recept !== undefined && data.iss_recept !== null ? data.iss_recept : 100.0;
      } else if (h.indexOf("intervalo") !== -1 || h.indexOf("arc int") !== -1 || h.indexOf("arc. int") !== -1) {
        val = data.arc_int !== undefined && data.arc_int !== null ? data.arc_int : 10;
      } else if (h.indexOf("consola") !== -1 || (h.indexOf("bat") !== -1 && h.indexOf("v") !== -1)) {
        val = data.console_battery_v !== undefined && data.console_battery_v !== null && data.console_battery_v !== "" ? data.console_battery_v : "";
      } else if (h.indexOf("transmisor") !== -1 || h.indexOf("iss") !== -1) {
        val = data.tx_battery_status === 0 ? "OK" : (data.tx_battery_status === 1 ? "Baja" : (data.tx_battery_status ? data.tx_battery_status : ""));
      } else if (h.indexOf("solar") !== -1 || h.indexOf("radiacion") !== -1 || h.indexOf("radiación") !== -1) {
        val = data.solar_radiation_wm2 !== undefined && data.solar_radiation_wm2 !== null ? data.solar_radiation_wm2 : "";
      } else if (h.indexOf("uv") !== -1) {
        val = data.uv_index !== undefined && data.uv_index !== null ? data.uv_index : "";
      }
      row.push(val);
    }
    
    // 4. PREVENCIÓN DE DUPLICADOS: Si la fecha/hora ya existe, actualizarla en lugar de duplicar
    var targetTimestamp = (data.timestamp || "").trim();
    var lastRow = sheet.getLastRow();
    var existingRowIndex = -1;
    
    if (lastRow > 1 && targetTimestamp) {
      // Buscar en los últimos 200 registros por rendimiento
      var startSearchRow = Math.max(2, lastRow - 200);
      var countSearch = lastRow - startSearchRow + 1;
      var timestamps = sheet.getRange(startSearchRow, 1, countSearch, 1).getValues();
      for (var r = 0; r < timestamps.length; r++) {
        var tStr = timestamps[r][0] ? timestamps[r][0].toString().trim() : "";
        if (tStr === targetTimestamp) {
          existingRowIndex = startSearchRow + r;
          break;
        }
      }
    }
    
    if (existingRowIndex !== -1) {
      sheet.getRange(existingRowIndex, 1, 1, row.length).setValues([row]);
    } else {
      sheet.appendRow(row);
    }
    
    // Auto-ordenamiento cronológico ascendente (Fecha / Hora en Columna 1)
    var updatedLastRow = sheet.getLastRow();
    if (updatedLastRow > 2) {
      sheet.getRange(2, 1, updatedLastRow - 1, numCols).sort({column: 1, ascending: true});
    }
    
    return ContentService.createTextOutput("SUCCESS").setMimeType(ContentService.MimeType.TEXT);
  } catch (err) {
    return ContentService.createTextOutput("ERROR: " + err.toString()).setMimeType(ContentService.MimeType.TEXT);
  }
}

function createHeaders(sheet) {
  if (!sheet) {
    sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
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
    "Índice UV"
  ];
  sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  sheet.getRange(1, 1, 1, headers.length).setFontWeight("bold").setBackground("#202124").setFontColor("#FFFFFF");
  sheet.setFrozenRows(1);
}

function archiveCurrentYear(ss, year) {
  var currentFile = DriveApp.getFileById(ss.getId());
  var parentFolder = currentFile.getParents().hasNext() ? currentFile.getParents().next() : DriveApp.getRootFolder();
  var archiveName = currentFile.getName().replace(/ \d{4}$/, "") + " - " + year;
  currentFile.makeCopy(archiveName, parentFolder);
  
  var sheet = ss.getActiveSheet();
  var lastRow = sheet.getLastRow();
  if (lastRow > 1) {
    sheet.deleteRows(2, lastRow - 1);
  }
}

/**
 * Función para ordenar manualmente la hoja por Fecha / Hora ascendente
 */
function ordenarPorFecha() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  var lastRow = sheet.getLastRow();
  if (lastRow > 2) {
    sheet.getRange(2, 1, lastRow - 1, sheet.getLastColumn()).sort({column: 1, ascending: true});
  }
}

/**
 * Función inteligente para eliminar filas duplicadas existentes en la hoja:
 * Si hay dos filas para la misma Fecha / Hora (por ejemplo una en vivo y otra de archivo),
 * CONSERVA AUTOMÁTICAMENTE LA MÁS COMPLETA (la que tiene más datos, como Batería y Viento)
 * y elimina la fila incompleta.
 */
function eliminarDuplicados() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  var lastRow = sheet.getLastRow();
  if (lastRow <= 2) return;
  
  var numCols = sheet.getLastColumn();
  var values = sheet.getRange(2, 1, lastRow - 1, numCols).getValues();
  
  // Agrupar filas por timestamp
  var groups = {};
  for (var i = 0; i < values.length; i++) {
    var ts = values[i][0] ? values[i][0].toString().trim() : "";
    if (!ts) continue;
    if (!groups[ts]) {
      groups[ts] = [];
    }
    // Calcular cuántos datos válidos (no vacíos) tiene esta fila
    var score = 0;
    for (var c = 0; c < numCols; c++) {
      if (values[i][c] !== "" && values[i][c] !== null && values[i][c] !== undefined) {
        score++;
      }
    }
    groups[ts].push({ rowNum: i + 2, score: score });
  }
  
  var rowsToDelete = [];
  for (var key in groups) {
    if (groups[key].length > 1) {
      // Ordenar por score descendente: la fila con más columnas llenas queda primera
      groups[key].sort(function(a, b) { return b.score - a.score; });
      // Mantener la primera (la más completa) y marcar las demás para eliminar
      for (var k = 1; k < groups[key].length; k++) {
        rowsToDelete.push(groups[key][k].rowNum);
      }
    }
  }
  
  // Ordenar de mayor a menor para borrar de abajo hacia arriba sin alterar índices
  rowsToDelete.sort(function(a, b) { return b - a; });
  for (var d = 0; d < rowsToDelete.length; d++) {
    sheet.deleteRow(rowsToDelete[d]);
  }
  
  // Re-ordenar cronológicamente
  var newLastRow = sheet.getLastRow();
  if (newLastRow > 2) {
    sheet.getRange(2, 1, newLastRow - 1, numCols).sort({column: 1, ascending: true});
  }
}

/**
 * Rellena automáticamente las celdas vacías de "Viento Med 10m (km/h)"
 * tomando el valor de "Viento Actual (km/h)" (que es el promedio de 10m de la consola).
 * No altera las celdas de batería para permitir identificar registros de archivo.
 */
function rellenarVientoMedio() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
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


