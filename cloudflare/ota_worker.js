/**
 * Davis VP2 "El Gancho" - Cloudflare Worker proxy para actualizaciones OTA
 *
 * Ruta de respaldo cuando el ESP32 no logra conectarse directo a
 * raw.githubusercontent.com (p. ej. MBEDTLS_ERR_PK_INVALID_PUBKEY).
 * Responde por HTTPS y por HTTP. Solo reenvia los archivos de firmware del
 * repositorio de la estacion, para no funcionar como proxy abierto.
 *
 *   GET /version.json            -> .../main/version.json
 *   GET /app.mpy                 -> .../main/app.mpy
 *   GET /src/<nombre>.mpy        -> .../main/src/<nombre>.mpy
 *   GET /tools/ota_rescue.py     -> .../main/tools/ota_rescue.py (instalador de emergencia)
 *
 * El ESP32 verifica la huella SHA-256 de cada archivo contra version.json.
 */

const ORIGIN = "https://raw.githubusercontent.com/jestayh/davis-vp2-el-gancho/main";
const ALLOWED = /^\/(version\.json|app\.mpy|src\/[A-Za-z0-9_]+\.mpy|tools\/ota_rescue\.py)$/;

export default {
  async fetch(request) {
    if (request.method !== "GET") {
      return new Response("Method not allowed", { status: 405 });
    }

    const path = new URL(request.url).pathname;
    if (!ALLOWED.test(path)) {
      return new Response("Not found", { status: 404 });
    }

    // Cache corto para que una version recien publicada llegue en ~1 minuto
    const upstream = await fetch(ORIGIN + path, { cf: { cacheTtl: 60, cacheEverything: true } });
    if (!upstream.ok) {
      return new Response("Upstream error " + upstream.status, { status: upstream.status });
    }

    // Todo se entrega como binario con Content-Length: el ESP32 lee el cuerpo "en bruto" y
    // no entiende Transfer-Encoding: chunked, que Cloudflare usa para tipos de texto
    // (application/json, text/plain) aunque el cuerpo este completo.
    const body = await upstream.arrayBuffer();
    return new Response(body, {
      status: 200,
      headers: { "Content-Type": "application/octet-stream", "Cache-Control": "no-store" },
    });
  },
};
