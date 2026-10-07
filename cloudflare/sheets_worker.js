/**
 * Davis VP2 "El Gancho" - Cloudflare Worker proxy para Google Sheets
 *
 * Recibe por HTTP (puerto 80) la fila que envia el ESP32, responde "OK" al instante
 * y entrega la fila a Google Apps Script en segundo plano.
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

    try {
      const payload = await request.text();

      // Cloudflare deposita los datos en Google Sheets en segundo plano en la nube
      ctx.waitUntil(
        fetch(GOOGLE_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: payload
        })
      );

      // Version publicada (cacheada 5 min; si falla, simplemente no se informa)
      const headers = { "Connection": "close" };
      const fw = await latestFirmwareVersion();
      if (fw) headers["X-Davis-Fw"] = fw;

      // Responde al ESP32 al instante para que no espere a Google
      return new Response("OK", { status: 200, headers });
    } catch (err) {
      return new Response("ERR", { status: 500 });
    }
  }
};
