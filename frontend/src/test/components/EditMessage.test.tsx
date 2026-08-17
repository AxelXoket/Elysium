/**
 * EditMessage.test.tsx - v1.1 C3 frontend: startEdit hook semantics + the
 * inline edit UI in MessageBubble + FF3 composer focus/Escape conventions.
 *
 * Server contract (mirrored here): the edit stream writes NOTHING until the
 * atomic swap at done - so abort/error must restore the pre-edit list, and
 * done replaces the edited row + appends the new reply.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { waitFor, act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { QueryClient } from "@tanstack/react-query";
import {
  createTestQueryClient,
  renderHookWithQueryClient,
  renderWithQueryClient,
} from "@/test/helpers/renderWithQueryClient";
import { useStreamingCompletion } from "@/lib/chat/useStreamingCompletion";
import { useStreamRegistry } from "@/lib/chat/streamRegistry";
import { keys } from "@/lib/query/keys";
import { useErrorStore } from "@/lib/errors";
import { useUiStore } from "@/lib/store/uiStore";
import { MessageList } from "@/components/chat/MessageList";
import { mockFetch } from "../mocks/api";
import {
  mockFetchWithStreams,
  controlledSseResponse,
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

// Chat: greeting, user question, old reply (the tail an edit discards).
const seed = [
  msg(1, "assistant", "greeting"),
  msg(2, "user", "original question"),
  msg(3, "assistant", "old reply"),
];

const editVars = {
  chatId: 1,
  messageId: 2,
  message: "edited question",
  modelId: "m",
};

describe("useStreamingCompletion - startEdit (v1.1 C3)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
    useStreamRegistry.setState({ controllers: new Map() });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("optimistically rewrites the row, hides the tail, and done lands the new exchange", async () => {
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), seed);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/messages/2/edit/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
    });

    let editPromise!: Promise<void>;
    await act(async () => {
      editPromise = result.current.startEdit(editVars);
    });

    // Optimistic: content replaced, tail (id 3) hidden, greeting intact.
    await waitFor(() => {
      const cached = messagesInCache(qc);
      expect(cached.map((m) => m.id)).toEqual([1, 2]);
      expect(cached[1].content).toBe("edited question");
    });

    stream.emit({
      type: "user_message",
      message: msg(2, "user", "edited question"),
    });
    stream.emit({ type: "delta", content: "New " });
    stream.emit({ type: "delta", content: "answer." });
    await waitFor(() => {
      expect(result.current.streamingByChat.get(1)?.text).toBe("New answer.");
      expect(result.current.streamingByChat.get(1)?.kind).toBe("edit");
    });

    stream.emit({
      type: "done",
      chat_id: 1,
      model_id: "m",
      user_message: msg(2, "user", "edited question"),
      assistant_message: msg(4, "assistant", "New answer."),
    });
    stream.close();
    await act(() => editPromise);

    const cached = messagesInCache(qc);
    expect(cached.map((m) => m.id)).toEqual([1, 2, 4]);
    expect(cached[1].content).toBe("edited question");
    expect(cached[2].content).toBe("New answer.");
    expect(result.current.streamingByChat.has(1)).toBe(false);
    expect(useErrorStore.getState().errors).toHaveLength(0);
  });

  it("an error frame after done neither undoes the edit nor reports one", async () => {
    // K-28, closed. This test used to pin the opposite of its own name.
    //
    // `done` leaves the stream body open for the voice drain, so a late
    // `error` frame can arrive after the exchange is committed server-side and
    // written to the cache. startEdit's terminal branch answered it by rolling
    // the edit back and pushing a toast - a successful edit ending with a
    // failure message on screen. The catch branch four lines below already
    // refused to do that and said why; the terminal branch had no such guard.
    //
    // BOTH halves are asserted now, and the second one is the reason this test
    // was worth rewriting rather than deleting:
    //
    //  - The toast is gone. Deterministic, and the whole point.
    //  - The rows survive. That USED to be a race: rollback was blocked only by
    //    restoreSnapshot's revision check, which compared `dataUpdatedAt`
    //    (millisecond resolution) and so only won if `done`'s background
    //    invalidate had already landed. Measured six runs: survived four,
    //    reverted twice, and the test could only assert the half that did not
    //    flip. The guard now compares the cached ARRAY IDENTITY, which changes
    //    on every write regardless of the clock, and the branch is gated on
    //    `sawDone` besides - so rollback is unreachable here by two independent
    //    routes and asserting the rows is no longer a coin flip.
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), seed);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/messages/2/edit/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
    });

    let editPromise!: Promise<void>;
    await act(async () => {
      editPromise = result.current.startEdit(editVars);
    });

    stream.emit({
      type: "user_message",
      message: msg(2, "user", "edited question"),
    });
    stream.emit({ type: "delta", content: "New answer." });
    stream.emit({
      type: "done",
      chat_id: 1,
      model_id: "m",
      user_message: msg(2, "user", "edited question"),
      assistant_message: msg(4, "assistant", "New answer."),
    });
    await waitFor(() => {
      expect(messagesInCache(qc).map((m) => m.id)).toEqual([1, 2, 4]);
    });

    // The exchange has landed. Now a late frame arrives in the drain window.
    stream.emit({ type: "error", status: 502, code: "openrouter_completion_error" });
    stream.close();
    await act(() => editPromise);

    expect(
      useErrorStore.getState().errors.map((e) => e.code),
      "a committed edit reported itself as failed",
    ).toEqual([]);
    // GROUND for that emptiness: the frame really was delivered and really was
    // ignored, rather than the stream having died before it arrived. Without
    // this, an edit that never streamed at all would satisfy the line above.
    expect(
      messagesInCache(qc).map((m) => m.id),
      "the committed exchange was rolled back by a late error frame",
    ).toEqual([1, 2, 4]);
    expect(
      messagesInCache(qc).find((m) => m.id === 2)?.content,
      "the edited text reverted to what it was before the edit",
    ).toBe("edited question");
  });

  it("still reports an error frame that arrives BEFORE done", async () => {
    // The discriminating half of the test above, and the reason `!sawDone` is
    // a guard rather than a silencer. Same stream, same frame, one difference:
    // `done` never lands. The toast must appear and the rows must go back.
    //
    // Without this pair, gating the branch on `sawDone` could be replaced by
    // deleting it outright and both tests would still pass.
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), seed);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/messages/2/edit/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
    });

    let editPromise!: Promise<void>;
    await act(async () => {
      editPromise = result.current.startEdit(editVars);
    });

    stream.emit({
      type: "user_message",
      message: msg(2, "user", "edited question"),
    });
    stream.emit({ type: "delta", content: "Half an ans" });
    stream.emit({ type: "error", status: 502, code: "openrouter_completion_error" });
    stream.close();
    await act(() => editPromise);

    expect(
      useErrorStore.getState().errors.map((e) => e.code),
      "a failure before done went unreported",
    ).toEqual(["openrouter_completion_error"]);
  });

  it("abort restores the pre-edit snapshot silently (server wrote nothing)", async () => {
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), seed);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/messages/2/edit/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
    });

    let editPromise!: Promise<void>;
    await act(async () => {
      editPromise = result.current.startEdit(editVars);
    });
    stream.emit({
      type: "user_message",
      message: msg(2, "user", "edited question"),
    });
    stream.emit({ type: "delta", content: "Half a rep" });
    await waitFor(() => {
      expect(result.current.streamingByChat.get(1)?.text).toBe("Half a rep");
    });

    act(() => {
      result.current.stop(1);
    });
    await act(() => editPromise);

    // Byte-identical restore: original content AND the old tail are back.
    const cached = messagesInCache(qc);
    expect(cached.map((m) => m.id)).toEqual([1, 2, 3]);
    expect(cached[1].content).toBe("original question");
    expect(cached[2].content).toBe("old reply");
    expect(useErrorStore.getState().errors).toHaveLength(0);
  });

  it("edit_conflict error event restores the snapshot and pushes a toast", async () => {
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), seed);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/messages/2/edit/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
    });

    let editPromise!: Promise<void>;
    await act(async () => {
      editPromise = result.current.startEdit(editVars);
    });
    stream.emit({
      type: "user_message",
      message: msg(2, "user", "edited question"),
    });
    stream.emit({ type: "error", status: 409, code: "edit_conflict" });
    stream.close();
    await act(() => editPromise);

    const cached = messagesInCache(qc);
    expect(cached.map((m) => m.id)).toEqual([1, 2, 3]);
    expect(cached[1].content).toBe("original question");
    expect(useErrorStore.getState().errors).toHaveLength(1);
  });

  it("I14: a foreign cache write during the stream is not clobbered by the rollback", async () => {
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), seed);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/messages/2/edit/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
    });

    let editPromise!: Promise<void>;
    await act(async () => {
      editPromise = result.current.startEdit(editVars);
    });
    await waitFor(() => {
      expect(messagesInCache(qc).map((m) => m.id)).toEqual([1, 2]);
    });

    // Foreign write lands mid-stream (e.g. a refetch from another mutation).
    const foreign = [msg(1, "assistant", "greeting"), msg(9, "user", "foreign row")];
    // No sleep here any more, and its absence is the point (K-28(b)). This
    // line used to be preceded by `await new Promise(r => setTimeout(r, 3))`,
    // because the guard compared millisecond-stamped revisions and a foreign
    // write landing in the same millisecond as the hook's own read as "still
    // ours". A test that has to wait for the clock to tick over is a test
    // measuring the clock. The guard compares the cached array identity now,
    // which setQueryData changes on every write, so this needs no delay and
    // cannot come out differently on a faster machine.
    qc.setQueryData<Message[]>(keys.messages(1), foreign);

    act(() => {
      result.current.stop(1);
    });
    await act(() => editPromise);

    // The stale pre-edit snapshot must NOT overwrite the newer foreign data;
    // the rollback path falls back to invalidation instead.
    expect(messagesInCache(qc).map((m) => m.id)).toEqual([1, 9]);
  });

  it("I14 holds even when the foreign write lands in the same millisecond", async () => {
    // K-28(b), made deterministic. The test above cannot tell the two guards
    // apart: on a real clock the foreign write usually gets a later stamp, so
    // the OLD millisecond comparison passes it too - measured, five runs, five
    // passes. The failure it was hiding only appears when both writes fall in
    // the same millisecond, which is a coin toss on a fast machine and was
    // previously papered over with a 3ms sleep.
    //
    // Freezing the clock turns that coin toss into a certainty. TanStack Query
    // stamps dataUpdatedAt with Date.now(), so with it frozen every write
    // carries the SAME revision and a guard built on revisions sees the foreign
    // write as its own and overwrites it. Array identity is unaffected: every
    // setQueryData produces a new array whatever the clock says.
    //
    // vi.spyOn rather than fake timers on purpose - fake timers would also
    // freeze the query client's own scheduling, so the test would stop
    // exercising the path it is about.
    const frozen = Date.now();
    vi.spyOn(Date, "now").mockReturnValue(frozen);

    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), seed);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/messages/2/edit/stream": { response: () => stream.response },
    });

    const { result } = renderHookWithQueryClient(() => useStreamingCompletion(), {
      client: qc,
    });

    let editPromise!: Promise<void>;
    await act(async () => {
      editPromise = result.current.startEdit(editVars);
    });
    await waitFor(() => {
      expect(messagesInCache(qc).map((m) => m.id)).toEqual([1, 2]);
    });

    // GROUND: the clock really is frozen, so the two writes below genuinely
    // share a revision. Without this the test could pass on a clock that
    // ticked normally and would prove nothing about the same-millisecond case.
    const beforeStamp = qc.getQueryState(keys.messages(1))?.dataUpdatedAt;
    const foreign = [msg(1, "assistant", "greeting"), msg(9, "user", "foreign row")];
    qc.setQueryData<Message[]>(keys.messages(1), foreign);
    expect(
      qc.getQueryState(keys.messages(1))?.dataUpdatedAt,
      "the clock was not frozen, so this test is not measuring the collision",
    ).toBe(beforeStamp);

    act(() => {
      result.current.stop(1);
    });
    await act(() => editPromise);

    expect(
      messagesInCache(qc).map((m) => m.id),
      "the rollback clobbered a foreign write that shared its revision",
    ).toEqual([1, 9]);
  });
});

describe("MessageBubble inline edit UI (v1.1 C3)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    // v1.1 audit L1: the edit pencil is now gated on a selected model.
    useUiStore.setState({ selectedModelId: "openai/gpt-4o" });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedModelId: null });
  });

  function renderList(onEditMessage = vi.fn()) {
    mockFetch({
      "/chats/1/messages": { body: seed },
    });
    renderWithQueryClient(
      <MessageList chatId={1} onEditMessage={onEditMessage} />,
    );
    return onEditMessage;
  }

  // v1.1 audit L1: with no model selected the pencil is disabled, so a
  // retyped edit can never be silently discarded (parity with send/regenerate).
  it("L1: the edit pencil is disabled when no model is selected", async () => {
    useUiStore.setState({ selectedModelId: null });
    renderList();
    await screen.findByText("original question");
    const pencil = screen.getByRole("button", { name: "Edit message" });
    expect(pencil).toBeDisabled();
    expect(pencil).toHaveAttribute("title", "Select a model to edit");
  });

  it("offers the pencil on an OLD user message, and counts what a save would delete", async () => {
    // K-27, closed. This was a CHARACTERISATION test and its own note said
    // "pinned here so that the day a real warning arrives, this goes red".
    // That day arrived; the test is rewritten rather than deleted, because two
    // of its three claims are still exactly right and worth keeping.
    //
    // What has NOT changed, and must not: editing is still offered on every
    // user row, and clicking the pencil still opens the box with no dialog in
    // the way. Opening the box destroys nothing - the confirmation belongs on
    // the save, and putting it on the pencil would ask the question before the
    // reader had decided anything.
    //
    // What HAS changed: the tooltip no longer says "the reply", singular, when
    // four messages are about to go.
    const user = userEvent.setup();
    mockFetch({
      "/chats/1/messages": {
        body: [
          msg(1, "assistant", "greeting"),
          msg(2, "user", "original question"),
          msg(3, "assistant", "old reply"),
          msg(4, "user", "a later question"),
          msg(5, "assistant", "a later reply"),
        ],
      },
    });
    renderWithQueryClient(<MessageList chatId={1} onEditMessage={vi.fn()} />);
    await screen.findByText("a later reply");

    // Both user rows are editable, including the one with four messages after.
    const pencils = screen.getAllByRole("button", { name: "Edit message" });
    expect(pencils).toHaveLength(2);

    // The first pencil has three rows behind it (3, 4, 5) and says so; the
    // last one has one and keeps the singular. Both are asserted, because a
    // tooltip that always printed a number would be just as wrong.
    expect(pencils[0]).toHaveAttribute(
      "title",
      "Edit message (the 3 messages after it are deleted)",
    );
    expect(pencils[1]).toHaveAttribute(
      "title",
      "Edit message (the reply after it is rewritten)",
    );

    await user.click(pencils[0]);
    // Still nothing in the way of OPENING the box - the question comes at save.
    expect(
      screen.queryByRole("dialog"),
      "the confirmation moved onto the pencil, where nothing is destroyed yet",
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "Edit message text" }),
    ).toHaveFocus();
  });

  it("K-27: saving an edit with several messages behind it asks, and says how many", async () => {
    const user = userEvent.setup();
    const onEditMessage = vi.fn();
    mockFetch({
      "/chats/1/messages": {
        body: [
          msg(1, "assistant", "greeting"),
          msg(2, "user", "original question"),
          msg(3, "assistant", "old reply"),
          msg(4, "user", "a later question"),
          msg(5, "assistant", "a later reply"),
        ],
      },
    });
    renderWithQueryClient(<MessageList chatId={1} onEditMessage={onEditMessage} />);
    await screen.findByText("a later reply");

    await user.click(screen.getAllByRole("button", { name: "Edit message" })[0]);
    const textarea = screen.getByRole("textbox", { name: "Edit message text" });
    await user.clear(textarea);
    await user.type(textarea, "a different question");
    await user.click(screen.getByRole("button", { name: "Save" }));

    // Named, not queried by role alone: the delete panel is also role="dialog"
    // and lives in the same list, so a bare getByRole could match the wrong
    // panel and pass.
    const panel = screen.getByRole("dialog", {
      name: "Confirm rewriting this message",
    });
    // The count, and it must be the SERVER's count - rows 3, 4 and 5 all go.
    expect(panel).toHaveTextContent("3 messages");
    expect(
      onEditMessage,
      "the edit was dispatched before the reader answered",
    ).not.toHaveBeenCalled();
    // The box and the retyped text are still there behind the question.
    expect(screen.getByRole("textbox", { name: "Edit message text" })).toHaveValue(
      "a different question",
    );

    await user.click(screen.getByRole("button", { name: "Save and delete" }));
    expect(onEditMessage).toHaveBeenCalledTimes(1);
    expect(onEditMessage).toHaveBeenCalledWith(2, "a different question");
  });

  it("K-27: cancelling the confirmation keeps the box, the text and the messages", async () => {
    const user = userEvent.setup();
    const onEditMessage = vi.fn();
    mockFetch({
      "/chats/1/messages": {
        body: [
          msg(1, "assistant", "greeting"),
          msg(2, "user", "original question"),
          msg(3, "assistant", "old reply"),
          msg(4, "user", "a later question"),
          msg(5, "assistant", "a later reply"),
        ],
      },
    });
    renderWithQueryClient(<MessageList chatId={1} onEditMessage={onEditMessage} />);
    await screen.findByText("a later reply");

    await user.click(screen.getAllByRole("button", { name: "Edit message" })[0]);
    const textarea = screen.getByRole("textbox", { name: "Edit message text" });
    await user.clear(textarea);
    await user.type(textarea, "second thoughts");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("dialog", { name: "Confirm rewriting this message" });

    await user.click(screen.getByRole("button", { name: "Go back" }));

    expect(onEditMessage).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("dialog", { name: "Confirm rewriting this message" }),
    ).not.toBeInTheDocument();
    // The lesson the blocked-save branch was built around: a refusal must never
    // cost the reader the words they retyped.
    expect(screen.getByRole("textbox", { name: "Edit message text" })).toHaveValue(
      "second thoughts",
    );
    expect(screen.getByText("a later reply")).toBeInTheDocument();
  });

  it("K-27: editing the LAST question saves straight away, with nothing to ask about", async () => {
    // The discriminating half, and the reason the threshold is above ONE.
    // Rewriting the last question really does rewrite one reply, and a dialog
    // for that is the kind that teaches people to click through dialogs.
    // Without this, `> 1` and `> 0` look identical from the test suite.
    const user = userEvent.setup();
    const onEditMessage = renderList();

    await screen.findByText("original question");
    await user.click(screen.getByRole("button", { name: "Edit message" }));
    const textarea = screen.getByRole("textbox", { name: "Edit message text" });
    await user.clear(textarea);
    await user.type(textarea, "asked differently");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onEditMessage).toHaveBeenCalledWith(2, "asked differently");
  });

  it("K-27: inactive variants are counted, because the server deletes them too", async () => {
    // The count has to be the SERVER's. Its sweep is `id > message_id`, which
    // takes inactive variant siblings with it - so a reply the reader browsed
    // away from still goes. Deriving the number from the ACTIVE rows would
    // show two where three are destroyed, which is a worse warning than none.
    const user = userEvent.setup();
    const group = { variant_group: 3, variant_count: 2, chat_id: 1 };
    mockFetch({
      "/chats/1/messages": {
        body: [
          msg(2, "user", "original question"),
          { ...msg(3, "assistant", "take one"), ...group, active: false, variant_index: 0 },
          { ...msg(4, "assistant", "take two"), ...group, active: true, variant_index: 1 },
        ],
      },
    });
    renderWithQueryClient(<MessageList chatId={1} onEditMessage={vi.fn()} />);
    await screen.findByText("take two");
    // GROUND: only ONE of the two takes is on screen, so a count taken from
    // what is rendered would say one and this test would be measuring that
    // mistake rather than the fix.
    expect(screen.queryByText("take one")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Edit message" }));
    const textarea = screen.getByRole("textbox", { name: "Edit message text" });
    await user.clear(textarea);
    await user.type(textarea, "changed");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(
      screen.getByRole("dialog", { name: "Confirm rewriting this message" }),
    ).toHaveTextContent("2 messages");
  });

  it("pencil shows only on user bubbles; Save fires onEditMessage with trimmed text", async () => {
    const user = userEvent.setup();
    const onEditMessage = renderList();

    await screen.findByText("original question");
    // Exactly ONE edit button - the single user row (not greeting/reply).
    const editButtons = screen.getAllByRole("button", { name: "Edit message" });
    expect(editButtons).toHaveLength(1);

    await user.click(editButtons[0]);
    const textarea = screen.getByRole("textbox", { name: "Edit message text" });
    expect(textarea).toHaveFocus();

    await user.clear(textarea);
    await user.type(textarea, "  rewritten question  ");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(onEditMessage).toHaveBeenCalledWith(2, "rewritten question");
    // Edit mode closed.
    expect(
      screen.queryByRole("textbox", { name: "Edit message text" }),
    ).not.toBeInTheDocument();
  });

  it("Enter on an emptied edit box does not wipe the turn", async () => {
    // The disabled Save button is not the guard that matters here. Somebody
    // who clears the box meaning to cancel, then presses Enter out of habit,
    // goes straight through handleEditKeyDown to saveEdit and never touches
    // the button. If the empty check there were lost, the turn would be
    // rewritten to nothing AND every message after it deleted, with no
    // confirmation and no undo. The existing "unchanged or empty text cancels
    // silently" test only ever checked that the button was disabled.
    const user = userEvent.setup();
    const onEditMessage = renderList();

    await screen.findByText("original question");
    await user.click(screen.getByRole("button", { name: "Edit message" }));
    const box = screen.getByRole("textbox", { name: "Edit message text" });
    await user.clear(box);
    await user.keyboard("{Enter}");

    expect(
      onEditMessage,
      "an emptied box saved itself over the message",
    ).not.toHaveBeenCalled();

    // Same for a box holding nothing but spaces.
    await user.type(box, "   ");
    await user.keyboard("{Enter}");
    expect(onEditMessage).not.toHaveBeenCalled();
  });

  it("Enter saves, Shift+Enter does not, Escape cancels without firing", async () => {
    const user = userEvent.setup();
    const onEditMessage = renderList();

    await screen.findByText("original question");
    await user.click(screen.getByRole("button", { name: "Edit message" }));
    const textarea = screen.getByRole("textbox", { name: "Edit message text" });

    // Shift+Enter = newline, still editing, no call.
    await user.type(textarea, "{Shift>}{Enter}{/Shift}more");
    expect(onEditMessage).not.toHaveBeenCalled();

    // Escape = cancel, no call, original text still rendered.
    await user.keyboard("{Escape}");
    expect(onEditMessage).not.toHaveBeenCalled();
    expect(screen.getByText("original question")).toBeInTheDocument();

    // Re-enter and save via Enter.
    await user.click(screen.getByRole("button", { name: "Edit message" }));
    const ta2 = screen.getByRole("textbox", { name: "Edit message text" });
    await user.clear(ta2);
    await user.type(ta2, "via enter{Enter}");
    expect(onEditMessage).toHaveBeenCalledWith(2, "via enter");
  });

  it("unchanged or empty text cancels silently without firing", async () => {
    const user = userEvent.setup();
    const onEditMessage = renderList();

    await screen.findByText("original question");
    // Unchanged → no call.
    await user.click(screen.getByRole("button", { name: "Edit message" }));
    await user.keyboard("{Enter}");
    expect(onEditMessage).not.toHaveBeenCalled();

    // Emptied → Save disabled.
    await user.click(screen.getByRole("button", { name: "Edit message" }));
    const textarea = screen.getByRole("textbox", { name: "Edit message text" });
    await user.clear(textarea);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("edit button is absent while the chat is pending", async () => {
    mockFetch({
      "/chats/1/messages": { body: seed },
    });
    renderWithQueryClient(
      <MessageList chatId={1} onEditMessage={vi.fn()} isPending />,
    );
    await screen.findByText("original question");
    const bubble = screen
      .getByText("original question")
      .closest(".message-bubble-shell") as HTMLElement;
    expect(
      within(bubble).getByRole("button", { name: "Edit message" }),
    ).toBeDisabled();
  });

  // ── Audit HIGH: the box can become un-savable while it is already OPEN ──
  //
  // The Save button was gated only on emptiness. startEdit early-returns with
  // no error when a stream owns the chat, and handleEditMessage bails with no
  // model - but saveEdit had already closed the box and dropped the draft, so
  // the retyped text was destroyed with no request, no toast and no diagnosis.

  it("Save is refused (and the retyped text kept) once the chat turns busy", async () => {
    const user = userEvent.setup();
    const onEditMessage = vi.fn();
    mockFetch({ "/chats/1/messages": { body: seed } });
    const view = renderWithQueryClient(
      <MessageList chatId={1} onEditMessage={onEditMessage} />,
    );

    await screen.findByText("original question");
    await user.click(screen.getByRole("button", { name: "Edit message" }));
    const textarea = screen.getByRole("textbox", { name: "Edit message text" });
    await user.clear(textarea);
    await user.type(textarea, "edited ONE");

    // A stream starts from elsewhere while this box is open.
    view.rerender(
      <MessageList chatId={1} onEditMessage={onEditMessage} isPending />,
    );

    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    expect(save).toHaveAttribute("title", "Wait for the current reply to finish");

    // Enter must not silently discard it either.
    await user.type(
      screen.getByRole("textbox", { name: "Edit message text" }),
      "{Enter}",
    );
    expect(onEditMessage).not.toHaveBeenCalled();
    expect(
      screen.getByRole("textbox", { name: "Edit message text" }),
    ).toHaveValue("edited ONE");
  });

  it("Save is refused when the model is deselected mid-edit", async () => {
    const user = userEvent.setup();
    const onEditMessage = vi.fn();
    renderList(onEditMessage);

    await screen.findByText("original question");
    await user.click(screen.getByRole("button", { name: "Edit message" }));
    const textarea = screen.getByRole("textbox", { name: "Edit message text" });
    await user.clear(textarea);
    await user.type(textarea, "still here");

    act(() => {
      useUiStore.setState({ selectedModelId: null });
    });

    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    expect(save).toHaveAttribute("title", "Select a model to save this edit");
    await user.type(
      screen.getByRole("textbox", { name: "Edit message text" }),
      "{Enter}",
    );
    expect(onEditMessage).not.toHaveBeenCalled();
    expect(
      screen.getByRole("textbox", { name: "Edit message text" }),
    ).toHaveValue("still here");
  });
});
