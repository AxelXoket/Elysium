/**
 * Where the keyboard goes when the FIRST message is deleted and the
 * composer cannot take it.
 *
 * Deleting a message deletes everything after it, so the chat is then
 * empty. The keyboard goes to the empty state ("No messages yet"): named by
 * its heading, focusable by script only. Never the sidebar (the real one is
 * rendered here, character card and chat row both carrying the "selected"
 * classes), never the unnamed `main`. A failed delete puts it back on the
 * message's own Delete button.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { MessageList } from "@/components/chat/MessageList";
import { Sidebar } from "@/components/layout/Sidebar";
import { useUiStore } from "@/lib/store/uiStore";
import { characterFixture, chatFixture } from "@/test/mocks/fixtures";
import { mockFetch } from "../mocks/api";
import type { Message } from "@/lib/schemas/chats";

const message = (id: number, role: "user" | "assistant", content: string): Message => ({
  id, chat_id: 1, role, content, created_at: `2026-01-01T00:0${id}:00Z`,
});
const TWO = [message(1, "user", "First"), message(2, "assistant", "Second")];

function routes(deleteStatus = 200) {
  // The list after a delete that worked is empty: the mock reads `body` at
  // request time, so the two getters below serve the server's real answer.
  let deleted = false;
  mockFetch({
    "DELETE /chats/1/messages/1": deleteStatus === 200
      ? { get body() { deleted = true; return { ok: true, deleted_count: 2 }; } }
      : deleteStatus === 404
        // The rows were already gone at the server: that IS the deletion.
        ? { status: 404, get body() { deleted = true; return { detail: "message_not_found" }; } }
        : { status: deleteStatus, body: { detail: "internal_error" } },
    "/chats/1/messages": { get body() { return deleted ? [] : TWO; } },
    "/chats": { body: [{ ...chatFixture, id: 1, character_id: characterFixture.id, title: "Harbour" }] },
    "/characters": { body: [characterFixture] },
    "/personas": { body: [] },
  });
}

/** The chat as the app draws it: the real sidebar beside the list, an
 *  unnamed `main`, and a composer that is off. */
function App() {
  return (
    <>
      <Sidebar />
      <main className="warm-canvas">
        <MessageList chatId={1} />
        <textarea aria-label="Message" disabled />
      </main>
    </>
  );
}

async function deleteFirst(user: ReturnType<typeof userEvent.setup>) {
  const shell = screen.getByText("First").closest(".message-bubble-shell") as HTMLElement;
  await user.click(within(shell).getByRole("button", { name: "Delete message" }));
  const dialog = await within(shell).findByRole("dialog", { name: "Confirm delete message" });
  await user.click(within(dialog).getByRole("button", { name: /^delete$/i }));
}

describe("deleting the first message with the composer off", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: 1, selectedCharacterId: characterFixture.id,
      selectedModelId: "openai/gpt-4o", sidebarCollapsed: false,
    });
  });
  afterEach(() => vi.restoreAllMocks());

  for (const collapsed of [false, true]) {
    it(`lands on the named empty state, sidebar ${collapsed ? "collapsed" : "open"}`, async () => {
      useUiStore.setState({ sidebarCollapsed: collapsed });
      routes();
      const user = userEvent.setup();
      renderWithQueryClient(<App />);
      await screen.findByText("First");
      if (!collapsed) {
        // GROUND: the sidebar's "selected" chat row is really there.
        await screen.findByRole("button", { name: /select chat harbour/i });
      }
      await deleteFirst(user);
      const empty = await screen.findByRole("region", { name: "No messages yet" });
      await waitFor(() => expect(empty).toHaveFocus());
      expect(empty).toHaveAttribute("tabindex", "-1");
      expect(document.activeElement?.closest("#es-sidebar")).toBeNull();
      expect(document.activeElement?.tagName).not.toBe("MAIN");
    });
  }

  it("a 404 (rows already gone) is the deletion: the empty state takes the keyboard", async () => {
    routes(404);
    const user = userEvent.setup();
    renderWithQueryClient(<App />);
    await screen.findByText("First");
    await deleteFirst(user);
    const empty = await screen.findByRole("region", { name: "No messages yet" });
    await waitFor(() => expect(empty).toHaveFocus());
    expect(document.activeElement).not.toBe(document.body);
  });

  it("a failed delete puts the keyboard back on the message's Delete button", async () => {
    routes(500);
    const user = userEvent.setup();
    renderWithQueryClient(<App />);
    await screen.findByText("First");
    await deleteFirst(user);
    const shell = screen.getByText("First").closest(".message-bubble-shell") as HTMLElement;
    const trigger = within(shell).getByRole("button", { name: "Delete message" });
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(screen.queryByRole("region", { name: "No messages yet" })).toBeNull();
  });

  it("POSITIVE CONTROL: an enabled composer still comes first", async () => {
    routes();
    const user = userEvent.setup();
    renderWithQueryClient(<><MessageList chatId={1} /><textarea aria-label="Message" /></>);
    await screen.findByText("First");
    await deleteFirst(user);
    await waitFor(() => expect(screen.getByRole("textbox", { name: "Message" })).toHaveFocus());
    const empty = await screen.findByRole("region", { name: "No messages yet" });
    expect(empty).not.toHaveFocus();
  });
});
