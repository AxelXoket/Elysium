/**
 * testClientCarriesProductionWindow.test.tsx - the suite lives in the same
 * cache window the app does.
 *
 * The app serves cached data for 30s before it will refetch. The test client
 * used to leave that at react-query's own default of 0, so every test refetched
 * on every remount and the window in which the app hands back data it already
 * has existed nowhere in the suite. A defect that lives only inside that window
 * was not merely untested, it was structurally untestable through this helper.
 *
 * The numbers below are read off the production client. Nothing here retypes
 * them, and nothing here reads anyone's source text: every assertion drives a
 * real useQuery through the real helper and counts real fetches.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { act, waitFor } from "@testing-library/react";
import { useQuery, QueryClient, focusManager } from "@tanstack/react-query";
import { queryClient as productionQueryClient } from "@/lib/query/queryClient";
import {
  createTestQueryClient,
  renderHookWithQueryClient,
} from "@/test/helpers/renderWithQueryClient";

/** What the app actually ships, read rather than restated. */
const productionQueries = productionQueryClient.getDefaultOptions().queries;

const KEY = ["renderWithQueryClient", "stale-window-probe"];

/** Mount the probe query, wait for its first result, then unmount. */
async function mountOnce(client: QueryClient, queryFn: () => Promise<string>) {
  const { result, unmount } = renderHookWithQueryClient(
    () => useQuery({ queryKey: KEY, queryFn }),
    { client },
  );
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  unmount();
}

/**
 * Leave the app and come back, through the same focus manager react-query
 * subscribes to - not by poking the client's internals.
 */
async function refocusWindow() {
  await act(async () => {
    focusManager.setFocused(false);
    focusManager.setFocused(true);
  });
}

/**
 * Mount a probe, let its data age past the stale window, then come back to
 * the window. Returns the fetch counter for the caller to read.
 */
async function refocusOnStaleData(client: QueryClient) {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const queryFn = vi.fn(() => Promise.resolve("payload"));
  const { result } = renderHookWithQueryClient(
    () => useQuery({ queryKey: ["focus-probe"], queryFn }),
    { client },
  );
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  vi.advanceTimersByTime(Number(productionQueries?.staleTime) + 1);

  await refocusWindow();
  // Give a refetch, if one is coming, the turns it needs to be counted.
  await act(async () => {
    await Promise.resolve();
  });
  return queryFn;
}

afterEach(() => {
  vi.useRealTimers();
  focusManager.setFocused(undefined);
});

describe("the test QueryClient carries the production cache window", () => {
  it("does not refetch a fresh query when a second consumer mounts inside the window", async () => {
    const client = createTestQueryClient();
    const queryFn = vi.fn(() => Promise.resolve("payload"));

    await mountOnce(client, queryFn);
    await mountOnce(client, queryFn);

    // Two mounts, one request: the second consumer was served the cached
    // answer, which is exactly what the app does for the first 30s.
    expect(
      queryFn,
      "a remount inside the stale window must be served from cache",
    ).toHaveBeenCalledTimes(1);
  });

  it("POSITIVE CONTROL: the same probe on a zero-window client refetches", async () => {
    // A bare client is react-query's default, staleTime 0. If the counter in
    // the test above could not see a refetch at all, it would pass on any
    // client and prove nothing. Here the refetch happens and is seen.
    const bare = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const queryFn = vi.fn(() => Promise.resolve("payload"));

    await mountOnce(bare, queryFn);
    await mountOnce(bare, queryFn);

    expect(queryFn).toHaveBeenCalledTimes(2);
  });

  it("POSITIVE CONTROL: past the window, the test client refetches too", async () => {
    const staleTime = productionQueries?.staleTime;
    // A function here would mean the window is per-query and this whole file
    // is measuring the wrong thing, so say so rather than skip.
    expect(typeof staleTime, "the global stale window must be a number").toBe(
      "number",
    );

    vi.useFakeTimers({ shouldAdvanceTime: true });
    const client = createTestQueryClient();
    const queryFn = vi.fn(() => Promise.resolve("payload"));

    await mountOnce(client, queryFn);
    vi.advanceTimersByTime((staleTime as number) + 1);
    await mountOnce(client, queryFn);

    // The window closes at the production number, not at some larger one the
    // first test would also have passed with.
    expect(queryFn).toHaveBeenCalledTimes(2);
  });

  it("does not refetch when the window is focused again, because production does not", async () => {
    // The data has to be STALE first, or the stale window alone would keep the
    // count at one and this would pass against a client that refetches on
    // focus - measuring nothing about the focus setting at all.
    const queryFn = await refocusOnStaleData(createTestQueryClient());

    // Coming back to the app is not a reason to ask again. A suite that
    // refetched here would tear down and rebuild state the app keeps.
    expect(
      queryFn,
      "regaining focus must not re-ask for data the client already has",
    ).toHaveBeenCalledTimes(1);
  });

  it("POSITIVE CONTROL: a client that does refetch on focus is seen doing it", async () => {
    const focusing = new QueryClient({
      defaultOptions: {
        queries: {
          ...productionQueries,
          retry: false,
          refetchOnWindowFocus: true,
        },
      },
    });
    // Same window, same staleness, same refocus - only the setting differs,
    // so the count above is attributable to that setting and nothing else.
    const queryFn = await refocusOnStaleData(focusing);

    expect(queryFn).toHaveBeenCalledTimes(2);
  });

  it("carries production's global query defaults, retry excepted", () => {
    const test = createTestQueryClient().getDefaultOptions().queries;

    expect(test?.staleTime).toBe(productionQueries?.staleTime);
    expect(test?.refetchOnWindowFocus).toBe(
      productionQueries?.refetchOnWindowFocus,
    );
  });

  it("GROUND CONTROL: retries stay off, and that is the one deliberate divergence", async () => {
    // Production retries once; tests must not, or an intended failure arrives
    // as three requests and a late error. This is the divergence the helper
    // exists to make, so it has to survive carrying everything else over.
    expect(productionQueries?.retry).not.toBe(false);
    expect(createTestQueryClient().getDefaultOptions().queries?.retry).toBe(
      false,
    );

    const client = createTestQueryClient();
    const failing = vi.fn(() => Promise.reject(new Error("nope")));
    const { result } = renderHookWithQueryClient(
      () => useQuery({ queryKey: ["retry-probe"], queryFn: failing }),
      { client },
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(failing, "a failing query is attempted exactly once").toHaveBeenCalledTimes(1);
  });
});
