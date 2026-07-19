import { vi } from "vitest";

/** HTTP verbs recognized as a method prefix in route patterns. */
const HTTP_METHODS = [
  "GET",
  "HEAD",
  "POST",
  "PUT",
  "PATCH",
  "DELETE",
  "OPTIONS",
] as const;

/**
 * Configures a global fetch mock that routes responses based on URL - and,
 * optionally, HTTP method. The first matching entry in insertion order wins.
 *
 * Route keys come in two forms:
 *  - "url-substring"          - matches any request whose URL contains it,
 *    regardless of method (legacy behavior).
 *  - "METHOD url-substring"   - e.g. "DELETE /chats/1". The key must start
 *    with an uppercase HTTP verb followed by a space; the request method
 *    (init.method, default GET) must equal it AND the URL must contain the
 *    remainder.
 *
 * Note: for SSE/streaming responses, use src/test/helpers/streamMocks.ts
 * instead - this mock only returns plain JSON bodies.
 */
export function mockFetch(
  routes: Record<string, { status?: number; body: unknown }>,
) {
  // The vault gate wraps the app: unless a test says otherwise, the vault
  // reports unlocked so component tests exercise the app itself. Tests that
  // target the gate provide their own /vault/status entry (first match wins
  // in insertion order, so an explicit route overrides this default).
  const hasVaultRoute = Object.keys(routes).some((k) =>
    k.includes("/vault/status"),
  );
  const withDefaults: Record<string, { status?: number; body: unknown }> = {
    ...routes,
    ...(hasVaultRoute
      ? {}
      : { "/vault/status": { body: { initialized: true, unlocked: true } } }),
  };

  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();

    for (const [pattern, response] of Object.entries(withDefaults)) {
      let urlPattern = pattern;

      const spaceIndex = pattern.indexOf(" ");
      if (spaceIndex !== -1) {
        const prefix = pattern.slice(0, spaceIndex);
        if ((HTTP_METHODS as readonly string[]).includes(prefix)) {
          if (prefix !== method) continue;
          urlPattern = pattern.slice(spaceIndex + 1);
        }
      }

      if (url.includes(urlPattern)) {
        return new Response(JSON.stringify(response.body), {
          status: response.status ?? 200,
          headers: { "Content-Type": "application/json" },
        });
      }
    }

    // Default 404 for unmatched routes
    return new Response(
      JSON.stringify({ detail: `No mock for ${method} ${url}` }),
      { status: 404, headers: { "Content-Type": "application/json" } },
    );
  });

  vi.stubGlobal("fetch", mock);
  return mock;
}
