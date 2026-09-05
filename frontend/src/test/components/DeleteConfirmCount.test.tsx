/**
 * Telling the reader how much "everything after it" is.
 *
 * The inline delete panel was already right in every way that could be seen
 * from the outside - dialog role, accessible name, Cancel before Delete, the
 * focus handed back on cancel. The one thing it would not say was the size of
 * what it was about to destroy, and that is the only fact the answer turns on.
 *
 * The sentence is imported, never retyped: a test that spells the wording out
 * a second time passes when both copies are wrong together.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { MessageList } from "@/components/chat/MessageList";
import { deleteConfirmQuestion } from "@/components/chat/MessageBubble";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "../mocks/api";
import type { Message } from "@/lib/schemas/chats";

function message(
  id: number,
  role: "user" | "assistant",
  content: string,
): Message {
  return {
    id,
    chat_id: 1,
    role,
    content,
    created_at: `2026-01-01T00:0${id}:00Z`,
  };
}

const THREE: Message[] = [
  message(1, "user", "First"),
  message(2, "assistant", "Middle"),
  message(3, "user", "Last"),
];

/** Opens the delete confirmation on the bubble carrying `text`. */
async function openConfirmOn(text: string): Promise<HTMLElement> {
  const user = userEvent.setup();
  const shell = screen.getByText(text).closest(".message-bubble-shell");
  expect(shell).not.toBeNull();
  await user.click(
    within(shell as HTMLElement).getByRole("button", {
      name: "Delete message",
    }),
  );
  return await within(shell as HTMLElement).findByRole("dialog", {
    name: "Confirm delete message",
  });
}

describe("the delete confirmation names the size of what it destroys", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 1, selectedModelId: "openai/gpt-4o" });
    mockFetch({ "/chats/1/messages": { body: THREE } });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("counts the one row below a middle message", async () => {
    renderWithQueryClient(<MessageList chatId={1} />);
    await screen.findByText("Middle");

    const panel = await openConfirmOn("Middle");
    // GROUND CONTROL, and it was already green before this unit: the test has
    // hold of the right panel - the dialog role and the accessible name that
    // distinguish it from the edit confirmation in the same list.
    expect(panel).toHaveAttribute("aria-label", "Confirm delete message");
    expect(within(panel).getByText(deleteConfirmQuestion(1))).toBeInTheDocument();
    // The point of the unit, stated as the reader would state it.
    expect(panel.textContent).toContain("1");
  });

  it("counts two rows below the first message, in the plural", async () => {
    renderWithQueryClient(<MessageList chatId={1} />);
    await screen.findByText("First");

    const panel = await openConfirmOn("First");
    expect(within(panel).getByText(deleteConfirmQuestion(2))).toBeInTheDocument();
    expect(panel.textContent).toContain("2");
    // An implementation that hard-codes the singular reads the same as one
    // that hard-codes "1"; both are ruled out here.
    expect(panel.textContent).toContain("messages");
  });

  it("says nothing about a count when there is nothing after the message", async () => {
    renderWithQueryClient(<MessageList chatId={1} />);
    await screen.findByText("Last");

    const panel = await openConfirmOn("Last");
    expect(within(panel).getByText(deleteConfirmQuestion(0))).toBeInTheDocument();
    // The wording that "the 0 messages after it" would produce, ruled out.
    expect(panel.textContent).not.toContain("0");
    // POSITIVE CONTROL for that absence: the sweep reads the panel's own text,
    // and it does find a digit there when one belongs - proved by the two
    // cases above and re-proved here on the same element by a string the
    // sentence really does carry.
    expect(panel.textContent).toContain("Delete this message?");
  });

  it("still deletes nothing when the reader cancels", async () => {
    // POSITIVE CONTROL for the whole panel: the rewritten sentence changed
    // what it says, not what its buttons do.
    const user = userEvent.setup();
    const fetchMock = mockFetch({ "/chats/1/messages": { body: THREE } });
    renderWithQueryClient(<MessageList chatId={1} />);
    await screen.findByText("Middle");

    const panel = await openConfirmOn("Middle");
    await user.click(within(panel).getByRole("button", { name: "Cancel" }));

    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Confirm delete message" }),
      ).toBeNull(),
    );
    expect(screen.getByText("Last")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(
        ([, init]) => (init as RequestInit | undefined)?.method === "DELETE",
      ),
    ).toBe(false);
  });
});

describe("the sentence itself", () => {
  it("is built from the count, singular and plural both", () => {
    // Pure, so it can be pinned exactly. The component tests above prove the
    // component actually calls this; this proves what it returns.
    expect(deleteConfirmQuestion(0)).toBe("Delete this message?");
    expect(deleteConfirmQuestion(1)).toBe(
      "Delete this message and the 1 message after it?",
    );
    expect(deleteConfirmQuestion(2)).toBe(
      "Delete this message and the 2 messages after it?",
    );
    expect(deleteConfirmQuestion(12)).toBe(
      "Delete this message and the 12 messages after it?",
    );
  });
});
