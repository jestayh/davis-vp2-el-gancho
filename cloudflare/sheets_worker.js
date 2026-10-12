/**
 * Davis VP2 "El Gancho" - Cloudflare Worker proxy para Google Sheets
 *
 * Recibe por HTTP (puerto 80) la fila que envia el ESP32, la entrega a Google Apps Script
 * y responde "OK" (200) solo si Google la guardo; si no, responde 502 y el ESP32 la
 * recupera mas tarde desde el archivo de la consola.
 *
 * Ademas informa en la cabecera X-Davis-Fw la ultima version de firmware publicada
 * en GitHub. Si difiere de la instalada, el ESP32 revisa el OTA de inmediato, asi
 * una version nueva llega en ~10 minutos sin esperar la revision diaria.
 *
 * IMPORTANTE: pega tu URL de Apps Script en GOOGLE_URL al desplegar. No la subas
 * al repositorio (es publico y cualquiera podria escribir filas en tu hoja).
 */

const GOOGLE_URL = "PEGA_AQUI_TU_URL_DE_APPS_SCRIPT";
const VERSION_URL = "https://raw.githubusercontent.com/jestayh/davis-vp2-el-gancho/main/version.json";

// Apps Script answers "SUCCESS", or "IGNORED: ..." for rows it rejects on purpose (no valid date):
// both are final, retrying would not help. Anything else ("ERROR: ...", a Google error page) failed.
function isStored(answer) {
  return answer.startsWith("SUCCESS") || answer.startsWith("IGNORED");
}

async function latestFirmwareVersion() {
  try {
    // Nunca demorar la respuesta al ESP32 mas de 2 s por esta consulta
    const r = await Promise.race([
      fetch(VERSION_URL, { cf: { cacheTtl: 300, cacheEverything: true } }),
      new Promise((resolve) => setTimeout(() => resolve(null), 2000)),
    ]);
    if (!r) return null;
    if (!r.ok) return null;
    const v = (await r.json()).version;
    return typeof v === "string" && /^[0-9.]{1,16}$/.test(v) ? v : null;
  } catch (err) {
    return null;
  }
}

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") {
      return new Response("OK", { status: 200 });
    }

    const headers = { "Connection": "close" };
    try {
      const payload = await request.text();

      // Se espera la respuesta de Google y se informa al ESP32: si la fila no se guardo, el ESP32
      // lo trata como un corte y la recupera despues desde el archivo de la consola. (Antes se
      // respondia "OK" sin esperar, y una falla de Google perdia el dato sin aviso.)
      const [google, fw] = await Promise.all([
        fetch(GOOGLE_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: payload
        }),
        latestFirmwareVersion()     // version publicada (cacheada 5 min; si falla, no se informa)
      ]);
      if (fw) headers["X-Davis-Fw"] = fw;

      const answer = (await google.text()).trim();
      if (google.ok && isStored(answer)) {
        return new Response("OK", { status: 200, headers });
      }
      return new Response("GOOGLE " + google.status + ": " + answer.slice(0, 200), { status: 502, headers });
    } catch (err) {
      return new Response("ERR " + err, { status: 502, headers });
    }
  }
};
