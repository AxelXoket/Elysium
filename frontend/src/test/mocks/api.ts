import { vi } from "vitest";

/**
 * Configures a global fetch mock that routes responses based on URL.
 * Each entry maps a URL substring to its mock response.
 */
export function mockFetch(
  routes: Record<string, { status?: number; body: unknown }>,
) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";

    for (const [pattern, response] of Object.entries(routes)) {
      if (url.includes(pattern)) {
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
