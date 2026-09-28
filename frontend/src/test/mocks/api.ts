import { afterAll, afterEach, expect, vi } from "vitest";

import { API_BASE } from "@/lib/api/base";
import { sentinelLog, type EgressRecord } from "@/test/sentinels";
import {
  characterFixture,
  chatFixture,
  modelListFixture,
  proxyHealthFixture,
  settingsFixture,
} from "./fixtures";

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

/** Statuses the Fetch standard defines as body-less. */
const NULL_BODY_STATUSES = new Set([204, 205, 304]);

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
        const status = response.status ?? 200;
        // A 204 (or 205, 304) may not carry a body - Response() throws on
        // one - so for those the route's body is ignored and the answer is
        // empty, which is what the real backend sends for POST /activity.
        if (NULL_BODY_STATUSES.has(status)) {
          return new Response(null, { status });
        }
        return new Response(JSON.stringify(response.body), {
          status,
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

/**
 * Every request that reached the REAL transport instead of a mock.
 *
 * The two regimes, because the difference is the whole point. `mockFetch`
 * installs its mock with `vi.stubGlobal("fetch", ...)`, which replaces the
 * egress sentinel's own wrapper - so while a mock is installed the sentinel
 * records nothing and nothing can leave. With no mock installed the sentinel
 * is still there, and its host whitelist contains `127.0.0.1` on purpose (the
 * app's backend binds a random loopback port), so it RECORDS the call and
 * then forwards it to the real transport. A render of a query-driven tree
 * with no mock therefore sends real GETs to `http://127.0.0.1:8787/api/v1`,
 * which is the running application's own API in front of a live vault.
 *
 * So the sentinel's log IS the escape counter, and an empty one is the proof.
 * It is cleared before every test by sentinels.ts, so this only ever answers
 * for the test that is running.
 *
 * Pure in its argument so a test can hand it a synthetic record and show that
 * it reports: an assertion that something is empty proves nothing until the
 * reader has been seen finding something.
 */
export function apiEscapes(
  records: ReadonlyArray<EgressRecord> = sentinelLog().egress,
): string[] {
  return records
    .filter((record) => record.url.includes(API_BASE))
    .map((record) => `${record.kind} ${record.url}`);
}

/**
 * Registers the whole-file escape net: nothing in this file may reach the
 * real transport, whether or not the test that did it thought to ask.
 *
 * `apiEscapes()` asserted at the end of a test is a net with one strand per
 * test that remembered to tie one. It is also the strand most likely to be
 * skipped, because a render with no stub usually dies BEFORE it: the data
 * never arrives, `findBy*` times out after five seconds, and the run reports
 * a missing element while the request it needed has already gone out to the
 * application's own API. Measured that way - the stub was removed from a
 * shell render and the failure read `Unable to find an element with the text`
 * with no mention of the nine requests that left.
 *
 * So the check is registered once for the file. The collector runs after each
 * test, while the sentinel's log still holds that test's records (sentinels.ts
 * clears it in `beforeEach`), and names the test beside the URL. The assertion
 * runs once at the end, so the report is one list of escapes rather than a
 * failure bolted onto whichever test happened to be last, and a test added
 * later inherits it instead of having to remember it.
 */
export function guardAgainstApiEscapes(): void {
  const escaped: string[] = [];
  afterEach(() => {
    const name = expect.getState().currentTestName ?? "(unnamed test)";
    for (const line of apiEscapes()) escaped.push(`${name} :: ${line}`);
  });
  afterAll(() => {
    expect(escaped, "a render in this file reached the real transport")
      .toEqual([]);
  });
}

/**
 * The routes the app shell asks for on mount.
 *
 * `AppShell` mounts `useStaleSelectionReconciliation`, which fires the chat,
 * character, model and settings queries in one go; the sidebar adds personas
 * and the proxy health check. A render of the shell with any of them missing
 * falls through to a 404 from the mock, which is fine - what is NOT fine is a
 * render with no mock at all, which is what this exists to make cheap.
 */
export function shellRoutes(): Record<string, { status?: number; body: unknown }> {
  return {
    "/settings/proxy/health": { body: proxyHealthFixture },
    "/settings": { body: settingsFixture },
    "/characters": { body: [characterFixture] },
    "/models/openrouter": { body: modelListFixture },
    "/personas": { body: [] },
    "/chats": { body: [chatFixture] },
  };
}
