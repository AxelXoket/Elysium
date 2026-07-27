/**
 * EditMessage.test.tsx - v1.1 C3 frontend: startEdit hook semantics + the
 * inline edit UI in MessageBubble + FF3 composer focus/Escape conventions.
 *
 * Server contract (mirrored here): the edit stream writes NOTHING until the
 * atomic swap at done - so abort/error must restore the pre-edit list, and
 * done replaces the edited row + appends the new reply.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, waitFor, act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), seed);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/messages/2/edit/stream": { response: () => stream.response },
    });

    const { result } = renderHook(() => useStreamingCompletion(), {
      wrapper: createWrapper(qc),
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

  it("abort restores the pre-edit snapshot silently (server wrote nothing)", async () => {
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), seed);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/messages/2/edit/stream": { response: () => stream.response },
    });

    const { result } = renderHook(() => useStreamingCompletion(), {
      wrapper: createWrapper(qc),
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
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), seed);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/messages/2/edit/stream": { response: () => stream.response },
    });

    const { result } = renderHook(() => useStreamingCompletion(), {
      wrapper: createWrapper(qc),
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
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), seed);
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/messages/2/edit/stream": { response: () => stream.response },
    });

    const { result } = renderHook(() => useStreamingCompletion(), {
      wrapper: createWrapper(qc),
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
  function wrapper({ children }: { children: ReactNode }) {
    const qc = newQueryClient();
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  }

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
    render(
      <MessageList chatId={1} onEditMessage={onEditMessage} />,
      { wrapper },
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
    render(
      <MessageList chatId={1} onEditMessage={vi.fn()} isPending />,
      { wrapper },
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
    const view = render(
      <MessageList chatId={1} onEditMessage={onEditMessage} />,
      { wrapper },
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
