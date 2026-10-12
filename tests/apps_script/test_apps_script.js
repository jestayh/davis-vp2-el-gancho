// google_apps_script.js against an in-memory spreadsheet filled with synthetic station data.
// Usage: node tests/apps_script/test_apps_script.js   (exit code 1 on any failure)
const path = require("path");
const { loadScript, Sheet } = require("./sheets_mock");

const SCRIPT = path.join(__dirname, "..", "..", "google_apps_script.js");
const { ctx, ss, logs, mails, lock, props } = loadScript(SCRIPT);

let failures = 0;
const check = (cond, msg) => {
  console.log((cond ? "OK   " : "FAIL ") + msg);
  if (!cond) failures++;
};
const post = (obj) => ctx.doPost({ postData: { contents: JSON.stringify(obj) } }).m;
const key = (v) => ctx.normalizeKey_(String(v));
const r1 = (x) => Math.round(x * 10) / 10;

// ---------- synthetic history: 01-sep 00:00 to 11-oct 12:00, every 10 min (~5.900 rows) ----------
const tmp = new Sheet(ss, "tmp");
ctx.createHeaders(tmp);
const HEADERS = tmp.rows[0].slice(0, 41);          // sheet created before the "Reinicio" column existed
const C = (name) => HEADERS.indexOf(name);
const CLICK = 0.254;

const truth = {};                                  // key -> row (what the console measured)
const order = [];
let dayClicks = 0, monthClicks = 0, yearClicks = Math.round(350 / CLICK), lastDay = null, lastMonth = null;
for (let i = 0, t = ctx.keyMinutes_("2026-09-01 00:00:00"); t <= ctx.keyMinutes_("2026-10-11 12:00:00"); i++, t += 10) {
  const k = ctx.shiftKey_("2026-09-01 00:00:00", t - ctx.keyMinutes_("2026-09-01 00:00:00"));
  const day = k.substring(0, 10);
  if (day !== lastDay) { dayClicks = 0; lastDay = day; }
  if (day.substring(0, 7) !== lastMonth) { monthClicks = 0; lastMonth = day.substring(0, 7); }
  const minuteOfDay = Number(k.substring(11, 13)) * 60 + Number(k.substring(14, 16));
  // Rain on 08-oct afternoon/evening (one bucket tip every 30 min) and some September days
  const rainyDay = day === "2026-10-08" || day === "2026-09-14" || day === "2026-09-30";
  const rains = rainyDay && minuteOfDay >= 12 * 60 && minuteOfDay % 30 === 0 ? 1 : 0;
  dayClicks += rains; monthClicks += rains; yearClicks += rains;
  const row = new Array(41).fill("");
  const temp = r1(12 + 6 * Math.sin(2 * Math.PI * (minuteOfDay - 600) / 1440));
  const wind = r1((i % 7) * 1.6);
  row[0] = k;
  row[C("Temp Ext (°C)")] = temp;
  row[C("Temp Máx 10m (°C)")] = temp;
  row[C("Temp Mín 10m (°C)")] = temp;
  row[C("Humedad Ext (%)")] = Math.round(70 - (temp - 12) * 2);
  row[C("Viento Actual (km/h)")] = wind;
  row[C("Dirección Viento (°)")] = 180;
  row[C("Presión (hPa)")] = r1(1015 + 2 * Math.sin(i / 50));
  row[C("Tendencia Baro")] = "Estable";
  row[C("Lluvia Int (mm)")] = r1(rains * CLICK);
  row[C("Tasa Lluvia (mm/h)")] = 0;
  row[C("Lluvia Día (mm)")] = r1(dayClicks * CLICK);
  row[C("Lluvia Tormenta (mm)")] = 0;
  row[C("Lluvia Mes (mm)")] = r1(monthClicks * CLICK);
  row[C("Lluvia Año (mm)")] = r1(yearClicks * CLICK);
  row[C("Muestras Viento")] = day === "2026-10-09" ? wind : "";   // the old column-mapping bug
  row[C("Tx Viento")] = day === "2026-10-09" ? wind : "";
  row[C("Intervalo Archivo (min)")] = 10;
  row[C("Batería Consola (V)")] = 4.24;
  truth[k] = row;
  order.push(k);
}

// The sheet lost 08-oct 10:40-20:30 (uploads stamped 1999), has two 1999 rows and a duplicate
const GAP = (k) => k >= "2026-10-08 10:40:00" && k <= "2026-10-08 20:30:00";
// (an invalid date sorts first, so that is where the script had inserted them)
const mainRows = [HEADERS.slice()];
mainRows.push(Object.assign(new Array(41).fill(""), { 0: "1999-12-31 21:10:00", 1: 14 }));
mainRows.push(Object.assign(new Array(41).fill(""), { 0: "2000-01-01 00:20:00", 1: 13 }));
for (const k of order) {
  if (GAP(k)) continue;
  mainRows.push(truth[k].slice());
  if (k === "2026-10-09 12:00:00") mainRows.push(Object.assign(truth[k].slice(), { 1: 99 }));
}
const main = new Sheet(ss, "Hoja 1", mainRows);
ss.sheets.push(main);
const rowsFor = (sheet, k) => sheet.rows.filter((r) => key(r[0]) === k);
const get = (k) => rowsFor(main, k)[0];

// ---------- doPost ----------
const n0 = main.rows.length;
ss.cellsRead = 0;
post({ timestamp: "2026-10-11 12:10:00", temp_out_c: 17, rain_interval_mm: 0, barometer_trend: "Estable",
       rain_day_mm: 0, storm_rain_mm: 0, rain_month_mm: 10.2, rain_year_mm: 421 });   // a normal live row
check(ss.cellsRead < 2000, "registro al final: lee solo el final de la hoja (" + ss.cellsRead + " celdas de " + n0 + " filas)");
const n1 = main.rows.length;
check(post({ timestamp: "2000-01-01 00:20:00", temp_out_c: 9 }).startsWith("IGNORED"), "fecha 2000 rechazada");
check(main.rows.length === n1, "no agrega filas invalidas");
check(post({ timestamp: "2026-10-11 13:00:00", temp_out_c: 18, wind_speed_kmh: 3.2, wind_samples: 225, wind_tx: 1,
             rain_day_mm: 0, storm_rain_mm: 0, rain_month_mm: 10.2, rain_year_mm: 421, reset_cause: "Encendido",
             barometer_trend: "Estable" }) === "SUCCESS", "fila normal SUCCESS");
const H = main.rows[0];
check(H[H.length - 2] === "Reinicio" && H[H.length - 1] === "Fallos WU", "columnas Reinicio y Fallos WU agregadas");
const r1300 = get("2026-10-11 13:00:00");
check(r1300[H.indexOf("Muestras Viento")] === 225 && r1300[H.indexOf("Tx Viento")] === 1, "Muestras y Tx en sus columnas (no la velocidad)");
check(r1300[H.indexOf("Reinicio")] === "Encendido", "Reinicio = Encendido");
post({ timestamp: "2026-10-11 13:00:00", temp_out_c: 18.5, rain_day_mm: 0, storm_rain_mm: 0, rain_month_mm: 10.2, rain_year_mm: 421 });
check(rowsFor(main, "2026-10-11 13:00:00").length === 1 && get("2026-10-11 13:00:00")[1] === 18.5, "fecha repetida reemplaza, no duplica");
post({ timestamp: "2026-10-11 12:30:00", temp_out_c: 17.4, rain_day_mm: 0, storm_rain_mm: 0, rain_month_mm: 10.2, rain_year_mm: 421 });
const i1230 = main.rows.findIndex((r) => key(r[0]) === "2026-10-11 12:30:00");
check(key(main.rows[i1230 - 1][0]) === "2026-10-11 12:10:00" && key(main.rows[i1230 + 1][0]) === "2026-10-11 13:00:00",
      "fila atrasada insertada en su lugar");

// ---------- console backfill: console clock 3 h behind ----------
let sent = 0;
for (const k of order.filter((k) => k >= "2026-10-08 03:00:00" && k <= "2026-10-09 02:50:00")) {
  const r = truth[k];
  const res = post({
    timestamp: "CONSOLA " + ctx.shiftKey_(k, -180), destino: "consola",
    temp_out_c: r[C("Temp Ext (°C)")] + 0.1, temp_out_hi_c: r[C("Temp Máx 10m (°C)")], temp_out_low_c: r[C("Temp Mín 10m (°C)")],
    humidity_out: r[C("Humedad Ext (%)")], pressure_hpa: r[C("Presión (hPa)")], wind_speed_kmh: r[C("Viento Actual (km/h)")],
    rain_interval_mm: r[C("Lluvia Int (mm)")], rain_day_mm: null, storm_rain_mm: null, rain_month_mm: null, rain_year_mm: null,
    wind_samples: 223, wind_tx: 1, iss_recept: 99.1,
  });
  if (res === "SUCCESS") sent++;
}
const cons = ss.getSheetByName("Consola");
check(cons && cons.rows.length - 1 === sent && sent === 144, "pestaña Consola con " + sent + " filas");
check(ss.getSheets()[0] === main, "la hoja principal sigue primera");

// ---------- importarConsola ----------
const before = main.rows.length;
ctx.importarConsola();
check(logs.some((l) => l.startsWith("Mejor desfase: +180 min")), "desfase detectado +180 min");
check(main.rows.length - before === 60, "insertadas " + (main.rows.length - before) + " filas (10:40 a 20:30)");
const HH = main.rows[0];
const c = (n) => HH.indexOf(n);
let totalsOk = true;
for (const k of order.filter(GAP)) {
  const got = get(k), want = truth[k];
  for (const n of ["Lluvia Int (mm)", "Lluvia Día (mm)", "Lluvia Mes (mm)", "Lluvia Año (mm)"]) {
    if (got[c(n)] !== want[C(n)]) { totalsOk = false; console.log("     " + k + " " + n + ": " + got[c(n)] + " != " + want[C(n)]); }
  }
}
check(totalsOk, "lluvia del intervalo, dia, mes y año de las filas importadas = lo medido");
check(get("2026-10-08 13:30:00")[c("Tendencia Baro")] !== "", "tendencia calculada");
check(get("2026-10-08 15:00:00")[c("Muestras Viento")] === 223, "muestras de la consola copiadas");
ctx.importarConsola();
check(main.rows.length - before === 60, "volver a importar no duplica");

// ---------- repararHoja ----------
ctx.repararHoja();
check(!main.rows.slice(1).some((r) => String(r[0]) < "2025"), "sin filas 1999/2000");
check(rowsFor(main, "2026-10-09 12:00:00").length === 1, "duplicado eliminado");
const iW = c("Viento Actual (km/h)"), iS = c("Muestras Viento"), iT = c("Tx Viento");
check(!main.rows.slice(1).some((r) => r[iW] !== "" && r[iS] === r[iW] && r[iT] === r[iW]), "Muestras/Tx erroneos vaciados");
check(get("2026-10-08 15:00:00")[iS] === 223, "muestras reales conservadas");
const keys = main.rows.slice(1).map((r) => key(r[0]));
check(keys.every((k, i) => i === 0 || keys[i - 1] < k), "hoja ordenada y sin fechas repetidas (" + keys.length + " filas)");
let daySumOk = true;
for (const day of ["2026-09-14", "2026-09-30", "2026-10-07", "2026-10-08", "2026-10-09"]) {
  const rows = main.rows.slice(1).filter((r) => key(r[0]).startsWith(day));
  const sum = r1(rows.reduce((s, r) => s + (Number(r[c("Lluvia Int (mm)")]) || 0), 0));
  const dayTotal = rows[rows.length - 1][c("Lluvia Día (mm)")];
  if (sum !== dayTotal) { daySumOk = false; console.log("     " + day + ": suma " + sum + " != dia " + dayTotal); }
}
check(daySumOk, "la Lluvia Int de cada dia suma su Lluvia Día");

// ---------- manual functions never run at the same time ----------
lock.free = false;
const rowsNow = main.rows.length;
ctx.repararHoja();
check(main.rows.length === rowsNow && logs[logs.length - 1].startsWith("Otra función"), "con el bloqueo tomado no modifica la hoja");
lock.free = true;

// ---------- Lluvia Int deduced (first record after a reboot / midnight / gap) ----------
const cI = c("Lluvia Int (mm)"), cD = c("Lluvia Día (mm)");
const live = (ts, day, int) => post({ timestamp: ts, temp_out_c: 10, rain_interval_mm: int, rain_day_mm: day,
                                      storm_rain_mm: day, rain_month_mm: 70 + day, rain_year_mm: 430 + day });
live("2026-10-11 13:10:00", 0.5, null);
check(get("2026-10-11 13:10:00")[cI] === 0.5, "tras reinicio: Lluvia Int 0.5 deducida (dia 0 -> 0.5)");
live("2026-10-11 23:50:00", 1.0, 0);
live("2026-10-12 00:00:00", 0.3, null);
check(get("2026-10-12 00:00:00")[cI] === 0.3, "tras medianoche: Lluvia Int 0.3 (la consola reinicio el dia)");
live("2026-10-12 01:00:00", 1.5, null);
check(get("2026-10-12 01:00:00")[cI] === "", "con hueco antes: Lluvia Int queda vacia (no inventa)");
for (const [t, r] of [["00:10", 0.3], ["00:20", 0], ["00:30", 0.5], ["00:40", 0], ["00:50", 0.3]]) {
  post({ timestamp: "2026-10-12 " + t + ":00", temp_out_c: 10, rain_interval_mm: r,
         rain_day_mm: null, storm_rain_mm: null, rain_month_mm: null, rain_year_mm: null });
}
check(get("2026-10-12 00:50:00")[cD] === 1.3, "acumulado del dia recuperado 1.3 (5 golpes) a las 00:50");
check(get("2026-10-12 01:00:00")[cI] === 0.2, "al llenar el hueco, 01:00 recibe Lluvia Int 0.2");

// ---------- daily ESP32 health report ----------
const mainRowsBefore = main.rows.length;
const status = { destino: "estado", timestamp: "ESTADO 2026-10-12 12:00:05", fw: "1.0.15", uptime_h: 13.2,
                 reset_cause: "Watchdog", mem_free_kb: 61.5, mem_min_kb: 48.2, rssi: -67,
                 err_console: 2, err_sheets: 0, err_wu: 1, recoveries: 1 };
check(post(status) === "SUCCESS", "reporte de estado aceptado");
const est = ss.getSheetByName("Estado");
check(est && ss.getSheets()[ss.getSheets().length - 1] === est && ss.getSheets()[0] === main, "pestaña Estado creada al final");
check(main.rows.length === mainRowsBefore, "el reporte no toca la hoja de datos");
const ESTH = est.rows[0];
const er = est.rows[1];
check(er[0] === "2026-10-12 12:00:05" && er[ESTH.indexOf("Firmware")] === "1.0.15" &&
      er[ESTH.indexOf("Último arranque")] === "Watchdog" && er[ESTH.indexOf("WiFi (dBm)")] === -67 &&
      er[ESTH.indexOf("Fallas Sheets")] === 0, "columnas del reporte en su lugar");
post(Object.assign({}, status, { timestamp: "ESTADO 2026-10-13 12:00:07", uptime_h: 37.2 }));
check(est.rows.length === 3, "un reporte por fila");
check(post(Object.assign({}, status, { timestamp: "ESTADO 2000-01-01 00:00:00" })).startsWith("IGNORED"), "reporte sin hora valida rechazado");
post({ timestamp: "2026-10-11 13:20:00", temp_out_c: 10, rain_day_mm: 0.5, storm_rain_mm: 0.5, rain_month_mm: 70.5, rain_year_mm: 430.5 });
check(!!get("2026-10-11 13:20:00"), "con la pestaña Estado, los datos siguen yendo a la hoja principal");

// ---------- e-mail alerts ----------
ctx.FAKE_NOW = "2026-10-12 01:40:00";
ctx.activarAvisos();
ctx.activarAvisos();
check(ctx.ScriptApp.triggers.length === 2 && ctx.ScriptApp.triggers[0].m === 30 &&
      ctx.ScriptApp.triggers[1].fn === "resumenDiario" && ctx.ScriptApp.triggers[1].h === 8,
      "un activador cada 30 min y uno diario a las 8 (sin duplicar)");
check(mails.length === 0, "40 min sin datos: sin correo");
ctx.FAKE_NOW = "2026-10-12 02:10:00";
ctx.revisarEstacion();
check(mails.length === 1 && mails[0].subject.includes("sin datos desde las 01:00"), "70 min: correo de alerta");
ctx.FAKE_NOW = "2026-10-12 02:40:00";
ctx.revisarEstacion();
check(mails.length === 1, "no repite el aviso en el mismo corte");
live("2026-10-12 02:40:00", 0.9, 0);
ctx.revisarEstacion();
check(mails.length === 2 && mails[1].subject.includes("volvieron"), "correo cuando vuelven los datos");

// ---------- weather alerts (checked by the same 30-min trigger) ----------
const wx = (ts, extra) => post(Object.assign({ timestamp: ts, temp_out_c: 5, rain_interval_mm: 0, rain_day_mm: 0.9,
  storm_rain_mm: 0.9, rain_month_mm: 70.9, rain_year_mm: 430.9, rain_rate_mm_per_hr: 0, windgustkmh_10m: 10,
  console_battery_v: 4.2, tx_battery_status: 0, barometer_trend: "Estable" }, extra));
const subjects = () => mails.slice(2).map((m) => m.subject);
wx("2026-10-12 02:50:00", { temp_out_c: 1.5 });
ctx.FAKE_NOW = "2026-10-12 02:55:00";
ctx.revisarEstacion();
check(subjects().length === 1 && subjects()[0].includes("riesgo de helada (1.5 °C"), "riesgo de helada -> " + subjects());
wx("2026-10-12 03:00:00", { temp_out_c: -0.5, windgustkmh_10m: 56.3, rain_rate_mm_per_hr: 12.2, console_battery_v: 3.4,
                            tx_battery_status: 1 });
ctx.FAKE_NOW = "2026-10-12 03:05:00";
ctx.revisarEstacion();
const s = subjects();
check(s.some((x) => x.includes("helada (-0.5 °C a las 03:00)")), "helada");
check(s.some((x) => x.includes("ráfagas de 56.3 km/h")), "ráfaga fuerte");
check(s.some((x) => x.includes("lluvia intensa")), "lluvia intensa");
check(s.some((x) => x.includes("batería de la consola baja (3.4 V)")), "batería de la consola baja");
check(s.some((x) => x.includes("batería del transmisor baja")), "batería del transmisor baja");
const count = mails.length;
wx("2026-10-12 03:10:00", { temp_out_c: -1.2, windgustkmh_10m: 60, rain_rate_mm_per_hr: 15, console_battery_v: 3.3,
                            tx_battery_status: 1 });
ctx.FAKE_NOW = "2026-10-12 03:35:00";
ctx.revisarEstacion();
check(mails.length === count, "no repite avisos en la misma noche / dia / semana");
ctx.FAKE_NOW = "2026-10-12 08:00:00";
wx("2026-10-12 07:50:00", { temp_out_c: 1.0 });
ctx.revisarEstacion();
check(mails.length === count, "ya hubo helada esa noche: no avisa riesgo despues");
wx("2026-10-13 02:00:00", { temp_out_c: -2 });
ctx.FAKE_NOW = "2026-10-13 02:05:00";
ctx.revisarEstacion();
check(mails.slice(count).some((m) => m.subject.includes("helada (-2 °C")), "otra noche: nuevo aviso de helada");
check(!mails.slice(count).some((m) => m.subject.includes("batería")), "baterías: un aviso por semana");

// ---------- Windy ----------
const windy = ctx.UrlFetchApp;
check(windy.requests.length === 0, "sin WINDY_PASSWORD no se envía nada a Windy");
props.WINDY_PASSWORD = "clave-de-prueba";
ctx.FAKE_NOW = "2026-10-13 02:15:00";
wx("2026-10-13 02:10:00", { rain_interval_mm: 0.3 });
ctx.FAKE_NOW = "2026-10-13 02:25:00";
check(wx("2026-10-13 02:20:00", { temp_out_c: -1.5, dewpoint_c: -3.04, humidity_out: 88, wind_speed_kmh: 20,
  wind_speed_10min_avg_kmh: 18, windgustkmh_10m: 36, wind_direction_deg: 360, pressure_hpa: 1013.4,
  rain_interval_mm: 0.5, uv_index: 0, solar_radiation_wm2: 0 }) === "SUCCESS", "fila guardada con Windy activo");
check(windy.requests.length === 2, "un envío a Windy por registro reciente");
const w = windy.requests[1];
const q = new URLSearchParams(w.url.split("?")[1]);
check(w.url.startsWith("https://stations.windy.com/api/v2/observation/update?") && q.get("id") === "NPLL5k7",
      "endpoint v2 con el ID de la estación");
check(w.opts.headers.Authorization === "Bearer clave-de-prueba" && !w.url.includes("clave-de-prueba"),
      "contraseña en la cabecera, no en la URL");
check(q.get("temp") === "-1.5" && q.get("dewpoint") === "-3" && q.get("humidity") === "88", "temperatura, rocío y humedad");
check(q.get("wind") === "5" && q.get("gust") === "10" && q.get("winddir") === "0", "viento medio 10 min y ráfaga en m/s");
check(q.get("pressure") === "101340", "presión en Pa");
check(q.get("precip") === "0.8", "lluvia de la última hora (0.3 + 0.5 mm) -> " + q.get("precip"));
check(Math.abs(Number(q.get("ts")) - Date.now() / 1000 + 300) < 5, "hora de la observación (hace 5 min)");
check(props.WINDY_ULTIMO === "2026-10-13 02:20:00 -> HTTP 200", "respuesta registrada");
wx("2026-10-13 01:30:00", {});
post({ destino: "consola", timestamp: "CONSOLA 2026-10-13 02:30:00", temp_out_c: 3 });
check(windy.requests.length === 2, "no se envían a Windy registros recuperados ni de la pestaña Consola");
windy.code = 400;
check(wx("2026-10-13 02:30:00", {}) === "SUCCESS" && props.WINDY_ULTIMO.includes("HTTP 400: Bad request"),
      "si Windy rechaza, la fila igual se guarda");
windy.fail = true;
check(wx("2026-10-13 02:30:00", {}) === "SUCCESS" && props.WINDY_ULTIMO.includes("DNS error"),
      "si Windy no responde, la fila igual se guarda");
ctx.estadoWindy();
check(logs.some((l) => l.includes("WINDY_PASSWORD): configurada")), "estadoWindy informa la configuración");

console.log(failures ? "\n" + failures + " FALLAS" : "\nTODO OK");
process.exit(failures ? 1 : 0);
