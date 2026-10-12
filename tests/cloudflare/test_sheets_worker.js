// cloudflare/sheets_worker.js with fetch() simulated. Usage: node tests/cloudflare/test_sheets_worker.js
const fs = require("fs");
const path = require("path");

let failures = 0;
const check = (cond, msg) => {
  console.log((cond ? "OK   " : "FAIL ") + msg);
  if (!cond) failures++;
};

(async () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "..", "cloudflare", "sheets_worker.js"), "utf8");
  const worker = (await import("data:text/javascript," + encodeURIComponent(src))).default;

  let google = null;            // what the fake Apps Script answers
  const sent = [];
  globalThis.fetch = async (url, opts) => {
    if (String(url).includes("version.json")) {
      return new Response(JSON.stringify({ version: "1.0.16" }), { status: 200 });
    }
    sent.push(opts.body);
    if (google instanceof Error) throw google;
    return new Response(google.body, { status: google.status });
  };
  const post = (body) => worker.fetch(new Request("http://proxy/", { method: "POST", body }), {}, { waitUntil() {} });

  google = { status: 200, body: "SUCCESS" };
  let r = await post('{"timestamp":"2026-10-12 12:00:00"}');
  check(r.status === 200 && (await r.text()) === "OK", "Google guardo la fila: 200 OK");
  check(r.headers.get("X-Davis-Fw") === "1.0.16", "informa la version publicada");
  check(sent[0] === '{"timestamp":"2026-10-12 12:00:00"}', "reenvia el cuerpo tal cual");

  google = { status: 200, body: "IGNORED: fecha invalida '2000-01-01'" };
  r = await post("{}");
  check(r.status === 200, "fila rechazada a proposito (IGNORED): 200, no se reintenta");

  google = { status: 200, body: "ERROR: Exception: Lock timeout" };
  r = await post("{}");
  check(r.status === 502 && (await r.text()).includes("Lock timeout"), "error del script: 502 para que el ESP32 la recupere");

  google = { status: 500, body: "<html>Google error</html>" };
  r = await post("{}");
  check(r.status === 502, "pagina de error de Google: 502");

  google = new Error("network down");
  r = await post("{}");
  check(r.status === 502, "sin conexion con Google: 502");

  r = await worker.fetch(new Request("http://proxy/", { method: "GET" }), {}, {});
  check(r.status === 200, "GET responde OK (prueba de vida)");

  console.log(failures ? "\n" + failures + " FALLAS" : "\nTODO OK");
  process.exit(failures ? 1 : 0);
})();
