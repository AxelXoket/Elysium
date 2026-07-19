import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatList } from "@/components/sidebar/ChatList";
import { useUiStore } from "@/lib/store/uiStore";
import { useErrorStore } from "@/lib/errors";
import { mockFetch } from "../mocks/api";
import { mockFetchWithStreams, jsonResponse } from "../helpers/streamMocks";
import { chatFixture } from "../mocks/fixtures";
import type { Chat } from "@/lib/schemas/chats";
import type { ReactNode } from "react";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("ChatList", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedCharacterId: 1,
      selectedChatId: null,
    });
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // T-24: Chat list renders for selected character
  it("T-24: renders chats for selected character", async () => {
    mockFetch({
      "/chats": { body: [chatFixture] },
    });

    render(<ChatList />, { wrapper });

    expect(await screen.findByText("Chats")).toBeInTheDocument();
    expect(await screen.findByText("Test Chat")).toBeInTheDocument();
    expect(await screen.findByText(/1 msg/)).toBeInTheDocument();
  });

  // T-25: No character selected shows placeholder
  it("T-25: shows placeholder when no character selected", async () => {
    useUiStore.setState({ selectedCharacterId: null });

    render(<ChatList />, { wrapper });

    expect(screen.getByText("Select a character first")).toBeInTheDocument();
  });

  // T-26: Empty chat list for selected character shows empty state
  it("T-26: shows empty state when no chats exist", async () => {
    mockFetch({
      "/chats": { body: [] },
    });

    render(<ChatList />, { wrapper });

    expect(await screen.findByText("No chats yet")).toBeInTheDocument();
  });

  it("FE-5B: shows compact chat action menu", async () => {
    const user = userEvent.setup();
    mockFetch({
      "/chats": { body: [chatFixture] },
    });

    render(<ChatList />, { wrapper });

    await screen.findByText("Test Chat");
    await user.click(
      screen.getByRole("button", { name: /open chat actions for test chat/i }),
    );

    expect(screen.getByRole("menuitem", { name: /clear chat/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /delete chat/i })).toBeInTheDocument();
  });

  it("FE-5B: clear chat asks confirmation and cancel does not mutate", async () => {
    const user = userEvent.setup();
    const mock = mockFetch({
      "/chats/1/clear": { body: { ok: true, deleted_count: 2 } },
      "/chats": { body: [chatFixture] },
    });

    render(<ChatList />, { wrapper });

    await screen.findByText("Test Chat");
    await user.click(
      screen.getByRole("button", { name: /open chat actions for test chat/i }),
    );
    await user.click(screen.getByRole("menuitem", { name: /clear chat/i }));

    expect(screen.getByText("Clear all messages in this chat?")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(
      mock.mock.calls.some(
        ([url, init]) =>
          String(url).includes("/chats/1/clear") &&
          (init as RequestInit | undefined)?.method === "POST",
      ),
    ).toBe(false);
  });

  it("FE-5B: confirm clear chat calls clear mutation", async () => {
    const user = userEvent.setup();
    const mock = mockFetch({
      "/chats/1/clear": { body: { ok: true, deleted_count: 2 } },
      "/chats": { body: [chatFixture] },
    });

    render(<ChatList />, { wrapper });

    await screen.findByText("Test Chat");
    await user.click(
      screen.getByRole("button", { name: /open chat actions for test chat/i }),
    );
    await user.click(screen.getByRole("menuitem", { name: /clear chat/i }));
    await user.click(screen.getByRole("button", { name: "Clear" }));

    await waitFor(() => {
      expect(
        mock.mock.calls.some(
          ([url, init]) =>
            String(url).includes("/chats/1/clear") &&
            (init as RequestInit | undefined)?.method === "POST",
        ),
      ).toBe(true);
    });
  });

  it("FE-5B: delete chat asks confirmation and confirm deletes selected chat", async () => {
    const user = userEvent.setup();
    useUiStore.setState({ selectedChatId: chatFixture.id });
    const mock = mockFetch({
      "/chats/1": { body: { ok: true } },
      "/chats": { body: [chatFixture] },
    });

    render(<ChatList />, { wrapper });

    await screen.findByText("Test Chat");
    await user.click(
      screen.getByRole("button", { name: /open chat actions for test chat/i }),
    );
    await user.click(screen.getByRole("menuitem", { name: /delete chat/i }));

    expect(screen.getByText("Delete this chat permanently?")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(
        mock.mock.calls.some(
          ([url, init]) =>
            String(url).endsWith("/chats/1") &&
            (init as RequestInit | undefined)?.method === "DELETE",
        ),
      ).toBe(true);
      expect(useUiStore.getState().selectedChatId).toBeNull();
    });
  });

  it("FE-5B: chat load error uses safe mapped message", async () => {
    mockFetch({
      "/chats": { status: 500, body: { detail: "UPSTREAM_RAW_SECRET" } },
    });

    render(<ChatList />, { wrapper });

    expect(
      await screen.findByText("Something went wrong. Please try again."),
    ).toBeInTheDocument();
    expect(screen.queryByText("UPSTREAM_RAW_SECRET")).not.toBeInTheDocument();
  });

  // FIX-8: menu a11y - Escape closes and returns focus to the trigger
  it("FIX-8: Escape closes the menu and refocuses the trigger", async () => {
    const user = userEvent.setup();
    mockFetch({
      "/chats": { body: [chatFixture] },
    });

    render(<ChatList />, { wrapper });

    await screen.findByText("Test Chat");
    const trigger = screen.getByRole("button", {
      name: /open chat actions for test chat/i,
    });
    await user.click(trigger);
    expect(screen.getByRole("menu")).toBeInTheDocument();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  // FIX-8: menu a11y - clicking outside closes the menu
  it("FIX-8: outside click closes the menu", async () => {
    const user = userEvent.setup();
    mockFetch({
      "/chats": { body: [chatFixture] },
    });

    render(<ChatList />, { wrapper });

    await screen.findByText("Test Chat");
    await user.click(
      screen.getByRole("button", { name: /open chat actions for test chat/i }),
    );
    expect(screen.getByRole("menu")).toBeInTheDocument();

    // Click something outside the chat list item
    await user.click(screen.getByText("Chats"));

    await waitFor(() => {
      expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    });
  });

  // FIX-8: destructive confirm autofocuses its confirm button
  it("FIX-8: inline confirm autofocuses the destructive button", async () => {
    const user = userEvent.setup();
    mockFetch({
      "/chats": { body: [chatFixture] },
    });

    render(<ChatList />, { wrapper });

    await screen.findByText("Test Chat");
    await user.click(
      screen.getByRole("button", { name: /open chat actions for test chat/i }),
    );
    await user.click(screen.getByRole("menuitem", { name: /delete chat/i }));

    expect(
      screen.getByText("Delete this chat permanently?"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete" })).toHaveFocus();
  });

  // FIX-8: Escape cancels the inline confirm without mutating
  it("FIX-8: Escape cancels the inline confirm", async () => {
    const user = userEvent.setup();
    const mock = mockFetch({
      "/chats/1": { body: { ok: true } },
      "/chats": { body: [chatFixture] },
    });

    render(<ChatList />, { wrapper });

    await screen.findByText("Test Chat");
    const trigger = screen.getByRole("button", {
      name: /open chat actions for test chat/i,
    });
    await user.click(trigger);
    await user.click(screen.getByRole("menuitem", { name: /delete chat/i }));
    expect(
      screen.getByText("Delete this chat permanently?"),
    ).toBeInTheDocument();

    await user.keyboard("{Escape}");

    expect(
      screen.queryByText("Delete this chat permanently?"),
    ).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(
      mock.mock.calls.some(
        ([url, init]) =>
          String(url).endsWith("/chats/1") &&
          (init as RequestInit | undefined)?.method === "DELETE",
      ),
    ).toBe(false);
  });

  // ── Rename ─────────────────────────────────────────────────────

  /** Open the ⋯ menu and choose Rename; returns the inline edit input. */
  async function openRenameInput(user: ReturnType<typeof userEvent.setup>) {
    await screen.findByText("Test Chat");
    await user.click(
      screen.getByRole("button", { name: /open chat actions for test chat/i }),
    );
    await user.click(screen.getByRole("menuitem", { name: /rename/i }));
    return screen.getByRole("textbox", {
      name: /rename chat test chat/i,
    }) as HTMLInputElement;
  }

  function patchCalls(mock: ReturnType<typeof mockFetch>) {
    return mock.mock.calls.filter(
      ([url, init]) =>
        String(url).endsWith("/chats/1") &&
        (init as RequestInit | undefined)?.method === "PATCH",
    );
  }

  it("RENAME: menu shows Rename as the first item", async () => {
    const user = userEvent.setup();
    mockFetch({
      "/chats": { body: [chatFixture] },
    });

    render(<ChatList />, { wrapper });

    await screen.findByText("Test Chat");
    await user.click(
      screen.getByRole("button", { name: /open chat actions for test chat/i }),
    );

    const items = screen.getAllByRole("menuitem");
    expect(items[0]).toHaveTextContent("Rename");
    expect(items.map((i) => i.textContent)).toEqual([
      "Rename",
      "Clear chat",
      "Delete chat",
    ]);
  });

  it("RENAME: commit sends PATCH with the trimmed title", async () => {
    const user = userEvent.setup();
    const mock = mockFetch({
      "PATCH /chats/1": {
        body: { ...chatFixture, title: "New title" },
      },
      "/chats": { body: [chatFixture] },
    });

    render(<ChatList />, { wrapper });

    const input = await openRenameInput(user);
    // Prefilled with the current title, focused, text selected
    expect(input).toHaveFocus();
    expect(input.value).toBe("Test Chat");
    expect(input.selectionStart).toBe(0);
    expect(input.selectionEnd).toBe("Test Chat".length);

    await user.clear(input);
    await user.type(input, "  New title  ");
    await user.keyboard("{Enter}");

    await waitFor(() => {
      const calls = patchCalls(mock);
      expect(calls).toHaveLength(1);
      const body = JSON.parse((calls[0][1] as RequestInit).body as string);
      expect(body).toEqual({ title: "New title" });
    });
  });

  it("RENAME: optimistic title shows immediately; busy affordance while pending", async () => {
    const user = userEvent.setup();
    let releaseRename!: (response: Response) => void;
    const renameGate = new Promise<Response>((resolve) => {
      releaseRename = resolve;
    });
    // The list route is dynamic so the post-settle refetch returns the
    // renamed row instead of reverting the title.
    let chatsBody: Chat[] = [chatFixture];
    mockFetchWithStreams({
      "/chats/1": { response: () => renameGate },
      "/chats": { response: () => jsonResponse(chatsBody) },
    });

    render(<ChatList />, { wrapper });

    const input = await openRenameInput(user);
    await user.clear(input);
    await user.type(input, "Renamed Chat");
    await user.keyboard("{Enter}");

    // Optimistic: the new title renders BEFORE the PATCH resolves
    expect(await screen.findByText("Renamed Chat")).toBeInTheDocument();
    expect(screen.queryByText("Test Chat")).not.toBeInTheDocument();

    // Row busy affordance: the ⋯ trigger is disabled and spinning
    const trigger = screen.getByRole("button", {
      name: /open chat actions for renamed chat/i,
    });
    expect(trigger).toBeDisabled();
    expect(trigger.querySelector(".animate-spin")).not.toBeNull();

    chatsBody = [{ ...chatFixture, title: "Renamed Chat" }];
    releaseRename(jsonResponse({ ...chatFixture, title: "Renamed Chat" }));

    await waitFor(() => {
      expect(
        screen.getByRole("button", {
          name: /open chat actions for renamed chat/i,
        }),
      ).not.toBeDisabled();
    });
    expect(screen.getByText("Renamed Chat")).toBeInTheDocument();
    expect(useErrorStore.getState().errors).toHaveLength(0);
  });

  it("RENAME: error restores the old title and pushes a toast", async () => {
    const user = userEvent.setup();
    mockFetch({
      "PATCH /chats/1": { status: 404, body: { detail: "chat_not_found" } },
      "/chats": { body: [chatFixture] },
    });

    render(<ChatList />, { wrapper });

    const input = await openRenameInput(user);
    await user.clear(input);
    await user.type(input, "Doomed rename");
    await user.keyboard("{Enter}");

    // Snapshot restored + toast pushed (the list has no inline error surface)
    await waitFor(() => {
      expect(screen.getByText("Test Chat")).toBeInTheDocument();
      expect(screen.queryByText("Doomed rename")).not.toBeInTheDocument();
      expect(useErrorStore.getState().errors).toHaveLength(1);
    });
    expect(useErrorStore.getState().errors[0].code).toBe("chat_not_found");
  });

  it("RENAME: Escape cancels the edit without a request", async () => {
    const user = userEvent.setup();
    const mock = mockFetch({
      "PATCH /chats/1": { body: { ...chatFixture, title: "x" } },
      "/chats": { body: [chatFixture] },
    });

    render(<ChatList />, { wrapper });

    const input = await openRenameInput(user);
    await user.clear(input);
    await user.type(input, "Never sent");
    await user.keyboard("{Escape}");

    expect(
      screen.queryByRole("textbox", { name: /rename chat/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Test Chat")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /open chat actions for test chat/i }),
    ).toHaveFocus();
    expect(patchCalls(mock)).toHaveLength(0);
  });

  it("RENAME: empty or unchanged commit cancels silently without a request", async () => {
    const user = userEvent.setup();
    const mock = mockFetch({
      "PATCH /chats/1": { body: { ...chatFixture, title: "x" } },
      "/chats": { body: [chatFixture] },
    });

    render(<ChatList />, { wrapper });

    // Empty commit
    let input = await openRenameInput(user);
    await user.clear(input);
    await user.keyboard("{Enter}");
    expect(
      screen.queryByRole("textbox", { name: /rename chat/i }),
    ).not.toBeInTheDocument();
    expect(patchCalls(mock)).toHaveLength(0);

    // Unchanged commit
    input = await openRenameInput(user);
    expect(input.value).toBe("Test Chat");
    await user.keyboard("{Enter}");
    expect(
      screen.queryByRole("textbox", { name: /rename chat/i }),
    ).not.toBeInTheDocument();
    expect(patchCalls(mock)).toHaveLength(0);
    expect(screen.getByText("Test Chat")).toBeInTheDocument();
  });

  it("RENAME: blur cancels the edit without a request", async () => {
    const user = userEvent.setup();
    const mock = mockFetch({
      "PATCH /chats/1": { body: { ...chatFixture, title: "x" } },
      "/chats": { body: [chatFixture] },
    });

    render(<ChatList />, { wrapper });

    const input = await openRenameInput(user);
    await user.clear(input);
    await user.type(input, "Abandoned");
    // Click outside the row - blur cancels without committing
    await user.click(screen.getByText("Chats"));

    await waitFor(() => {
      expect(
        screen.queryByRole("textbox", { name: /rename chat/i }),
      ).not.toBeInTheDocument();
    });
    expect(screen.getByText("Test Chat")).toBeInTheDocument();
    expect(patchCalls(mock)).toHaveLength(0);
  });
});
