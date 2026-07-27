/**
 * StreamingDrainWindow.test.tsx - the post-`done` voice-drain window.
 *
 * The backend holds the SSE body open after `done` to drain voice events (up
 * to DRAIN_TIMEOUT_S = 120s). `done` already cleared the streaming entry, so
 * the composer is enabled and the Stop button is gone: for the user the chat
 * is idle. The controller map, however, still owns the chat.
 *
 * Audit CRITICAL: in that window every startSend/startRegenerate/startEdit
 * hit `if (controllersRef.has(chatId)) return;` and vanished - no request, no
 * callback, no error, and the typed text was destroyed with zero feedback.
 *
 * These tests pin the contract: a request in the drain window supersedes the
 * tail audio (the drain is aborted) and is actually dispatched, the newcomer
 * keeps its own streaming entry and controller, and the completed exchange it
 * superseded is NOT rolled back. A genuinely LIVE stream still blocks.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useStreamingCompletion } from "@/lib/chat/useStreamingCompletion";
import { keys } from "@/lib/query/keys";
import { useErrorStore } from "@/lib/errors";
import {
  mockFetchWithStreams,
  controlledSseResponse,
  jsonResponse,
} from "../helpers/streamMocks";
import type { Message } from "@/lib/schemas/chats";
import type { ReactNode } from "react";

function msg(id: number, role: "user" | "assistant", content: string): Message {
  return {
    id,
    chat_id: 1,
    role,
    content,
    created_at: "2026-01-01T00:00:00Z",
  };
}

function newQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function createWrapper(qc: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

function messagesInCache(qc: QueryClient): Message[] {
  return qc.getQueryData<Message[]>(keys.messages(1)) ?? [];
}

/** Count fetches whose URL contains `pattern`. */
function callsTo(mock: ReturnType<typeof mockFetchWithStreams>, pattern: string) {
  return mock.mock.calls.filter(([input]) =>
    (typeof input === "string" ? input : String(input)).includes(pattern),
  );
}

const sendVars = { chatId: 1, message: "first", modelId: "m" };

describe("post-`done` voice-drain window", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  /**
   * Drive a send to `done` and STOP THERE - body still open, exactly the state
   * the backend is in while it drains voice events.
   */
  async function sendToDrainWindow(qc: QueryClient) {
    const streams = [controlledSseResponse(), controlledSseResponse()];
    let served = 0;
    const fetchMock = mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => streams[served++].response },
      "/regenerate/stream": { response: () => streams[served++].response },
      "/edit/stream": { response: () => streams[served++].response },
    });

    const { result } = renderHook(() => useStreamingCompletion(), {
      wrapper: createWrapper(qc),
    });

    let firstSend!: Promise<void>;
    await act(async () => {
      firstSend = result.current.startSend(sendVars);
    });

    streams[0].emit({ type: "user_message", message: msg(5, "user", "first") });
    streams[0].emit({ type: "delta", content: "Hello" });
    await waitFor(() => {
      expect(result.current.streamingByChat.get(1)?.text).toBe("Hello");
    });

    streams[0].emit({
      type: "done",
      chat_id: 1,
      model_id: "m",
      user_message: msg(5, "user", "first"),
      assistant_message: msg(6, "assistant", "Hello"),
    });

    // The body stays OPEN (no close()) - the drain window. The UI now reads
    // as idle: no streaming entry, so the composer is enabled again.
    await waitFor(() => {
      expect(result.current.streamingByChat.has(1)).toBe(false);
      expect(messagesInCache(qc).map((m) => m.id)).toEqual([5, 6]);
    });

    return { result, streams, fetchMock, firstSend };
  }

  it("send in the drain window is dispatched, not silently swallowed", async () => {
    const qc = newQueryClient();
    const { result, streams, fetchMock, firstSend } = await sendToDrainWindow(qc);

    const onError = vi.fn();
    const onAbortedEmpty = vi.fn();
    let secondSend!: Promise<void>;
    await act(async () => {
      secondSend = result.current.startSend(
        { chatId: 1, message: "second", modelId: "m" },
        { onError, onAbortedEmpty },
      );
    });

    // The request actually went out, carrying the second message.
    const posts = callsTo(fetchMock, "/chats/1/complete/stream");
    expect(posts).toHaveLength(2);
    expect(JSON.parse(String(posts[1][1]?.body))).toMatchObject({
      message: "second",
    });

    // The newcomer owns the chat: its streaming entry survives the superseded
    // stream's teardown (identity-guarded finally).
    await waitFor(() => {
      expect(result.current.streamingByChat.get(1)).toMatchObject({
        kind: "send",
      });
    });

    // The exchange the drain belonged to is already persisted - superseding it
    // must not run the abort rollback that deletes the user row. (The second
    // send's own optimistic row rides along with a negative id.)
    expect(
      messagesInCache(qc)
        .map((m) => m.id)
        .filter((id) => id > 0),
    ).toEqual([5, 6]);
    expect(onError).not.toHaveBeenCalled();
    expect(onAbortedEmpty).not.toHaveBeenCalled();
    expect(useErrorStore.getState().errors).toHaveLength(0);

    await act(() => firstSend);
    streams[1].close();
    await act(() => secondSend);
  });

  it("regenerate in the drain window is dispatched", async () => {
    const qc = newQueryClient();
    const { result, streams, fetchMock, firstSend } = await sendToDrainWindow(qc);

    let regen!: Promise<void>;
    await act(async () => {
      regen = result.current.startRegenerate({
        chatId: 1,
        messageId: 6,
        anchor: 6,
        modelId: "m",
      });
    });

    expect(callsTo(fetchMock, "/messages/6/regenerate/stream")).toHaveLength(1);
    await waitFor(() => {
      expect(result.current.streamingByChat.get(1)).toMatchObject({
        kind: "regenerate",
        targetMessageId: 6,
      });
    });
    expect(useErrorStore.getState().errors).toHaveLength(0);

    await act(() => firstSend);
    streams[1].close();
    await act(() => regen);
  });

  it("inline edit in the drain window is dispatched", async () => {
    const qc = newQueryClient();
    const { result, streams, fetchMock, firstSend } = await sendToDrainWindow(qc);

    let edit!: Promise<void>;
    await act(async () => {
      edit = result.current.startEdit({
        chatId: 1,
        messageId: 5,
        message: "edited ONE",
        modelId: "m",
      });
    });

    const posts = callsTo(fetchMock, "/messages/5/edit/stream");
    expect(posts).toHaveLength(1);
    expect(JSON.parse(String(posts[0][1]?.body))).toMatchObject({
      message: "edited ONE",
    });
    await waitFor(() => {
      expect(result.current.streamingByChat.get(1)).toMatchObject({
        kind: "edit",
        targetMessageId: 5,
      });
    });

    await act(() => firstSend);
    streams[1].close();
    await act(() => edit);
  });

  it("a LIVE stream still blocks a second send", async () => {
    const qc = newQueryClient();
    const stream = controlledSseResponse();
    const fetchMock = mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHook(() => useStreamingCompletion(), {
      wrapper: createWrapper(qc),
    });

    let firstSend!: Promise<void>;
    await act(async () => {
      firstSend = result.current.startSend(sendVars);
    });
    stream.emit({ type: "user_message", message: msg(5, "user", "first") });
    stream.emit({ type: "delta", content: "still going" });
    await waitFor(() => {
      expect(result.current.streamingByChat.get(1)?.text).toBe("still going");
    });

    // No `done` yet - the stream is genuinely live and keeps the slot.
    await act(async () => {
      await result.current.startSend({ chatId: 1, message: "second", modelId: "m" });
    });

    expect(callsTo(fetchMock, "/chats/1/complete/stream")).toHaveLength(1);
    expect(result.current.streamingByChat.get(1)?.text).toBe("still going");

    act(() => {
      result.current.stop(1);
    });
    await act(() => firstSend);
  });

  it("stopping inside the drain window keeps the completed exchange", async () => {
    const qc = newQueryClient();
    const { result, firstSend } = await sendToDrainWindow(qc);

    // Stop during the drain: the abort branches must not treat a persisted
    // exchange as an aborted one and delete the user row.
    act(() => {
      result.current.stop(1);
    });
    await act(() => firstSend);

    expect(messagesInCache(qc).map((m) => m.id)).toEqual([5, 6]);
    expect(useErrorStore.getState().errors).toHaveLength(0);
  });
});

/**
 * Audit HIGH: abort WITH a partial fired one immediate refetch and nothing
 * else, while the sibling abort-empty branch documents that the server's
 * GeneratorExit "propagates LATE - our refetch below usually answers BEFORE"
 * the DB write, and mitigates it twice. So the refetch typically returned the
 * pre-insert list, clearEntry removed the transient bubble, and the partial the
 * user watched arrive was invisible until they switched chats.
 */
describe("abort with a partial reply", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.useFakeTimers();
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("arms the delayed resync so the late server insert still lands", async () => {
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), []);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });
    const { result } = renderHook(() => useStreamingCompletion(), {
      wrapper: createWrapper(qc),
    });

    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.startSend(sendVars);
    });
    stream.emit({ type: "user_message", message: msg(5, "user", "first") });
    stream.emit({ type: "delta", content: "a partial reply" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    act(() => {
      result.current.stop(1);
    });
    await act(() => sendPromise);

    const messageInvalidates = () =>
      invalidateSpy.mock.calls.filter(
        ([arg]) =>
          JSON.stringify((arg as { queryKey: unknown }).queryKey) ===
          JSON.stringify(keys.messages(1)),
      ).length;

    // The immediate one - the only refetch this branch used to do.
    expect(messageInvalidates()).toBe(1);
    // The chat list preview changed too.
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: keys.chats() });

    // ...and the second one, after the server's disconnect handler has had
    // time to write the partial.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800);
    });
    expect(messageInvalidates()).toBe(2);
  });

  it("a new stream cancels a pending resync (regenerate included)", async () => {
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), []);
    const streams = [controlledSseResponse(), controlledSseResponse()];
    let served = 0;
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => streams[served++].response },
      "/regenerate/stream": { response: () => streams[served++].response },
    });
    const { result } = renderHook(() => useStreamingCompletion(), {
      wrapper: createWrapper(qc),
    });

    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.startSend(sendVars);
    });
    streams[0].emit({ type: "user_message", message: msg(5, "user", "first") });
    streams[0].emit({ type: "delta", content: "partial" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    act(() => {
      result.current.stop(1);
    });
    await act(() => sendPromise);

    // A regenerate started inside the resync window: its refetch would land
    // mid-stream with pre-append state.
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    let regen!: Promise<void>;
    await act(async () => {
      regen = result.current.startRegenerate({
        chatId: 1, messageId: 6, anchor: 6, modelId: "m",
      });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800);
    });
    expect(
      invalidateSpy.mock.calls.filter(
        ([arg]) =>
          JSON.stringify((arg as { queryKey: unknown }).queryKey) ===
          JSON.stringify(keys.messages(1)),
      ),
    ).toHaveLength(0);

    act(() => {
      result.current.stop(1);
    });
    await act(() => regen);
  });
});

/**
 * Audit: abort-empty restored staged tiles whose attachment rows were gone.
 *
 * The two abort-empty cleanups do DIFFERENT things. The server's own disconnect
 * handler UNLINKS attachments back to staged (a retry can carry them); the
 * authoritative client-side DELETE /chats/{id}/messages/{id} deletes the
 * attachment rows outright. onAbortedEmpty reported neither, so the caller
 * restored the strip either way: blank 56px tiles carrying dead ids, and a
 * retry refused with 404 attachment_not_found.
 */
describe("abort before the first token, with images", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("reports the attachments as GONE when the client deleted the row", async () => {
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), []);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/messages/5": {
        response: () => jsonResponse({ ok: true, deleted_count: 1 }),
      },
      "/chats/1/complete/stream": { response: () => stream.response },
    });
    const { result } = renderHook(() => useStreamingCompletion(), {
      wrapper: createWrapper(qc),
    });

    const onAbortedEmpty = vi.fn();
    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.startSend(
        { ...sendVars, attachments: [7] },
        { onAbortedEmpty },
      );
    });

    // The user row is persisted (so the client knows its id) but no delta.
    stream.emit({ type: "user_message", message: msg(5, "user", "first") });
    await waitFor(() => {
      expect(messagesInCache(qc).some((m) => m.id === 5)).toBe(true);
    });

    act(() => {
      result.current.stop(1);
    });
    await act(() => sendPromise);

    expect(onAbortedEmpty).toHaveBeenCalledTimes(1);
    expect(onAbortedEmpty.mock.calls[0][0]).toEqual({
      attachmentsSurvived: false,
    });
  });

  it("reports them as SURVIVING in the blind window", async () => {
    // Stopped before the user_message event: the client never learned an id,
    // so the server's own cleanup runs - and that one unlinks.
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), []);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });
    const { result } = renderHook(() => useStreamingCompletion(), {
      wrapper: createWrapper(qc),
    });

    const onAbortedEmpty = vi.fn();
    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.startSend(
        { ...sendVars, attachments: [7] },
        { onAbortedEmpty },
      );
    });
    act(() => {
      result.current.stop(1);
    });
    await act(() => sendPromise);

    expect(onAbortedEmpty.mock.calls[0][0]).toEqual({
      attachmentsSurvived: true,
    });
  });
});
