// Daily summary e-mail, silent-failure alerts and the "Estado" tab (google_apps_script.js).
// Usage: node tests/apps_script/test_resumen.js   (exit code 1 on any failure)
const path = require("path");
const { loadScript, Sheet } = require("./sheets_mock");

const SCRIPT = path.join(__dirname, "..", "..", "google_apps_script.js");
let failures = 0;
const check = (cond, msg) => {
  console.log((cond ? "OK   " : "FAIL ") + msg);
  if (!cond) failures++;
};

// A station with 11-oct complete (144 records) and 12-oct until 08:00, every 10 min
function station() {
  const env = loadScript(SCRIPT);
  const { ctx, ss } = env;
  const main = new Sheet(ss, "Hoja 1");
  ctx.createHeaders(main);
  ss.sheets.push(main);
  const H = main.rows[0];
  const C = (name) => H.indexOf(name);
  let rain = 0;
  for (let t = ctx.keyMinutes_("2026-10-11 00:00:00"); t <= ctx.keyMinutes_("2026-10-12 08:00:00"); t += 10) {
    const k = ctx.shiftKey_("2026-10-11 00:00:00", t - ctx.keyMinutes_("2026-10-11 00:00:00"));
    const m = Number(k.substring(11, 13)) * 60 + Number(k.substring(14, 16));
    const temp = Math.round((10 + 8 * Math.sin(2 * Math.PI * (m - 540) / 1440)) * 10) / 10;
    if (k.startsWith("2026-10-11") && m >= 600 && m < 660) rain += 0.2;      // 1.2 mm 10:00-10:50
    if (k === "2026-10-12 00:00:00") rain = 0;
    const row = new Array(H.length).fill("");
    row[0] = k;
    row[C("Temp Ext (°C)")] = temp;
    row[C("Temp Máx 10m (°C)")] = temp;
    row[C("Temp Mín 10m (°C)")] = temp;
    row[C("Humedad Ext (%)")] = Math.round(80 - temp);
    row[C("Ráfaga 10m (km/h)")] = k === "2026-10-11 14:40:00" ? 45.1 : 12;
    row[C("Dir Ráfaga (°)")] = 180;
    row[C("Lluvia Día (mm)")] = Math.round(rain * 10) / 10;
    row[C("Lluvia Mes (mm)")] = 70 + Math.round(rain * 10) / 10;
    row[C("Lluvia Año (mm)")] = 420 + Math.round(rain * 10) / 10;
    row[C("Recepción ISS (%)")] = 99;
    row[C("Batería Consola (V)")] = 4.24;
    row[C("Batería Transmisor")] = "OK";
    row[C("Fallos WU")] = 0;
    main.rows.push(row);
  }
  env.main = main;
  env.C = C;
  env.at = (k) => main.rows.find((r) => r[0] === k);
  env.at("2026-10-11 16:40:00")[C("Reinicio")] = "Actualizacion v1.0.18";
  env.ctx.FAKE_NOW = "2026-10-12 08:00:00";
  env.props.WINDY_PASSWORD = "x";
  env.props.WINDY_CONTEO = JSON.stringify({ "2026-10-11": [144, 0] });
  env.ctx.doPost({ postData: { contents: JSON.stringify({ destino: "estado", timestamp: "ESTADO 2026-10-11 12:00:05",
    fw: "1.0.18", mem_min_kb: 48.2, err_display: 0, ssid: "2.4G_Casa", ota_route: "Proxy HTTP" }) } });
  return env;
}

// ---------- daily summary: a good day ----------
{
  const { ctx, mails } = station();
  ctx.resumenDiario();
  const mail = mails[mails.length - 1];
  check(mail && mail.to === "yo@example.com", "resumen enviado al dueño del script");
  check(mail.subject === "El Gancho 11-oct · 2,0 / 18,0 °C · 1,2 mm · ✅ todo bien", "asunto: " + mail.subject);
  // Several records round to the extreme: the first time it was reached
  check(mail.body.includes("Mínima  2,0 °C a las 02:40") && mail.body.includes("Máxima  18,0 °C a las 14:40"),
        "mínima y máxima con su hora -> " + mail.body.split("\n")[1]);
  check(mail.body.includes("Lluvia  1,2 mm   (mes 71,2 · año 421,2)"), "lluvia del día, mes y año");
  check(mail.body.includes("Ráfaga  45 km/h del S a las 14:40"), "ráfaga máxima con dirección y hora");
  check(mail.body.includes("Datos       144/144 registros (100 %)"), "registros completos");
  check(mail.body.includes("v1.0.18 · 1 actualización · 0 reinicios · RAM mínima 48 KB"),
        "firmware, actualizaciones aparte de los reinicios, RAM");
  check(mail.body.includes("Planilla ✅  WU ✅  Windy ✅") && !mail.body.includes("PROBLEMAS"), "envíos OK, sin problemas");
}

// ---------- daily summary: a bad day ----------
{
  const { ctx, mails, main, C, at, props } = station();
  main.rows = main.rows.filter((r) => !(r[0] >= "2026-10-11 20:00:00" && r[0] < "2026-10-11 21:40:00"));   // 10 lost
  at("2026-10-11 03:10:00")[C("Reinicio")] = "Watchdog";
  at("2026-10-11 03:40:00")[C("Reinicio")] = "Watchdog";
  main.rows.forEach((r) => { if (String(r[0]).startsWith("2026-10-11")) r[C("Recepción ISS (%)")] = 70; });
  at("2026-10-11 09:00:00")[C("Batería Transmisor")] = "Baja";
  main.rows.forEach((r) => { if (String(r[0]).startsWith("2026-10-11 1")) r[C("Fallos WU")] = 20; });
  props.WINDY_CONTEO = JSON.stringify({ "2026-10-11": [10, 100] });
  ctx.resumenDiario();
  const mail = mails[mails.length - 1];
  const p = mail.body.split("⚠️ PROBLEMAS")[1] || "";
  check(mail.subject.endsWith("· ⚠️ 6 problemas"), "asunto con la cantidad de problemas: " + mail.subject);
  check(p.includes("Faltan 10 de 144 registros"), "registros faltantes");
  check(p.includes("2 reinicios (Watchdog 03:10, Watchdog 03:40)"), "reinicios con causa y hora");
  check(p.includes("Recepción del transmisor 70 %"), "recepción baja");
  check(p.includes("Batería del transmisor baja"), "batería del transmisor");
  check(p.includes("Weather Underground: 1200 envíos fallidos de 2680"), "WU con fallas");
  check(p.includes("Windy: 10 envíos aceptados y 100 fallidos"), "Windy con fallas");
}

// ---------- daily summary: no data at all ----------
{
  const { ctx, mails, main } = station();
  main.rows = [main.rows[0]];
  ctx.resumenDiario();
  check(mails[mails.length - 1].subject === "El Gancho 11-oct · ⚠️ sin datos", "sin datos en el asunto");
}

// ---------- silent-failure alerts (every 30 min, once a day each) ----------
{
  const { ctx, mails, main, C, at, props } = station();
  const subjects = () => mails.map((m) => m.subject).join(" | ");
  ctx.FAKE_NOW = "2026-10-12 08:05:00";
  ctx.revisarEstacion();
  check(mails.length === 0, "estación sana: ningún aviso -> " + subjects());

  // OTA installs are expected restarts: four of them alone do not trigger the alert
  ["2026-10-11 21:00:00", "2026-10-11 21:20:00", "2026-10-11 21:40:00", "2026-10-12 06:00:00"].forEach((k, i) => {
    at(k)[C("Reinicio")] = "Actualizacion v1.0." + (15 + i);
  });
  ctx.revisarEstacion();
  check(mails.length === 0, "las actualizaciones OTA no cuentan como reinicios -> " + subjects());
  ["2026-10-11 22:00:00", "2026-10-12 03:10:00"].forEach((k) => { at(k)[C("Reinicio")] = "Watchdog"; });
  at("2026-10-12 05:00:00")[C("Reinicio")] = "Actualizacion v1.0.19 (arranque 2)";   // new version crashing
  const last = main.rows.slice(-37);
  last.forEach((r) => { r[C("Temp Ext (°C)")] = 7.7; r[C("Humedad Ext (%)")] = 91; });
  main.rows.slice(-18).forEach((r) => { r[C("Recepción ISS (%)")] = 60; });
  main.rows.slice(-12).forEach((r) => { r[C("Fallos WU")] = 20; });
  props.WINDY_ULTIMO_OK = "2026-10-12 05:00:00";
  props.WINDY_ULTIMO = "2026-10-12 08:00:00 -> HTTP 401: Unauthorized";
  ctx.revisarEstacion();
  const s = subjects();
  check(s.includes("se reinició 3 veces en 24 h"), "reinicios repetidos");
  check(s.includes("temperatura y humedad sin cambios hace 6 h"), "sensor congelado");
  check(s.includes("recepción del transmisor 60 %"), "recepción baja");
  check(s.includes("Weather Underground no recibe datos"), "WU sin recibir");
  check(s.includes("Windy no recibe datos desde las 05:00"), "Windy sin recibir");
  const n = mails.length;
  ctx.FAKE_NOW = "2026-10-12 08:35:00";
  ctx.revisarEstacion();
  check(mails.length === n, "cada aviso una sola vez por día");
}

// ---------- "Estado" tab ----------
{
  const env = loadScript(SCRIPT);
  const { ctx, ss } = env;
  const main = new Sheet(ss, "Hoja 1");
  ctx.createHeaders(main);
  ss.sheets.push(main);
  for (let i = 0; i < 144; i++) main.rows.push([ctx.shiftKey_("2026-10-11 00:00:00", 10 * i), 10]);
  // A tab created by the previous version: only its 11 columns
  const est = new Sheet(ss, "Estado", [["Fecha / Hora", "Firmware", "Encendido (h)", "Último arranque", "RAM libre (KB)",
    "RAM mínima (KB)", "WiFi (dBm)", "Fallas consola", "Fallas Sheets", "Fallas WU", "Recuperaciones consola"]]);
  ss.sheets.push(est);
  const res = ctx.doPost({ postData: { contents: JSON.stringify({ destino: "estado", timestamp: "ESTADO 2026-10-12 12:00:05",
    fw: "1.0.18", reset_cause: "Watchdog", mem_free_kb: 61, mem_min_kb: 25.5, rssi: -70, err_console: 1, err_sheets: 0,
    err_wu: 3, recoveries: 0, err_display: 2, ssid: "2.4G_Casa", ota_route: "Proxy HTTP" }) } }).m;
  const H = est.rows[0];
  const row = est.rows[1];
  const col = (t) => H.indexOf(t);
  check(res === "SUCCESS", "reporte aceptado");
  check(["Errores pantalla", "Red WiFi", "Ruta OTA", "Registros ayer"].every((t) => col(t) > 10),
        "columnas nuevas agregadas al final de la pestaña existente");
  check(row[col("Red WiFi")] === "2.4G_Casa" && row[col("Ruta OTA")] === "Proxy HTTP" && row[col("Errores pantalla")] === 2,
        "red, ruta OTA y errores de pantalla");
  check(row[col("Registros ayer")] === 144, "registros del día anterior: " + row[col("Registros ayer")]);
  const red = (t) => (est.bg || {})["2," + (col(t) + 1)] === "#f4cccc";
  check(red("RAM mínima (KB)") && red("Errores pantalla") && red("Último arranque"), "valores fuera de rango en rojo");
  check(!red("WiFi (dBm)") && !red("Registros ayer") && !red("Fallas WU"), "valores normales sin color");
}

console.log(failures ? "\n" + failures + " FALLAS" : "\nTODO OK");
process.exit(failures ? 1 : 0);
