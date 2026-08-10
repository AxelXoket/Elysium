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
import { waitFor, act } from "@testing-library/react";
import type { QueryClient } from "@tanstack/react-query";
import { createTestQueryClient, renderHookWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { useStreamingCompletion } from "@/lib/chat/useStreamingCompletion";
import { useStreamRegistry, stopChat } from "@/lib/chat/streamRegistry";
import { useClearChat, useDeleteChat } from "@/lib/query/chats";
import { keys } from "@/lib/query/keys";
import { useErrorStore } from "@/lib/errors";
import {
  mockFetchWithStreams,
  controlledSseResponse,
  jsonResponse,
} from "../helpers/streamMocks";
import type { Message } from "@/lib/schemas/chats";

function msg(id: number, role: "user" | "assistant", content: string): Message {
  return {
    id,
    chat_id: 1,
    role,
    content,
    created_at: "2026-01-01T00:00:00Z",
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
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
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
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
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

  it("KÖK 16: an error that saved the partial keeps the rows it committed", async () => {
    // The provider failed AFTER text arrived and the server kept it - the
    // same thing pressing Stop at that moment does. Mirroring the old
    // roll-back here would delete a reply the user is still looking at.
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
    });

    const onError = vi.fn();
    const onPersisted = vi.fn();
    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.startSend(sendVars, { onError, onPersisted });
    });

    stream.emit({ type: "user_message", message: msg(5, "user", "stream me") });
    stream.emit({ type: "delta", content: "half an answer" });
    await waitFor(() => {
      expect(result.current.streamingByChat.get(1)?.text).toBe("half an answer");
    });

    stream.emit({
      type: "error",
      status: 429,
      code: "openrouter_rate_limited",
      partial_saved: true,
    });
    stream.close();
    await act(() => sendPromise);

    // The user row STAYS - unlike the plain-error path above, which removes it.
    expect(messagesInCache(qc).map((m) => m.id)).toEqual([1, 5]);
    // And the failure is still reported: they keep what they read AND learn
    // why it stopped.
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0]).toMatchObject({
      status: 429,
      detail: "openrouter_rate_limited",
    });
    expect(onPersisted).toHaveBeenCalledTimes(1);
  });

  it("a notice arriving before the reply becomes a warning, not an error", async () => {
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
    });

    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.startSend(sendVars);
    });

    stream.emit({ type: "user_message", message: msg(5, "user", "stream me") });
    stream.emit({ type: "notice", code: "images_omitted", count: 2 });
    stream.emit({ type: "delta", content: "I see no image." });
    stream.emit({
      type: "done",
      chat_id: 1,
      model_id: "m",
      user_message: msg(5, "user", "stream me"),
      assistant_message: msg(6, "assistant", "I see no image."),
    });
    stream.close();
    await act(() => sendPromise);

    const [notice] = useErrorStore.getState().errors;
    expect(notice.code).toBe("images_omitted");
    expect(notice.severity).toBe("warning");
    expect(notice.message).toContain("2 images");
    // The reply itself succeeded - the notice must not look like a failure.
    expect(messagesInCache(qc).map((m) => m.id)).toEqual([1, 5, 6]);
  });

  it("abort with partial text: keeps user row and refetches messages", async () => {
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
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
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
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
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), chatMessages);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/regenerate/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
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
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), chatMessages);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/regenerate/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
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
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), chatMessages);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/regenerate/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
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
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    // Record every distinct non-empty streaming text a render observed -
    // batching means "Hel" alone must never appear.
    const seenTexts: string[] = [];
    const { result } = renderHookWithQueryClient(
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
      { client: qc },
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
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
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
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
    const stream = controlledSseResponse();
    const mock = mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
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
    const qc = createTestQueryClient();
    const stream = controlledSseResponse();
    const mock = mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
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
    const qc = createTestQueryClient();
    const stream = controlledSseResponse();
    const mock = mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
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
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
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
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
    let stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
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

// ── v1.1 Faz 1: ghost-message chain (D1/D3/I8) + stream registry (FF1/H7) ──

describe("useStreamingCompletion - ghost-message chain (v1.1)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
    useStreamRegistry.setState({ controllers: new Map() });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("D1: abort-empty deletes the persisted user row BEFORE invalidating; 404 is swallowed", async () => {
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
    const order: string[] = [];
    const invalidateOriginal = qc.invalidateQueries.bind(qc);
    vi.spyOn(qc, "invalidateQueries").mockImplementation((filters, opts) => {
      order.push("invalidate");
      return invalidateOriginal(filters as never, opts as never);
    });
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
      // The authority delete: answered with the ghost 404 - MUST be silent.
      "/messages/5": {
        response: () => {
          order.push("delete");
          return jsonResponse({ detail: "message_not_found" }, 404);
        },
      },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
    });

    const onAbortedEmpty = vi.fn();
    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.startSend(sendVars, { onAbortedEmpty });
    });

    stream.emit({ type: "user_message", message: msg(5, "user", "stream me") });
    await waitFor(() => {
      expect(messagesInCache(qc).some((m) => m.id === 5)).toBe(true);
    });

    act(() => {
      result.current.stop(1);
    });
    await act(() => sendPromise);

    // The DELETE fired, and BEFORE the messages invalidate (the whole point:
    // the refetch must not race the server's own lazy cleanup).
    expect(order).toContain("delete");
    expect(order.indexOf("delete")).toBeLessThan(order.lastIndexOf("invalidate"));
    // 404 swallowed - no "already deleted" toast, ghost gone from the cache.
    expect(useErrorStore.getState().errors).toHaveLength(0);
    expect(messagesInCache(qc).map((m) => m.id)).toEqual([1]);
    expect(onAbortedEmpty).toHaveBeenCalledTimes(1);
  });

  it("D3: done invalidates messages UNCONDITIONALLY (history already present)", async () => {
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]); // history loaded
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
    });

    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.startSend(sendVars);
    });
    stream.emit({ type: "user_message", message: msg(5, "user", "stream me") });
    stream.emit({ type: "delta", content: "Hi" });
    stream.emit({
      type: "done",
      chat_id: 1,
      model_id: "m",
      user_message: msg(5, "user", "stream me"),
      assistant_message: msg(6, "assistant", "Hi"),
    });
    stream.close();
    await act(() => sendPromise);

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: keys.messages(1) });
  });

  it("I8: abort-empty BEFORE user_message arms a 750ms one-shot resync", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
    const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
    });

    const onAbortedEmpty = vi.fn();
    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.startSend(sendVars, { onAbortedEmpty });
    });

    // Stop before ANY event: the server persisted a row under an id the
    // client never learned - the blind window.
    act(() => {
      result.current.stop(1);
    });
    await act(() => sendPromise);
    expect(onAbortedEmpty).toHaveBeenCalledTimes(1);

    const messageInvalidates = () =>
      invalidateSpy.mock.calls.filter(
        (c) =>
          JSON.stringify((c[0] as { queryKey?: unknown })?.queryKey) ===
          JSON.stringify(keys.messages(1)),
      ).length;

    const immediate = messageInvalidates();
    expect(immediate).toBeGreaterThanOrEqual(1);

    // The one-shot net fires at +750ms and settles the cache on server truth.
    act(() => {
      vi.advanceTimersByTime(750);
    });
    expect(messageInvalidates()).toBe(immediate + 1);

    // One-shot: no further firings.
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(messageInvalidates()).toBe(immediate + 1);
  });

  it("FF1/H7: the module registry tracks the stream and stopChat aborts it", async () => {
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedGreeting]);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
    });

    const onAbortedEmpty = vi.fn();
    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.startSend(sendVars, { onAbortedEmpty });
    });

    // Registered while in flight - visible OUTSIDE the hook instance.
    expect(useStreamRegistry.getState().controllers.has(1)).toBe(true);

    // stopChat (what useClearChat/useDeleteChat call in onMutate) aborts it.
    act(() => {
      stopChat(1);
    });
    await act(() => sendPromise);

    expect(onAbortedEmpty).toHaveBeenCalledTimes(1);
    expect(useStreamRegistry.getState().controllers.has(1)).toBe(false);
  });

  it("FF1: useClearChat/useDeleteChat abort the chat's stream in onMutate", async () => {
    const qc = createTestQueryClient();
    mockFetchWithStreams({
      "/chats/1/clear": { body: { ok: true, deleted_count: 2 } },
      "/chats/2": { body: { ok: true, deleted_count: 1 } },
    });

    const clearController = new AbortController();
    const deleteController = new AbortController();
    useStreamRegistry.setState({
      controllers: new Map([
        [1, clearController],
        [2, deleteController],
      ]),
    });

    const { result } = renderHookWithQueryClient(
      () => ({ clear: useClearChat(), del: useDeleteChat() }),
      { client: qc },
    );

    await act(async () => {
      result.current.clear.mutate(1);
    });
    expect(clearController.signal.aborted).toBe(true);

    await act(async () => {
      result.current.del.mutate(2);
    });
    expect(deleteController.signal.aborted).toBe(true);
  });
});
