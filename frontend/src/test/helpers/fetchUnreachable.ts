/**
 * fetchUnreachable.ts - a backend that is not there at all.
 *
 * mockFetch answers every route with a Response, so the worst it can model is
 * a status code. An unreachable backend is a different failure: fetch itself
 * rejects before any status exists, and request() turns that rejection into
 * its NETWORK_ERROR (status 0). The two travel different paths through the
 * client, and the second is the likelier one in practice (the local server
 * not started, or stopped underneath an open settings panel), so a test that
 * only sends a 500 has not seen the common case.
 *
 * Installed the same way mockFetch installs itself, at the global fetch, so
 * nothing under test is replaced and everything above the network runs for
 * real.
 */
import { vi } from "vitest";

export function mockFetchUnreachable() {
  const mock = vi.fn(async (): Promise<Response> => {
    throw new TypeError("Failed to fetch");
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}
