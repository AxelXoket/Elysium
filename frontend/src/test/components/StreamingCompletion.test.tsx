/**
 * StreamingCompletion.test.tsx - useStreamingCompletion hook behavior.
 *
 * Covers:
 *  - send happy path: optimistic insert → user_message swap → deltas in
 *    local state (not cache) → done appends assistant + clears state
 *  - send provider error event: user row removed (backend deleted it),
 *    onError fired, messages invalidated, no toast
 *  - send abort with partial: messages invalidated (backend persisted the
 *    partial), user row kept, silent
 *  - send abort with no partial: user rows removed, onAbortedEmpty fired,
 *    silent
 *  - regenerate: done swaps the old assistant row; error keeps the old row
 *    and pushes a toast; abort is silent
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
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
}

function createWrapper(qc: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

function messagesInCache(qc: QueryClient): Message[] {
  return qc.getQueryData<Message[]>(keys.messages(1)) ?? [];
}

const seedGreeting = msg(1, "assistant", "greeting");

const sendVars = { chatId: 1, message: "stream me", modelId: "m" };

describe("useStreamingCompletion - send", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("happy path: optimistic → user swap → deltas → done", async () => {
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
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

    // Optimistic user message (negative id) + streaming entry active
    await waitFor(() => {
      expect(messagesInCache(qc).some((m) => m.id < 0)).toBe(true);
      expect(result.current.streamingByChat.get(1)).toMatchObject({
        kind: "send",
        text: "",
      });
    });

    // Persisted user row replaces the optimistic one
    stream.emit({ type: "user_message", message: msg(5, "user", "stream me") });
    await waitFor(() => {
      const cached = messagesInCache(qc);
      expect(cached.some((m) => m.id === 5)).toBe(true);
      expect(cached.some((m) => m.id < 0)).toBe(false);
    });

    // Deltas accumulate in local state - NOT in the query cache
    stream.emit({ type: "delta", content: "Hel" });
    stream.emit({ type: "delta", content: "lo" });
    await waitFor(() => {
      expect(result.current.streamingByChat.get(1)?.text).toBe("Hello");
    });
    expect(messagesInCache(qc).map((m) => m.id)).toEqual([1, 5]);

    // Done: assistant appended, streaming state cleared
    stream.emit({
      type: "done",
      chat_id: 1,
      model_id: "m",
      user_message: msg(5, "user", "stream me"),
      assistant_message: msg(6, "assistant", "Hello"),
    });
    stream.close();
    await act(() => sendPromise);

    expect(messagesInCache(qc).map((m) => m.id)).toEqual([1, 5, 6]);
    expect(result.current.streamingByChat.has(1)).toBe(false);
    expect(useErrorStore.getState().errors).toHaveLength(0);
  });

  it("provider error event: user row removed, onError fired, no toast", async () => {
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHook(() => useStreamingCompletion(), {
      wrapper: createWrapper(qc),
    });

    const onError = vi.fn();
    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.startSend(sendVars, { onError });
    });

    stream.emit({ type: "user_message", message: msg(5, "user", "stream me") });
    stream.emit({ type: "delta", content: "par" });
    await waitFor(() => {
      expect(result.current.streamingByChat.get(1)?.text).toBe("par");
    });

    stream.emit({ type: "error", status: 502, code: "openrouter_completion_error" });
    stream.close();
    await act(() => sendPromise);

    // Backend deleted the user row - cache mirrors it
    expect(messagesInCache(qc).map((m) => m.id)).toEqual([1]);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0]).toMatchObject({
      status: 502,
      detail: "openrouter_completion_error",
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: keys.messages(1) });
    // Send errors surface in the Composer banner - never as a toast
    expect(useErrorStore.getState().errors).toHaveLength(0);
    expect(result.current.streamingByChat.has(1)).toBe(false);
  });

  it("abort with partial text: keeps user row and refetches messages", async () => {
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHook(() => useStreamingCompletion(), {
      wrapper: createWrapper(qc),
    });

    const onError = vi.fn();
    const onAbortedEmpty = vi.fn();
    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.startSend(sendVars, { onError, onAbortedEmpty });
    });

    stream.emit({ type: "user_message", message: msg(5, "user", "stream me") });
    stream.emit({ type: "delta", content: "partial tex" });
    await waitFor(() => {
      expect(result.current.streamingByChat.get(1)?.text).toBe("partial tex");
    });

    act(() => {
      result.current.stop(1);
    });
    await act(() => sendPromise);

    // Backend persisted the partial - user row stays, refetch resyncs
    expect(messagesInCache(qc).some((m) => m.id === 5)).toBe(true);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: keys.messages(1) });
    expect(onError).not.toHaveBeenCalled();
    expect(onAbortedEmpty).not.toHaveBeenCalled();
    expect(useErrorStore.getState().errors).toHaveLength(0);
    expect(result.current.streamingByChat.has(1)).toBe(false);
  });

  it("abort with no text: removes user rows and fires onAbortedEmpty", async () => {
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHook(() => useStreamingCompletion(), {
      wrapper: createWrapper(qc),
    });

    const onError = vi.fn();
    const onAbortedEmpty = vi.fn();
    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.startSend(sendVars, { onError, onAbortedEmpty });
    });

    stream.emit({ type: "user_message", message: msg(5, "user", "stream me") });
    await waitFor(() => {
      expect(messagesInCache(qc).some((m) => m.id === 5)).toBe(true);
    });

    act(() => {
      result.current.stop(1);
    });
    await act(() => sendPromise);

    // Backend deleted the user row - silent cleanup, draft handled by caller
    expect(messagesInCache(qc).map((m) => m.id)).toEqual([1]);
    expect(onAbortedEmpty).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
    expect(useErrorStore.getState().errors).toHaveLength(0);
    expect(result.current.streamingByChat.has(1)).toBe(false);
  });
});

describe("useStreamingCompletion - regenerate", () => {
  const chatMessages = [msg(2, "user", "prompt"), msg(3, "assistant", "old answer")];
  // anchor = messageId here: the fixture message has no variant siblings.
  const regenerateVars = { chatId: 1, messageId: 3, anchor: 3, modelId: "m" };

  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("done appends the new variant and deactivates the old row in place", async () => {
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), chatMessages);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/regenerate/stream": { response: () => stream.response },
    });

    const { result } = renderHook(() => useStreamingCompletion(), {
      wrapper: createWrapper(qc),
    });

    let promise!: Promise<void>;
    await act(async () => {
      promise = result.current.startRegenerate(regenerateVars);
    });

    stream.emit({ type: "user_message", message: msg(2, "user", "prompt") });
    stream.emit({ type: "delta", content: "new " });
    stream.emit({ type: "delta", content: "answer" });

    // NO optimistic change: old assistant row stays in cache while streaming
    await waitFor(() => {
      expect(result.current.streamingByChat.get(1)).toMatchObject({
        kind: "regenerate",
        targetMessageId: 3,
        text: "new answer",
      });
    });
    expect(messagesInCache(qc)).toEqual(chatMessages);

    stream.emit({
      type: "done",
      chat_id: 1,
      model_id: "m",
      user_message: msg(2, "user", "prompt"),
      assistant_message: {
        ...msg(4, "assistant", "new answer"),
        variant_group: 3,
        active: true,
        variant_index: 1,
        variant_count: 2,
      },
      deactivated_message_id: 3,
    });
    stream.close();
    await act(() => promise);

    // Variant contract: nothing is removed - the old row flips inactive and
    // the new active row is appended to the same group.
    const rows = messagesInCache(qc);
    expect(rows.map((m) => m.id)).toEqual([2, 3, 4]);
    const old = rows.find((m) => m.id === 3)!;
    expect(old.active).toBe(false);
    expect(old.variant_group).toBe(3);
    expect(old.content).toBe("old answer");
    const fresh = rows.find((m) => m.id === 4)!;
    expect(fresh.active).toBe(true);
    expect(fresh.variant_group).toBe(3);
    expect(result.current.streamingByChat.has(1)).toBe(false);
    expect(useErrorStore.getState().errors).toHaveLength(0);
  });

  it("error event keeps the old row and pushes a toast", async () => {
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), chatMessages);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/regenerate/stream": { response: () => stream.response },
    });

    const { result } = renderHook(() => useStreamingCompletion(), {
      wrapper: createWrapper(qc),
    });

    let promise!: Promise<void>;
    await act(async () => {
      promise = result.current.startRegenerate(regenerateVars);
    });

    stream.emit({ type: "delta", content: "doomed" });
    stream.emit({ type: "error", status: 429, code: "openrouter_rate_limited" });
    stream.close();
    await act(() => promise);

    // Old assistant row intact, partial discarded
    expect(messagesInCache(qc)).toEqual(chatMessages);
    expect(result.current.streamingByChat.has(1)).toBe(false);
    // Regenerate errors surface as a toast (single surface for regenerate)
    expect(useErrorStore.getState().errors).toHaveLength(1);
    expect(useErrorStore.getState().errors[0].code).toBe("openrouter_rate_limited");
  });

  it("abort is silent: old row intact, no toast", async () => {
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), chatMessages);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/regenerate/stream": { response: () => stream.response },
    });

    const { result } = renderHook(() => useStreamingCompletion(), {
      wrapper: createWrapper(qc),
    });

    let promise!: Promise<void>;
    await act(async () => {
      promise = result.current.startRegenerate(regenerateVars);
    });

    stream.emit({ type: "delta", content: "half an ans" });
    await waitFor(() => {
      expect(result.current.streamingByChat.get(1)?.text).toBe("half an ans");
    });

    act(() => {
      result.current.stop(1);
    });
    await act(() => promise);

    expect(messagesInCache(qc)).toEqual(chatMessages);
    expect(result.current.streamingByChat.has(1)).toBe(false);
    expect(useErrorStore.getState().errors).toHaveLength(0);
  });
});

describe("useStreamingCompletion - rAF delta batching", () => {
  // Deterministic frames: capture callbacks and run them manually so the
  // tests control exactly when a batch flushes.
  let scheduledFrames: Map<number, FrameRequestCallback>;
  let rafCalls: number;
  let nextFrameHandle: number;

  function stubAnimationFrames() {
    scheduledFrames = new Map();
    rafCalls = 0;
    nextFrameHandle = 1;
    vi.stubGlobal(
      "requestAnimationFrame",
      (callback: FrameRequestCallback): number => {
        rafCalls += 1;
        const handle = nextFrameHandle;
        nextFrameHandle += 1;
        scheduledFrames.set(handle, callback);
        return handle;
      },
    );
    vi.stubGlobal("cancelAnimationFrame", (handle: number): void => {
      scheduledFrames.delete(handle);
    });
  }

  function runFrames() {
    const callbacks = [...scheduledFrames.values()];
    scheduledFrames.clear();
    for (const callback of callbacks) callback(performance.now());
  }

  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("two deltas before a frame flush produce a single combined state update", async () => {
    stubAnimationFrames();
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    // Record every distinct non-empty streaming text a render observed -
    // batching means "Hel" alone must never appear.
    const seenTexts: string[] = [];
    const { result } = renderHook(
      () => {
        const hook = useStreamingCompletion();
        const text = hook.streamingByChat.get(1)?.text;
        if (
          text != null &&
          text.length > 0 &&
          seenTexts[seenTexts.length - 1] !== text
        ) {
          seenTexts.push(text);
        }
        return hook;
      },
      { wrapper: createWrapper(qc) },
    );

    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.startSend(sendVars);
    });

    // Deltas first, then user_message as an in-order consumption sentinel:
    // once id 5 is in the cache, both deltas have been handled.
    stream.emit({ type: "delta", content: "Hel" });
    stream.emit({ type: "delta", content: "lo" });
    stream.emit({ type: "user_message", message: msg(5, "user", "stream me") });
    await waitFor(() => {
      expect(messagesInCache(qc).some((m) => m.id === 5)).toBe(true);
    });

    // Both deltas accumulated behind ONE scheduled frame; nothing flushed yet
    expect(rafCalls).toBe(1);
    expect(result.current.streamingByChat.get(1)?.text).toBe("");

    act(() => {
      runFrames();
    });

    expect(result.current.streamingByChat.get(1)?.text).toBe("Hello");
    expect(seenTexts).toEqual(["Hello"]);

    stream.emit({
      type: "done",
      chat_id: 1,
      model_id: "m",
      user_message: msg(5, "user", "stream me"),
      assistant_message: msg(6, "assistant", "Hello"),
    });
    stream.close();
    await act(() => sendPromise);

    expect(messagesInCache(qc).map((m) => m.id)).toEqual([1, 5, 6]);
    expect(result.current.streamingByChat.has(1)).toBe(false);
    // No stray frame may fire after the entry is cleared (ghost entry guard)
    runFrames();
    expect(result.current.streamingByChat.has(1)).toBe(false);
  });

  it("abort mid-batch still persists the full accumulated partial", async () => {
    stubAnimationFrames();
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHook(() => useStreamingCompletion(), {
      wrapper: createWrapper(qc),
    });

    const onError = vi.fn();
    const onAbortedEmpty = vi.fn();
    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.startSend(sendVars, {
        onError,
        onAbortedEmpty,
      });
    });

    stream.emit({ type: "user_message", message: msg(5, "user", "stream me") });
    stream.emit({ type: "delta", content: "partial tex" });
    // Wait until the delta queued a frame - the frame deliberately NEVER runs,
    // so the streaming state still shows nothing when the abort hits.
    await waitFor(() => {
      expect(rafCalls).toBe(1);
    });
    expect(result.current.streamingByChat.get(1)?.text).toBe("");

    act(() => {
      result.current.stop(1);
    });
    await act(() => sendPromise);

    // The unflushed text still counts as a partial: user row kept, messages
    // refetched (backend persisted the partial), silent for the caller.
    expect(messagesInCache(qc).some((m) => m.id === 5)).toBe(true);
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: keys.messages(1) });
    expect(onError).not.toHaveBeenCalled();
    expect(onAbortedEmpty).not.toHaveBeenCalled();
    expect(useErrorStore.getState().errors).toHaveLength(0);
    expect(result.current.streamingByChat.has(1)).toBe(false);
  });
});

describe("useStreamingCompletion - attachments", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  /** Body of the streaming POST request the fetch stub received. */
  function streamRequestBody(
    mock: ReturnType<typeof mockFetchWithStreams>,
  ): Record<string, unknown> {
    const call = mock.mock.calls.find(
      ([url]) => typeof url === "string" && url.includes("/complete/stream"),
    );
    expect(call).toBeDefined();
    return JSON.parse((call![1] as RequestInit).body as string);
  }

  it("send includes attachment ids in the body and fires onPersisted on done", async () => {
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
    const stream = controlledSseResponse();
    const mock = mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHook(() => useStreamingCompletion(), {
      wrapper: createWrapper(qc),
    });

    const onPersisted = vi.fn();
    const onError = vi.fn();
    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.startSend(
        { ...sendVars, attachments: [11, 12] },
        { onPersisted, onError },
      );
    });

    const body = streamRequestBody(mock);
    expect(body.attachments).toEqual([11, 12]);
    expect(body.message).toBe("stream me");

    // Not persisted until the terminal event lands
    expect(onPersisted).not.toHaveBeenCalled();

    stream.emit({ type: "user_message", message: msg(5, "user", "stream me") });
    stream.emit({ type: "delta", content: "Hello" });
    stream.emit({
      type: "done",
      chat_id: 1,
      model_id: "m",
      user_message: msg(5, "user", "stream me"),
      assistant_message: msg(6, "assistant", "Hello"),
    });
    stream.close();
    await act(() => sendPromise);

    expect(onPersisted).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
  });

  it("send omits the attachments key when none are provided", async () => {
    const qc = newQueryClient();
    const stream = controlledSseResponse();
    const mock = mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHook(() => useStreamingCompletion(), {
      wrapper: createWrapper(qc),
    });

    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.startSend(sendVars);
    });

    expect(streamRequestBody(mock)).not.toHaveProperty("attachments");

    stream.emit({
      type: "done",
      chat_id: 1,
      model_id: "m",
      user_message: msg(5, "user", "stream me"),
      assistant_message: msg(6, "assistant", "Hello"),
    });
    stream.close();
    await act(() => sendPromise);
  });

  it("send omits the attachments key for an empty array", async () => {
    const qc = newQueryClient();
    const stream = controlledSseResponse();
    const mock = mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHook(() => useStreamingCompletion(), {
      wrapper: createWrapper(qc),
    });

    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.startSend({ ...sendVars, attachments: [] });
    });

    expect(streamRequestBody(mock)).not.toHaveProperty("attachments");

    stream.close();
    await act(() => sendPromise);
  });

  it("abort with partial text fires onPersisted (attachments consumed)", async () => {
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHook(() => useStreamingCompletion(), {
      wrapper: createWrapper(qc),
    });

    const onPersisted = vi.fn();
    const onAbortedEmpty = vi.fn();
    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.startSend(
        { ...sendVars, attachments: [7] },
        { onPersisted, onAbortedEmpty },
      );
    });

    stream.emit({ type: "user_message", message: msg(5, "user", "stream me") });
    stream.emit({ type: "delta", content: "partial" });
    await waitFor(() => {
      expect(result.current.streamingByChat.get(1)?.text).toBe("partial");
    });

    act(() => {
      result.current.stop(1);
    });
    await act(() => sendPromise);

    expect(onPersisted).toHaveBeenCalledTimes(1);
    expect(onAbortedEmpty).not.toHaveBeenCalled();
  });

  it("error event and abort-empty do NOT fire onPersisted", async () => {
    // Error event first
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
    let stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHook(() => useStreamingCompletion(), {
      wrapper: createWrapper(qc),
    });

    const onPersisted = vi.fn();
    const onError = vi.fn();
    const onAbortedEmpty = vi.fn();
    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.startSend(
        { ...sendVars, attachments: [7] },
        { onPersisted, onError, onAbortedEmpty },
      );
    });

    stream.emit({ type: "error", status: 400, code: "attachment_unavailable" });
    stream.close();
    await act(() => sendPromise);

    expect(onPersisted).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0]).toMatchObject({
      status: 400,
      detail: "attachment_unavailable",
    });

    // Abort before any text: silent cleanup, still not persisted
    stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });
    await act(async () => {
      sendPromise = result.current.startSend(
        { ...sendVars, attachments: [7] },
        { onPersisted, onError, onAbortedEmpty },
      );
    });
    await waitFor(() => {
      expect(result.current.streamingByChat.has(1)).toBe(true);
    });
    act(() => {
      result.current.stop(1);
    });
    await act(() => sendPromise);

    expect(onPersisted).not.toHaveBeenCalled();
    expect(onAbortedEmpty).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledTimes(1);
  });
});
