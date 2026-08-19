/**
 * delayRoute.ts - hold one mocked route open until a test says otherwise.
 *
 * Every existing test on ApiKeySection, ProxySection and VoiceSettingsPage
 * `waitFor`s straight past the gap between "request sent" and "response
 * landed" - which is exactly the window where a query reads as `undefined`
 * and a naive `settings?.foo ? configured : NOT configured` collapses that
 * into a false "not configured" alarm. Nothing in the existing suites can
 * see that gap, because nothing holds it open long enough to look.
 *
 * This wraps whatever mockFetch already installed rather than replacing it:
 * every other route keeps answering immediately, and only the one route a
 * test names hangs until `release()` is called.
 */
import type { Mock } from "vitest";

export function delayRoute(
  fetchMock: Mock,
  method: string,
  urlSubstring: string,
  response: { status?: number; body: unknown },
): () => void {
  const original = fetchMock.getMockImplementation();
  if (!original) {
    throw new Error("delayRoute needs a fetchMock that already has an implementation");
  }
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  fetchMock.mockImplementation(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const reqMethod = (init?.method ?? "GET").toUpperCase();
      if (url.includes(urlSubstring) && reqMethod === method) {
        await gate;
        return new Response(JSON.stringify(response.body), {
          status: response.status ?? 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return original(input, init);
    },
  );
  return release;
}
