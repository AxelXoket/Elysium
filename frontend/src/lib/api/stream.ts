/**
 * stream.ts - SSE transport for streaming completion endpoints.
 *
 * Data-only server-sent events; each event is a JSON object with a "type"
 * discriminator. Validation failures (chat_not_found, api_key_missing, …)
 * arrive as normal HTTP JSON errors BEFORE any stream starts and are thrown
 * with the same ApiError shape client.ts produces.
 *
 * Privacy: no Authorization header, no provider fields - the request body is
 * built by lib/generation payload builders upstream.
 */

import { z } from "zod/v4";
import { MessageSchema } from "../schemas/chats";
import { getErrorMessage } from "../errors";
import type { ApiError } from "./client";
import { notifyVaultLocked } from "./client";
import { API_BASE as BASE } from "./base";

// ── Event schemas ────────────────────────────────────────────────

export const StreamEventSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("user_message"), message: MessageSchema }),
  z.object({ type: z.literal("delta"), content: z.string() }),
  z.object({
    type: z.literal("done"),
    chat_id: z.number(),
    model_id: z.string(),
    user_message: MessageSchema,
    assistant_message: MessageSchema,
    // Regenerate only: the sibling variant deactivated by this append.
    deactivated_message_id: z.number().nullable().optional(),
  }),
  z.object({
    type: z.literal("error"),
    status: z.number(),
    code: z.string(),
  }),
]);

export type StreamEvent = z.infer<typeof StreamEventSchema>;

// ── Abort helpers ────────────────────────────────────────────────

function createAbortError(): Error {
  if (typeof DOMException !== "undefined") {
    return new DOMException("The stream was aborted.", "AbortError");
  }
  const err = new Error("The stream was aborted.");
  err.name = "AbortError";
  return err;
}

/** True for abort rejections from fetch/reader or our own abort signal. */
export function isAbortError(err: unknown): boolean {
  // Structural check: DOMException is not `instanceof Error` in every
  // runtime/realm (Node, jsdom), so match on the standard name instead.
  return (
    typeof err === "object" &&
    err !== null &&
    (err as { name?: unknown }).name === "AbortError"
  );
}

function makeApiError(status: number, detail: string): ApiError {
  return { status, detail, message: getErrorMessage(detail) };
}

// ── SSE parser ───────────────────────────────────────────────────

/**
 * Incremental data-only SSE parser.
 *
 * - Buffers across chunk boundaries (events may be split mid-JSON)
 * - Handles multiple events per chunk
 * - Tolerates CRLF line endings
 * - Joins multi-`data:` events with "\n" per the SSE spec
 * - Ignores non-data lines (comments, event:, id:, retry:) and malformed
 *   JSON defensively - a stream that ends without a terminal event is the
 *   caller's signal that something went wrong
 */
export function createSseParser(onEvent: (event: StreamEvent) => void) {
  let buffer = "";
  let dataLines: string[] = [];

  function dispatch() {
    if (dataLines.length === 0) return;
    const payload = dataLines.join("\n");
    dataLines = [];
    let json: unknown;
    try {
      json = JSON.parse(payload);
    } catch {
      return; // malformed data line - ignore defensively
    }
    const parsed = StreamEventSchema.safeParse(json);
    if (parsed.success) {
      onEvent(parsed.data);
    }
    // Unknown event types / invalid shapes are ignored (forward compatibility)
  }

  function processLine(rawLine: string) {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (line === "") {
      dispatch();
      return;
    }
    if (line.startsWith("data:")) {
      let value = line.slice(5);
      if (value.startsWith(" ")) value = value.slice(1);
      dataLines.push(value);
    }
    // Any other line is ignored defensively
  }

  return {
    /** Feed a decoded chunk. Complete lines are processed immediately. */
    push(chunk: string) {
      buffer += chunk;
      let newlineIndex = buffer.indexOf("\n");
      while (newlineIndex !== -1) {
        const line = buffer.slice(0, newlineIndex);
        buffer = buffer.slice(newlineIndex + 1);
        processLine(line);
        newlineIndex = buffer.indexOf("\n");
      }
    },
    /** Flush a trailing event whose final terminator never arrived. */
    flush() {
      if (buffer.length > 0) {
        processLine(buffer);
        buffer = "";
      }
      dispatch();
    },
  };
}

// ── Streaming request ────────────────────────────────────────────

export interface StreamOptions {
  signal?: AbortSignal;
  onEvent: (event: StreamEvent) => void;
}

/**
 * POST JSON to a streaming endpoint and dispatch each SSE event to onEvent.
 *
 * - !res.ok → throws ApiError ({status, detail, message}) exactly like the
 *   non-streaming client (validation failures never start a stream)
 * - abort → throws an "AbortError" (check with isAbortError)
 * - resolves when the stream ends; the CALLER decides whether the absence of
 *   a terminal done/error event is an error
 */
export async function streamCompletion(
  path: string,
  body: Record<string, unknown>,
  { signal, onEvent }: StreamOptions,
): Promise<void> {
  if (signal?.aborted) throw createAbortError();

  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if (isAbortError(err) || signal?.aborted) throw createAbortError();
    throw makeApiError(0, "network_error");
  }

  if (!res.ok) {
    if (res.status === 423) notifyVaultLocked();
    const json = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    const detail =
      res.status === 422 && Array.isArray(json?.detail)
        ? "invalid_generation_params"
        : typeof json?.detail === "string" && json.detail.length > 0
          ? (json.detail as string)
          : "unknown_error";
    throw makeApiError(res.status, detail);
  }

  if (!res.body) {
    throw makeApiError(res.status, "invalid_response_shape");
  }

  const reader = res.body.getReader();
  // Ensure abort terminates the read loop even when the runtime does not
  // reject in-flight reads on signal abort (also covers stubbed fetch in tests).
  const onAbort = () => {
    void reader.cancel().catch(() => undefined);
  };
  signal?.addEventListener("abort", onAbort, { once: true });

  const decoder = new TextDecoder();
  const parser = createSseParser(onEvent);

  try {
    for (;;) {
      let result: ReadableStreamReadResult<Uint8Array>;
      try {
        result = await reader.read();
      } catch (err) {
        if (isAbortError(err) || signal?.aborted) throw createAbortError();
        throw makeApiError(0, "network_error");
      }
      if (result.done) break;
      if (signal?.aborted) throw createAbortError();
      parser.push(decoder.decode(result.value, { stream: true }));
    }
    if (signal?.aborted) throw createAbortError();
    const tail = decoder.decode();
    if (tail) parser.push(tail);
    parser.flush();
  } finally {
    signal?.removeEventListener("abort", onAbort);
  }
}

// ── Typed endpoint wrappers ──────────────────────────────────────

/** Stream a chat completion. Body must come from buildCompletionPayload. */
export function streamChatCompletion(
  chatId: number,
  payload: Record<string, unknown>,
  options: StreamOptions,
): Promise<void> {
  return streamCompletion(`/chats/${chatId}/complete/stream`, payload, options);
}

/** Stream a regeneration. Body must come from buildRegeneratePayload. */
export function streamRegenerateMessage(
  chatId: number,
  messageId: number,
  payload: Record<string, unknown>,
  options: StreamOptions,
): Promise<void> {
  return streamCompletion(
    `/chats/${chatId}/messages/${messageId}/regenerate/stream`,
    payload,
    options,
  );
}
