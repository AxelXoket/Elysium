/**
 * ConfirmPlacement.test.tsx - the delete confirmation opens where the button is.
 *
 * The complaint: pressing Delete on an assistant message opened the "this
 * cannot be undone" panel at the FAR LEFT of the bubble, nowhere near the
 * control that was just pressed. The delete button lives in `.message-actions`,
 * which is anchored top-RIGHT on every bubble regardless of role, so a
 * left-anchored panel put the question about an irreversible action as far
 * from the pointer as the row allowed.
 *
 * Measured through the cascade rather than by reading the rule: the override
 * that caused this was a separate, more specific selector, and a regex over
 * the base rule would not have seen it.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "fs";
import path from "path";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { useUiStore } from "@/lib/store/uiStore";
import { MessageList } from "@/components/chat/MessageList";
import { mockFetch } from "../mocks/api";
import type { Message } from "@/lib/schemas/chats";

const CSS = readFileSync(path.resolve(__dirname, "../../index.css"), "utf-8");

function msg(id: number, role: "user" | "assistant", content: string): Message {
  return {
    id,
    chat_id: 1,
    role,
    content,
    created_at: "2026-01-01T00:00:00Z",
  };
}

const seed = [
  msg(1, "assistant", "an assistant reply"),
  msg(2, "user", "a user question"),
];

describe("delete confirmation opens under its own button", () => {
  let sheet: HTMLStyleElement;

  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedModelId: "openai/gpt-4o" });
    sheet = document.createElement("style");
    sheet.textContent = CSS;
    document.head.appendChild(sheet);
  });

  afterEach(() => {
    sheet.remove();
    vi.restoreAllMocks();
    useUiStore.setState({ selectedModelId: null });
  });

  /** Opens the confirm on the message carrying `text` and returns both the
   *  panel and the action row it should be sitting under. */
  async function openConfirmOn(text: string) {
    const user = userEvent.setup();
    mockFetch({ "/chats/1/messages": { body: seed } });
    renderWithQueryClient(<MessageList chatId={1} onEditMessage={vi.fn()} />);

    const body = await screen.findByText(text);
    const shell = body.closest(".message-bubble-shell");
    if (!shell) throw new Error("no bubble shell around the message");

    const trigger = shell.querySelector<HTMLButtonElement>(
      '[aria-label="Delete message"]',
    );
    if (!trigger) throw new Error("no delete control on this bubble");
    await user.click(trigger);

    const panel = shell.querySelector(".message-action-confirm");
    const row = shell.querySelector(".message-actions");
    if (!panel || !row) throw new Error("confirm or action row missing");
    return { panel, row };
  }

  const anchoredEdge = (el: Element) => {
    const s = getComputedStyle(el);
    return { left: s.left, right: s.right };
  };

  it("anchors to the same edge as the action row on an assistant message", async () => {
    // THE BUG. An override sent this one to `left: 0.5rem; right: auto` while
    // the button stayed on the right.
    const { panel, row } = await openConfirmOn("an assistant reply");
    const p = anchoredEdge(panel);
    const r = anchoredEdge(row);

    expect(r.right, "the action row stopped being right-anchored").not.toBe(
      "auto",
    );
    expect(
      p.right,
      "the confirm opens on the opposite edge from the button that opened it",
    ).not.toBe("auto");
    expect(p.left).toBe("auto");
  });

  it("anchors the same way on a user message", async () => {
    // GROUND CONTROL: the user side was already correct, so this proves the
    // assertion above is not simply reading a value that is right everywhere.
    // If a future change flips the roles instead of unifying them, one of
    // these two goes red.
    const { panel, row } = await openConfirmOn("a user question");
    expect(anchoredEdge(row).right).not.toBe("auto");
    expect(anchoredEdge(panel).right).not.toBe("auto");
    expect(anchoredEdge(panel).left).toBe("auto");
  });

  it("widens a short bubble instead of letting the panel overhang off-screen", async () => {
    // The reason the left anchor existed: a 17rem panel right-anchored to a
    // narrow bubble hangs past the column edge and clips. Fixed at the cause -
    // the shell takes a floor while the confirm is open - so proximity does
    // not cost clipping. Without this the first test would be a regression
    // dressed up as a fix.
    const { panel } = await openConfirmOn("an assistant reply");
    const shell = panel.closest(".message-bubble-shell")!;
    const floor = getComputedStyle(shell).minWidth;

    expect(floor, "the shell takes no width floor while a confirm is open").not.toBe(
      "",
    );
    expect(floor).not.toBe("0px");
    expect(floor).not.toBe("auto");
  });
});
