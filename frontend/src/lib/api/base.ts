/**
 * API base URL, resolved once for every transport (request, stream, upload).
 *
 * - Dev (`npm run dev`, Vite on :5173 with a SEPARATE backend on :8787):
 *   hit the backend directly at its absolute origin.
 * - Production build (served BY the FastAPI process itself, e.g. the packaged
 *   desktop app): same-origin relative path, so it works on whatever port the
 *   bundled server happens to bind.
 * - VITE_API_BASE_URL overrides both.
 */
export const API_BASE =
  import.meta.env.VITE_API_BASE_URL ??
  (import.meta.env.PROD ? "/api/v1" : "http://127.0.0.1:8787/api/v1");
