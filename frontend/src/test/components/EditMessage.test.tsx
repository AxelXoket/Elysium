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

  it("an error frame after done cannot undo the edit, but still reports one", async () => {
    // Two halves, and they landed differently than expected. Worth reading
    // before touching either.
    //
    // After the stream ends, startEdit checks `errorEvent != null` BEFORE it
    // checks `sawDone`, and calls restoreSnapshot(). That LOOKS like a late
    // error frame in the drain window rolls back an edit the server already
    // committed. It does not, and the guard is not where the ordering
    // suggests: restoreSnapshot is REVISION-guarded. It only writes the
    // snapshot back while the cache's dataUpdatedAt still equals the
    // revision the edit started from, and `done` has already moved it. So
    // the committed rows survive. That is the half this test protects: if
    // the revision check is ever dropped, the reader's new question and its
    // reply silently revert while the vault keeps the new text.
    //
    // The second half is worse, and measured: KUSUR-DEFTERI K-28. Whether
    // the rows survive is a RACE, not a rule. `done` settles the cache in
    // the background (`void qc.invalidateQueries(...)`), and the revision
    // check only wins if that has landed first. Run this scenario six times
    // and the committed exchange survived four and reverted twice.
    //
    // So the assertion below covers only the deterministic half: the toast.
    // Asserting the rows would make this test itself flaky, and a flaky test
    // cannot be a gate. The race is recorded in the ledger with its numbers
    // instead of being pinned by a coin flip.
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

    // Only the deterministic half is asserted here, on purpose. A successful
    // edit ends with a failure message on screen: pushError runs whenever an
    // error frame was seen, without asking whether `done` already landed.
    expect(
      useErrorStore.getState().errors.map((e) => e.code),
      "a committed edit reported itself as failed",
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
    // Revisions are millisecond-stamped - ensure the foreign write gets a
    // LATER stamp than the hook's own optimistic write.
    await new Promise((r) => setTimeout(r, 3));
    qc.setQueryData<Message[]>(keys.messages(1), foreign);

    act(() => {
      result.current.stop(1);
    });
    await act(() => editPromise);

    // The stale pre-edit snapshot must NOT overwrite the newer foreign data;
    // the rollback path falls back to invalidation instead.
    expect(messagesInCache(qc).map((m) => m.id)).toEqual([1, 9]);
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

  it("offers the pencil on an OLD user message, and warns about one reply", async () => {
    // CHARACTERISATION, not approval. See KUSUR-DEFTERI K-27.
    //
    // Editing is offered on EVERY user row, not just the last one, and the
    // backend deletes `id > message_id`, which is every message after it.
    // There is no confirmation step: Enter in the textarea fires it. The only
    // warning is this tooltip, and it says "the reply after it", singular,
    // for an action that on an old message discards the whole rest of the
    // conversation. Permanently: the delete is a hard SQL DELETE with no
    // trash and no undo.
    //
    // Pinned here so that the day a real warning arrives, this goes red.
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

    expect(pencils[0]).toHaveAttribute(
      "title",
      "Edit message (the reply after it is rewritten)",
    );

    await user.click(pencils[0]);
    // No confirmation stands between the reader and the deletion.
    expect(
      screen.queryByRole("dialog"),
      "an irreversible edit grew a confirmation step",
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "Edit message text" }),
    ).toHaveFocus();
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
