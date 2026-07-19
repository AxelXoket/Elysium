/**
 * StreamApi.test.ts - SSE parser and streaming transport unit tests.
 *
 * Parser: chunk-boundary handling (mid-JSON splits), multiple events per
 * chunk, CRLF tolerance, trailing partial buffer, defensive skipping of
 * non-data lines / malformed JSON / unknown event types.
 *
 * Transport: HTTP error → ApiError shape, network failure → network_error,
 * abort → AbortError, 422 array detail normalization.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import {
  createSseParser,
  streamCompletion,
  isAbortError,
  type StreamEvent,
} from "@/lib/api/stream";
import { isApiError } from "@/lib/api/client";
import { sseResponse, jsonResponse } from "../helpers/streamMocks";

function collector() {
  const events: StreamEvent[] = [];
  return { events, onEvent: (e: StreamEvent) => events.push(e) };
}

const deltaA = { type: "delta", content: "Hello " };
const deltaB = { type: "delta", content: "world" };

describe("createSseParser", () => {
  it("parses multiple events arriving in a single chunk", () => {
    const { events, onEvent } = collector();
    const parser = createSseParser(onEvent);

    parser.push(
      `data: ${JSON.stringify(deltaA)}\n\ndata: ${JSON.stringify(deltaB)}\n\n`,
    );

    expect(events).toEqual([deltaA, deltaB]);
  });

  it("parses an event split mid-JSON across chunks", () => {
    const { events, onEvent } = collector();
    const parser = createSseParser(onEvent);
    const full = `data: ${JSON.stringify(deltaA)}\n\n`;

    // Split in the middle of the JSON payload
    const splitAt = full.indexOf("content") + 3;
    parser.push(full.slice(0, splitAt));
    expect(events).toEqual([]); // nothing complete yet
    parser.push(full.slice(splitAt));

    expect(events).toEqual([deltaA]);
  });

  it("handles a chunk boundary exactly between the two newlines", () => {
    const { events, onEvent } = collector();
    const parser = createSseParser(onEvent);

    parser.push(`data: ${JSON.stringify(deltaA)}\n`);
    expect(events).toEqual([]);
    parser.push(`\ndata: ${JSON.stringify(deltaB)}\n\n`);

    expect(events).toEqual([deltaA, deltaB]);
  });

  it("tolerates CRLF line endings", () => {
    const { events, onEvent } = collector();
    const parser = createSseParser(onEvent);

    parser.push(
      `data: ${JSON.stringify(deltaA)}\r\n\r\ndata: ${JSON.stringify(deltaB)}\r\n\r\n`,
    );

    expect(events).toEqual([deltaA, deltaB]);
  });

  it("flushes a trailing event whose terminator never arrived", () => {
    const { events, onEvent } = collector();
    const parser = createSseParser(onEvent);

    parser.push(`data: ${JSON.stringify(deltaA)}`); // no newline at all
    expect(events).toEqual([]);
    parser.flush();

    expect(events).toEqual([deltaA]);
  });

  it("ignores comment, event:, id: and retry: lines defensively", () => {
    const { events, onEvent } = collector();
    const parser = createSseParser(onEvent);

    parser.push(": keep-alive\n");
    parser.push("event: message\n");
    parser.push("id: 42\n");
    parser.push("retry: 1000\n");
    parser.push(`data: ${JSON.stringify(deltaA)}\n\n`);

    expect(events).toEqual([deltaA]);
  });

  it("skips malformed JSON and unknown event types, keeps parsing", () => {
    const { events, onEvent } = collector();
    const parser = createSseParser(onEvent);

    parser.push("data: {not json\n\n");
    parser.push(`data: ${JSON.stringify({ type: "future_event", x: 1 })}\n\n`);
    parser.push(`data: ${JSON.stringify(deltaB)}\n\n`);

    expect(events).toEqual([deltaB]);
  });

  it("supports data lines without a space after the colon", () => {
    const { events, onEvent } = collector();
    const parser = createSseParser(onEvent);

    parser.push(`data:${JSON.stringify(deltaA)}\n\n`);

    expect(events).toEqual([deltaA]);
  });
});

describe("streamCompletion", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("dispatches events in order for a well-formed stream", async () => {
    const done = {
      type: "done",
      chat_id: 1,
      model_id: "m",
      user_message: {
        id: 2,
        chat_id: 1,
        role: "user",
        content: "hi",
        created_at: "2026-01-01T00:00:00",
      },
      assistant_message: {
        id: 3,
        chat_id: 1,
        role: "assistant",
        content: "Hello world",
        created_at: "2026-01-01T00:00:01",
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => sseResponse([deltaA, deltaB, done])),
    );

    const { events, onEvent } = collector();
    await streamCompletion("/chats/1/complete/stream", { model_id: "m" }, { onEvent });

    expect(events.map((e) => e.type)).toEqual(["delta", "delta", "done"]);
  });

  it("throws the client ApiError shape on HTTP errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "chat_not_found" }, 404)),
    );

    const { onEvent } = collector();
    let thrown: unknown;
    try {
      await streamCompletion("/chats/1/complete/stream", {}, { onEvent });
    } catch (err) {
      thrown = err;
    }

    expect(isApiError(thrown)).toBe(true);
    expect(thrown).toMatchObject({
      status: 404,
      detail: "chat_not_found",
      message: "This chat no longer exists.",
    });
  });

  it("normalizes 422 array detail to invalid_generation_params", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ detail: [{ loc: ["temperature"], msg: "bad" }] }, 422),
      ),
    );

    let thrown: unknown;
    try {
      await streamCompletion("/chats/1/complete/stream", {}, { onEvent: () => {} });
    } catch (err) {
      thrown = err;
    }

    expect(thrown).toMatchObject({
      status: 422,
      detail: "invalid_generation_params",
    });
  });

  it("maps network failures to network_error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );

    let thrown: unknown;
    try {
      await streamCompletion("/chats/1/complete/stream", {}, { onEvent: () => {} });
    } catch (err) {
      thrown = err;
    }

    expect(thrown).toMatchObject({ status: 0, detail: "network_error" });
  });

  it("throws an AbortError when the signal is already aborted", async () => {
    vi.stubGlobal("fetch", vi.fn());
    const controller = new AbortController();
    controller.abort();

    let thrown: unknown;
    try {
      await streamCompletion("/chats/1/complete/stream", {}, {
        signal: controller.signal,
        onEvent: () => {},
      });
    } catch (err) {
      thrown = err;
    }

    expect(isAbortError(thrown)).toBe(true);
  });

  it("aborting mid-stream throws AbortError and stops dispatching", async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        controller = c;
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(body, {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          }),
      ),
    );

    const abort = new AbortController();
    const { events, onEvent } = collector();
    const promise = streamCompletion("/chats/1/complete/stream", {}, {
      signal: abort.signal,
      onEvent,
    });

    const encoder = new TextEncoder();
    controller.enqueue(encoder.encode(`data: ${JSON.stringify(deltaA)}\n\n`));
    // Give the read loop a tick to consume the first event
    await new Promise((r) => setTimeout(r, 10));
    abort.abort();

    let thrown: unknown;
    try {
      await promise;
    } catch (err) {
      thrown = err;
    }

    expect(isAbortError(thrown)).toBe(true);
    expect(events).toEqual([deltaA]);
  });
});
