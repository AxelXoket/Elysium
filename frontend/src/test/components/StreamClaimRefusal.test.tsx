/**
 * What happens when the hook REFUSES to start a stream.
 *
 * `claimChat` returns false when a chat's slot is already owned by a stream
 * that is not in the post-`done` voice drain. Two of the three entry points
 * used to answer that with a bare `return`: no request, no callback, no
 * toast, and - on the send path - the user's sentence gone, because the
 * composer empties its own buffer before it calls in and the argument was
 * the only copy left.
 *
 * The window is real rather than theoretical. `stop` only calls `abort()`;
 * the controller is removed later, in an identity-guarded `finally` that on
 * the abort-empty path sits behind an awaited `DELETE /chats/{id}/messages/
 * {id}`. For the width of that request the chat still owns its slot and is
 * not yet draining, which is exactly the state these tests set up.
 *
 * All three doors are exercised in one file on purpose. `startEdit` already
 * carried its `onFailure`, so its case is the proof that the window is being
 * built correctly: if the setup were wrong the reference case would fail
 * too, and a green reference next to two red ones is what says the other two
 * were genuinely silent rather than merely unreached.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { waitFor, act } from "@testing-library/react";
import type { QueryClient } from "@tanstack/react-query";

import { useStreamingCompletion } from "@/lib/chat/useStreamingCompletion";
import { keys } from "@/lib/query/keys";
import { useErrorStore, getErrorMessage, parseApiError } from "@/lib/errors";
import {
  createTestQueryClient,
  renderHookWithQueryClient,
} from "@/test/helpers/renderWithQueryClient";
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

/** Count fetches whose URL contains `pattern`. */
function callsTo(mock: ReturnType<typeof mockFetchWithStreams>, pattern: string) {
  return mock.mock.calls.filter(([input]) =>
    (typeof input === "string" ? input : String(input)).includes(pattern),
  );
}

/**
 * The sentence a refused claim must carry.
 *
 * Imported, never retyped: this is the same lookup the production code and
 * the toast stack go through, so a re-worded catalogue entry moves all three
 * together instead of turning this file red for a copy-edit.
 */
const REFUSED_MESSAGE = getErrorMessage("unknown_error");

const SEND_ROUTE = "/complete/stream";
const REGENERATE_ROUTE = "/regenerate/stream";
const EDIT_ROUTE = "/edit/stream";

/**
 * Routes. Stream paths are listed FIRST because `mockFetchWithStreams`
 * matches by substring in insertion order, and `/chats/1/messages` is a
 * prefix of `/chats/1/messages/6/regenerate/stream`.
 */
function routesServing(streams: ReturnType<typeof controlledSseResponse>[]) {
  let served = 0;
  return {
    [SEND_ROUTE]: { response: () => streams[served++].response },
    [REGENERATE_ROUTE]: { response: () => streams[served++].response },
    [EDIT_ROUTE]: { response: () => streams[served++].response },
    // The abort-empty branch deletes the user row it learned the id of, and
    // that awaited round trip is the window this file measures.
    "/chats/1/messages": { response: () => jsonResponse({ deleted: 1 }) },
  };
}

/**
 * Drive a chat to LIVE-and-not-draining: a stream that has emitted text but
 * never `done`, so `drainingRef` does not hold this chat and `claimChat`
 * will refuse the next caller.
 */
async function liveStream(qc: QueryClient) {
  const streams = [controlledSseResponse(), controlledSseResponse()];
  const fetchMock = mockFetchWithStreams(routesServing(streams));

  const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
    client: qc,
  });

  let firstSend!: Promise<void>;
  await act(async () => {
    firstSend = result.current.startSend({
      chatId: 1,
      message: "first",
      modelId: "m",
    });
  });

  streams[0].emit({ type: "user_message", message: msg(5, "user", "first") });
  streams[0].emit({ type: "delta", content: "Hel" });
  await waitFor(() => {
    expect(result.current.streamingByChat.get(1)?.text).toBe("Hel");
  });

  return { result, streams, fetchMock, firstSend };
}

describe("a refused stream claim", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("tells the sender its message did not go", async () => {
    const qc = createTestQueryClient();
    const { result, fetchMock, firstSend } = await liveStream(qc);
    const sendsBefore = callsTo(fetchMock, SEND_ROUTE).length;

    const onError = vi.fn();
    const onAbortedEmpty = vi.fn();
    let second!: Promise<void>;
    // Stop and send in ONE synchronous block. `startSend` reaches its
    // claimChat guard before its first `await`, so this lands inside the
    // window rather than after the teardown has cleared the slot.
    act(() => {
      result.current.stop(1);
      second = result.current.startSend(
        { chatId: 1, message: "retyped", modelId: "m" },
        { onError, onAbortedEmpty },
      );
    });
    // waitFor rather than awaiting `second` first: a regression where the
    // claim is GRANTED leaves that promise parked on a stream nobody serves,
    // and the failure has to name the callback that never fired instead of
    // timing the whole case out with no reason attached.
    await waitFor(() => {
      expect(onError).toHaveBeenCalledTimes(1);
    });
    expect(parseApiError(onError.mock.calls[0][0]).message).toBe(
      REFUSED_MESSAGE,
    );
    // Refused, not merely reported: no second request went out. Without this
    // the assertion above could be satisfied by a send that was dispatched
    // and then failed for some ordinary reason.
    expect(callsTo(fetchMock, SEND_ROUTE)).toHaveLength(sendsBefore);
    // ONE door, not both. A refusal reported down BOTH channels is a
    // different bug wearing this one's green: `onAbortedEmpty` restores the
    // staged strip, and on the branch where the just-persisted user row is
    // deleted those ids are already dead, which is how blank tiles carrying
    // dead ids got back into the composer and the retry answered
    // attachment_not_found. Nothing was sent here, so nothing was aborted
    // empty either. The case below is the positive control for this line.
    expect(onAbortedEmpty).not.toHaveBeenCalled();

    // `second` resolves the moment the claim is refused; `firstSend` settles
    // on the abort that opened the window. Neither stream body needs closing:
    // the abort already tore the live one down.
    await act(() => second);
    await act(() => firstSend);
  });

  it("fires onAbortedEmpty when the claim is GRANTED and nothing streamed", async () => {
    // Positive control for the absence asserted above. Both spies are wired
    // the same way, through the same harness, on the same entry point, so a
    // green `not.toHaveBeenCalled` up there has to mean the callback stayed
    // silent rather than that this file could never have seen it fire.
    // `onError` is the mirror image: test one is its positive control.
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), []);
    const streams = [controlledSseResponse()];
    const fetchMock = mockFetchWithStreams(routesServing(streams));

    const { result } = renderHookWithQueryClient(
      () => useStreamingCompletion(),
      { client: qc },
    );

    const onError = vi.fn();
    const onAbortedEmpty = vi.fn();
    let send!: Promise<void>;
    await act(async () => {
      send = result.current.startSend(
        { chatId: 1, message: "only", modelId: "m" },
        { onError, onAbortedEmpty },
      );
    });

    // The user row is persisted, so the claim was granted and the id is
    // known - but no delta ever arrives before the stop.
    streams[0].emit({ type: "user_message", message: msg(5, "user", "only") });
    await waitFor(() => {
      expect(
        qc.getQueryData<Message[]>(keys.messages(1))?.some((m) => m.id === 5),
      ).toBe(true);
    });

    act(() => {
      result.current.stop(1);
    });
    await act(() => send);

    // Ground control: the request really went out, so the abort below is an
    // abort of something rather than a claim that was refused all along.
    expect(callsTo(fetchMock, SEND_ROUTE)).toHaveLength(1);
    expect(onAbortedEmpty).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
  });

  it("tells the user a refused regenerate did not run", async () => {
    const qc = createTestQueryClient();
    const { result, fetchMock, firstSend } = await liveStream(qc);

    let second!: Promise<void>;
    act(() => {
      result.current.stop(1);
      second = result.current.startRegenerate({
        chatId: 1,
        messageId: 6,
        anchor: 6,
        modelId: "m",
      });
    });
    // Regenerate has no caller-side failure channel, so the only place it
    // can speak is the toast stack.
    await waitFor(() => {
      expect(useErrorStore.getState().errors).toHaveLength(1);
    });
    const errors = useErrorStore.getState().errors;
    expect(errors[0].message).toBe(REFUSED_MESSAGE);
    // Keyed to the chat that refused, not to whichever chat is open.
    expect(errors[0].chatId).toBe(1);
    expect(callsTo(fetchMock, REGENERATE_ROUTE)).toHaveLength(0);

    // `second` resolves the moment the claim is refused; `firstSend` settles
    // on the abort that opened the window. Neither stream body needs closing:
    // the abort already tore the live one down.
    await act(() => second);
    await act(() => firstSend);
  });

  it("tells an editor its retyped text did not go (the reference door)", async () => {
    const qc = createTestQueryClient();
    const { result, fetchMock, firstSend } = await liveStream(qc);

    const onSuccess = vi.fn();
    const onFailure = vi.fn();
    let second!: Promise<void>;
    act(() => {
      result.current.stop(1);
      second = result.current.startEdit(
        { chatId: 1, messageId: 5, message: "retyped", modelId: "m" },
        { onSuccess, onFailure },
      );
    });
    await waitFor(() => {
      expect(onFailure).toHaveBeenCalledTimes(1);
    });
    expect(onSuccess).not.toHaveBeenCalled();
    expect(callsTo(fetchMock, EDIT_ROUTE)).toHaveLength(0);

    // `second` resolves the moment the claim is refused; `firstSend` settles
    // on the abort that opened the window. Neither stream body needs closing:
    // the abort already tore the live one down.
    await act(() => second);
    await act(() => firstSend);
  });

  it("stays quiet when nothing owns the chat", async () => {
    // Ground control for all three cases above. Every one of them asserts
    // that a callback fired or a toast appeared; this one asserts the same
    // instruments read ZERO on the ordinary path, so a fix that simply
    // reports failure on every call cannot pass this file.
    const qc = createTestQueryClient();
    const streams = [controlledSseResponse()];
    const fetchMock = mockFetchWithStreams(routesServing(streams));

    const { result } = renderHookWithQueryClient(
      () => useStreamingCompletion(),
      { client: qc },
    );

    const onError = vi.fn();
    const onAbortedEmpty = vi.fn();
    let send!: Promise<void>;
    await act(async () => {
      send = result.current.startSend(
        { chatId: 1, message: "only", modelId: "m" },
        { onError, onAbortedEmpty },
      );
    });

    streams[0].emit({ type: "user_message", message: msg(5, "user", "only") });
    streams[0].emit({ type: "delta", content: "Hi" });
    streams[0].emit({
      type: "done",
      chat_id: 1,
      model_id: "m",
      user_message: msg(5, "user", "only"),
      assistant_message: msg(6, "assistant", "Hi"),
    });
    streams[0].close();
    await act(() => send);

    expect(callsTo(fetchMock, SEND_ROUTE)).toHaveLength(1);
    expect(onError).not.toHaveBeenCalled();
    expect(onAbortedEmpty).not.toHaveBeenCalled();
    expect(useErrorStore.getState().errors).toHaveLength(0);
    expect(qc.getQueryData<Message[]>(keys.messages(1))?.map((m) => m.id)).toEqual(
      [5, 6],
    );
  });
});
