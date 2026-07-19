/**
 * streamMocks.ts - SSE-aware fetch stubbing for streaming tests.
 *
 * src/test/mocks/api.ts is owned elsewhere (JSON-only); this helper adds
 * routes that answer with text/event-stream bodies, deferred responses, and
 * abort-signal awareness (mirrors real fetch, which rejects with AbortError
 * when the signal fires before the response resolves).
 */
import { vi } from "vitest";

export type StreamRoute =
  | { status?: number; body: unknown }
  | { sse: unknown[] }
  | { response: (init?: RequestInit) => Promise<Response> | Response };

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Serialize events as a data-only SSE body. */
export function sseBody(events: unknown[]): string {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
}

export function sseResponse(events: unknown[]): Response {
  return new Response(sseBody(events), {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

/** Standard happy-path event sequence for a completion response fixture. */
export function sseEventsFor(completion: {
  chat_id: number;
  model_id: string;
  user_message: unknown;
  assistant_message: { content: string };
}): unknown[] {
  return [
    { type: "user_message", message: completion.user_message },
    { type: "delta", content: completion.assistant_message.content },
    { type: "done", ...completion },
  ];
}

/**
 * A manually-driven SSE response: emit events chunk by chunk, close when done.
 * Lets tests hold a stream open (pending state) or split events across chunks.
 */
export function controlledSseResponse() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  const encoder = new TextEncoder();
  return {
    response: new Response(stream, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    }),
    emit(event: unknown) {
      controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
    },
    emitRaw(text: string) {
      controller.enqueue(encoder.encode(text));
    },
    close() {
      controller.close();
    },
  };
}

function makeAbortError(): Error {
  if (typeof DOMException !== "undefined") {
    return new DOMException("The operation was aborted.", "AbortError");
  }
  const err = new Error("The operation was aborted.");
  err.name = "AbortError";
  return err;
}

/** Reject the pending response when the request signal aborts - like real fetch. */
function withAbortSignal(
  signal: AbortSignal | null | undefined,
  responsePromise: Promise<Response>,
): Promise<Response> {
  if (!signal) return responsePromise;
  return new Promise<Response>((resolve, reject) => {
    if (signal.aborted) {
      reject(makeAbortError());
      return;
    }
    signal.addEventListener("abort", () => reject(makeAbortError()), {
      once: true,
    });
    responsePromise.then(resolve, reject);
  });
}

/**
 * Global fetch stub routing by URL substring (first match wins, insertion
 * order) - like mocks/api.ts mockFetch, plus `sse` and `response` routes.
 */
export function mockFetchWithStreams(routes: Record<string, StreamRoute>) {
  const mock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();

    for (const [pattern, route] of Object.entries(routes)) {
      if (!url.includes(pattern)) continue;
      let responsePromise: Promise<Response>;
      if ("response" in route) {
        responsePromise = Promise.resolve(route.response(init));
      } else if ("sse" in route) {
        responsePromise = Promise.resolve(sseResponse(route.sse));
      } else {
        responsePromise = Promise.resolve(
          jsonResponse(route.body, route.status ?? 200),
        );
      }
      return withAbortSignal(init?.signal, responsePromise);
    }

    return Promise.resolve(
      jsonResponse({ detail: `No mock for ${init?.method ?? "GET"} ${url}` }, 404),
    );
  });

  vi.stubGlobal("fetch", mock);
  return mock;
}
