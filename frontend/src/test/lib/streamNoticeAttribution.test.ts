/**
 * streamNoticeAttribution.test.ts - a stream notice belongs to ONE chat.
 *
 * `notice` is the only frame that speaks about the request rather than the
 * reply: images the payload had to leave behind. It is pushed as a warning,
 * and the error store treats two events with the same code, the same chat and
 * the same partial-saved flag as one event on purpose (a retry storm must not
 * paint five identical toasts). That dedupe is why the chat id matters here:
 * with no chat on the event, a notice from the second conversation was
 * identical to the first one's and was dropped, so the disclosure the user
 * needed for chat B was spoken for by a toast sitting on chat A.
 *
 * Everything below drives the REAL hook over a REAL SSE body and then reads
 * the store. Nothing asserts on the source text of the hook.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, waitFor } from "@testing-library/react";
import {
  createTestQueryClient,
  renderHookWithQueryClient,
} from "@/test/helpers/renderWithQueryClient";
import { useStreamingCompletion } from "@/lib/chat/useStreamingCompletion";
import { useErrorStore, getCountMessage } from "@/lib/errors";
import type { ErrorEvent } from "@/lib/errors";
import { keys } from "@/lib/query/keys";
import { mockFetchWithStreams, controlledSseResponse } from "../helpers/streamMocks";
import type { Message } from "@/lib/schemas/chats";

/** The code the backend actually sends when the payload dropped pictures. */
const NOTICE_CODE = "images_omitted";

function msg(
  id: number,
  chatId: number,
  role: "user" | "assistant",
  content: string,
): Message {
  return {
    id,
    chat_id: chatId,
    role,
    content,
    created_at: "2026-01-01T00:00:00Z",
  };
}

type Hook = ReturnType<typeof useStreamingCompletion>;

/**
 * Run one complete send for `chatId` and emit `notice` frames along the way.
 *
 * Ids are derived from the chat id so two chats never collide in the cache,
 * and the promise is awaited to its `done` so the store is settled before the
 * assertions read it.
 */
async function sendWithNotices(
  hook: { current: Hook },
  chatId: number,
  notices: { code: string; count?: number }[],
) {
  const stream = controlledSseResponse();
  mockFetchWithStreams({
    [`/chats/${chatId}/complete/stream`]: { response: () => stream.response },
  });

  let sendPromise!: Promise<void>;
  await act(async () => {
    sendPromise = hook.current.startSend({
      chatId,
      message: "look at this",
      modelId: "m",
    });
  });
  await waitFor(() => {
    expect(hook.current.streamingByChat.has(chatId)).toBe(true);
  });

  for (const notice of notices) {
    stream.emit({ type: "notice", ...notice });
  }
  stream.emit({
    type: "done",
    chat_id: chatId,
    model_id: "m",
    user_message: msg(chatId * 10, chatId, "user", "look at this"),
    assistant_message: msg(chatId * 10 + 1, chatId, "assistant", "ok"),
  });
  stream.close();
  await act(() => sendPromise);
}

/** Every notice the store is holding, shown and queued alike. */
function noticesInStore(): ErrorEvent[] {
  const state = useErrorStore.getState();
  return [...state.errors, ...state.queuedErrors].filter(
    (e) => e.code === NOTICE_CODE,
  );
}

describe("a stream notice carries the chat it came from", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
  });

  it("two chats disclosing the same thing produce two notices, each on its own chat", async () => {
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), []);
    qc.setQueryData<Message[]>(keys.messages(2), []);
    const { result } = renderHookWithQueryClient(
      () => useStreamingCompletion(),
      { client: qc },
    );

    await sendWithNotices(result, 1, [{ code: NOTICE_CODE, count: 2 }]);
    await sendWithNotices(result, 2, [{ code: NOTICE_CODE, count: 2 }]);

    const notices = noticesInStore();
    // Two conversations, two disclosures. One survivor here means the second
    // chat's notice was dropped as a duplicate of the first chat's.
    expect(
      notices,
      "each chat's notice must survive the other chat's",
    ).toHaveLength(2);
    expect(notices.map((e) => e.chatId).sort()).toEqual([1, 2]);
    // The attribution has to be right, not merely present.
    expect(notices.find((e) => e.chatId === 1)).toBeDefined();
    expect(notices.find((e) => e.chatId === 2)).toBeDefined();
  });

  it("the surviving notice keeps the catalogue sentence and warning severity", async () => {
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), []);
    const { result } = renderHookWithQueryClient(
      () => useStreamingCompletion(),
      { client: qc },
    );

    await sendWithNotices(result, 1, [{ code: NOTICE_CODE, count: 3 }]);

    const notices = noticesInStore();
    expect(notices).toHaveLength(1);
    expect(notices[0].chatId).toBe(1);
    expect(notices[0].severity).toBe("warning");
    // Imported from the catalogue, never retyped: a sentence typed here would
    // pass while the reader saw something else, which is the whole reason the
    // counted forms were moved out of the hook.
    expect(notices[0].message).toBe(getCountMessage(NOTICE_CODE, 3));
  });

  it("GROUND CONTROL: one chat saying the same thing twice still collapses to one", async () => {
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), []);
    const { result } = renderHookWithQueryClient(
      () => useStreamingCompletion(),
      { client: qc },
    );

    await sendWithNotices(result, 1, [
      { code: NOTICE_CODE, count: 2 },
      { code: NOTICE_CODE, count: 2 },
    ]);

    // Same code, same chat, same partial flag: one event. Adding the chat id
    // must sharpen the dedupe, not disable it.
    expect(
      noticesInStore(),
      "a repeated notice from one chat must not paint a second toast",
    ).toHaveLength(1);
    expect(noticesInStore()[0].chatId).toBe(1);
  });

  it("POSITIVE CONTROL: the store really carries a chat id onto the event", () => {
    // If the fourth argument were dropped on the floor, every assertion above
    // that reads `.chatId` would be measuring nothing. This is the sweep
    // seeing the thing when it IS there.
    useErrorStore
      .getState()
      .pushErrorDirect(NOTICE_CODE, "sentence", "warning", { chatId: 7 });
    expect(useErrorStore.getState().errors[0].chatId).toBe(7);

    // And a push with no context leaves it undefined, so a chat id in the
    // tests above cannot have come from the store inventing one.
    useErrorStore.getState().clearAll();
    useErrorStore.getState().pushErrorDirect(NOTICE_CODE, "sentence", "warning");
    expect(useErrorStore.getState().errors[0].chatId).toBeUndefined();
  });
});
