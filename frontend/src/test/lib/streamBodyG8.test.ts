/**
 * streamBodyG8.test.ts - the client half of the shared streaming body.
 *
 * Three things the backend now says that this side had no way to hear, plus
 * the leak that meant it sometimes never said goodbye at all:
 *
 *  - `notice`: an image the model never received. The reply reads normally,
 *    which is exactly why it has to be announced.
 *  - `partial_saved` on an error: the provider failed after text arrived and
 *    the server KEPT it. Rolling the optimistic rows back would delete a reply
 *    the user is still looking at.
 *  - `reader.cancel()` in the `finally`: cancelling was wired only to the
 *    abort signal, so every other exit left the response body open - and the
 *    server's generator never got the GeneratorExit its abort branch lives in.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { streamCompletion, StreamEventSchema, type StreamEvent } from "@/lib/api/stream";
import { sseResponse } from "../helpers/streamMocks";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function collector() {
  const events: StreamEvent[] = [];
  return { events, onEvent: (e: StreamEvent) => events.push(e) };
}

describe("the notice event", () => {
  it("parses and reaches the caller", () => {
    const parsed = StreamEventSchema.safeParse({
      type: "notice",
      code: "images_omitted",
      count: 2,
    });
    expect(parsed.success).toBe(true);
    expect(parsed.success && parsed.data).toEqual({
      type: "notice",
      code: "images_omitted",
      count: 2,
    });
  });

  it("arrives before the first delta, where it can still change the reading", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          sseResponse([
            { type: "notice", code: "images_omitted", count: 1 },
            { type: "delta", content: "I do not see an image." },
          ]),
        ),
      ),
    );

    const { events, onEvent } = collector();
    await streamCompletion("/chats/1/complete/stream", {}, { onEvent });

    expect(events.map((e) => e.type)).toEqual(["notice", "delta"]);
  });

  it("survives a count the server chose not to send", () => {
    const parsed = StreamEventSchema.safeParse({
      type: "notice",
      code: "something_new",
    });
    expect(parsed.success).toBe(true);
  });
});

describe("partial_saved on an error event", () => {
  it("is carried through to the caller", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          sseResponse([
            { type: "delta", content: "Half an answer" },
            {
              type: "error",
              status: 429,
              code: "openrouter_rate_limited",
              partial_saved: true,
            },
          ]),
        ),
      ),
    );

    const { events, onEvent } = collector();
    await streamCompletion("/chats/1/complete/stream", {}, { onEvent });

    const error = events.find((e) => e.type === "error");
    expect(error).toMatchObject({
      status: 429,
      code: "openrouter_rate_limited",
      partial_saved: true,
    });
  });

  it("is absent on a failure that produced nothing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          sseResponse([
            { type: "error", status: 429, code: "openrouter_rate_limited" },
          ]),
        ),
      ),
    );

    const { events, onEvent } = collector();
    await streamCompletion("/chats/1/complete/stream", {}, { onEvent });

    expect(events[0]).not.toHaveProperty("partial_saved");
  });
});

describe("the response body is always released", () => {
  /** A response whose reader records whether it was cancelled. */
  function trackedResponse(behaviour: "ok" | "throw") {
    const cancel = vi.fn(() => Promise.resolve());
    let served = false;
    const reader = {
      read: () => {
        if (behaviour === "throw") {
          return Promise.reject(new TypeError("network went away"));
        }
        if (served) return Promise.resolve({ done: true, value: undefined });
        served = true;
        return Promise.resolve({
          done: false,
          value: new TextEncoder().encode(
            `data: ${JSON.stringify({ type: "delta", content: "hi" })}\n\n`,
          ),
        });
      },
      cancel,
    };
    const response = {
      ok: true,
      status: 200,
      body: { getReader: () => reader },
    } as unknown as Response;
    return { response, cancel };
  }

  it("cancels the reader when the transport fails mid-stream", async () => {
    const { response, cancel } = trackedResponse("throw");
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response)));

    await expect(
      streamCompletion("/chats/1/complete/stream", {}, { onEvent: () => {} }),
    ).rejects.toMatchObject({ detail: "network_error" });

    expect(cancel).toHaveBeenCalledTimes(1);
  });

  it("cancels the reader on a clean finish too", async () => {
    const { response, cancel } = trackedResponse("ok");
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response)));

    const { events, onEvent } = collector();
    await streamCompletion("/chats/1/complete/stream", {}, { onEvent });

    expect(events).toHaveLength(1);
    expect(cancel).toHaveBeenCalledTimes(1);
  });

  it("cancels the reader when our own abort check wins the race", async () => {
    // The signal is already aborted by the time the loop re-checks it, but no
    // "abort" event ever fires - so the listener that used to be the ONLY
    // canceller never runs. This is the leak, exactly.
    const controller = new AbortController();
    const cancel = vi.fn(() => Promise.resolve());
    const reader = {
      read: () => {
        controller.abort();
        return Promise.resolve({
          done: false,
          value: new TextEncoder().encode("data: {}\n\n"),
        });
      },
      cancel,
    };
    const response = {
      ok: true,
      status: 200,
      body: { getReader: () => reader },
    } as unknown as Response;
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response)));

    await expect(
      streamCompletion(
        "/chats/1/complete/stream",
        {},
        { onEvent: () => {}, signal: controller.signal },
      ),
    ).rejects.toMatchObject({ name: "AbortError" });

    expect(cancel).toHaveBeenCalled();
  });
});
