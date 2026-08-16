/**
 * The copy button, and the promise it exists to keep.
 *
 * Settings tells the reader "Copying a message always copies the original
 * text, asterisks included." Until this button there was no copying at all -
 * pywebview shipped with text selection off and the context menu disabled -
 * and even with selection on, a drag copies what the DOM renders, which
 * parseMessage strips the delimiting asterisks out of. Only a button reading
 * the message body can honour that sentence, so the asterisk test below is
 * the one that matters most.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { MessageList } from "@/components/chat/MessageList";
import { COPY_FEEDBACK_MS } from "@/components/chat/CopyMessageButton";
import { useUiStore } from "@/lib/store/uiStore";
import { useErrorStore } from "@/lib/errors";
import { mockFetch } from "../mocks/api";
import {
  installClipboardMock,
  removeClipboardApi,
  type ClipboardMock,
} from "../helpers/clipboardMock";
import type { Message } from "@/lib/schemas/chats";

/** Asterisks around narration: rendered away, and required in the copy. */
const ASSISTANT_TEXT = "*She turned slowly.* Then she spoke.";
const USER_TEXT = "Ask her what she saw.";

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

function seedTwoMessages(): void {
  mockFetch({
    "/chats/1/messages": {
      body: [
        message(1, "user", USER_TEXT),
        message(2, "assistant", ASSISTANT_TEXT),
      ],
    },
  });
}

function bubbleContaining(fragment: string): HTMLElement {
  const bubble = screen.getByText(fragment, { exact: false })
    .closest(".message-bubble-shell");
  expect(bubble).not.toBeNull();
  return bubble as HTMLElement;
}

describe("CopyMessage", () => {
  let clipboard: ClipboardMock | null = null;
  let undoRemoval: (() => void) | null = null;

  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
    useUiStore.setState({
      selectedChatId: 1,
      selectedCharacterId: 1,
      selectedModelId: "openai/gpt-4o",
    });
  });

  afterEach(() => {
    clipboard?.restore();
    clipboard = null;
    undoRemoval?.();
    undoRemoval = null;
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("copies a reply with the asterisks the screen does not show", async () => {
    const user = userEvent.setup();
    clipboard = installClipboardMock();
    seedTwoMessages();

    renderWithQueryClient(<MessageList chatId={1} />);
    await screen.findByText("Then she spoke.", { exact: false });

    // GROUND: the rendered text really has lost the asterisks, so the
    // assertion below is about the message body and not about the DOM
    // happening to match it.
    const bubble = bubbleContaining("Then she spoke.");
    expect(bubble.textContent).not.toContain("*");

    await user.click(
      within(bubble).getByRole("button", { name: "Copy reply" }),
    );

    expect(clipboard.writeText).toHaveBeenCalledTimes(1);
    expect(clipboard.writeText).toHaveBeenCalledWith(ASSISTANT_TEXT);
  });

  it("copies the reader's own message too", async () => {
    const user = userEvent.setup();
    clipboard = installClipboardMock();
    seedTwoMessages();

    renderWithQueryClient(<MessageList chatId={1} />);
    await screen.findByText(USER_TEXT);

    const bubble = bubbleContaining(USER_TEXT);
    await user.click(
      within(bubble).getByRole("button", { name: "Copy your message" }),
    );

    expect(clipboard.writeText).toHaveBeenCalledWith(USER_TEXT);
  });

  it("confirms in place and never through the toast stack", async () => {
    const user = userEvent.setup();
    clipboard = installClipboardMock();
    seedTwoMessages();

    renderWithQueryClient(<MessageList chatId={1} />);
    await screen.findByText(USER_TEXT);

    const bubble = bubbleContaining(USER_TEXT);
    await user.click(
      within(bubble).getByRole("button", { name: "Copy your message" }),
    );

    // The outcome shows in the title and in the live region, never in the
    // accessible name: the name has to stay findable. Queried by the
    // live-region attribute rather than role="status", because that role
    // belongs to the thinking indicator and one per bubble would make its
    // own tests ambiguous.
    const button = within(bubble).getByRole("button", {
      name: "Copy your message",
    });
    await waitFor(() => {
      expect(button).toHaveAttribute("title", "Copied");
    });
    expect(bubble.querySelector("[aria-live='polite']")).toHaveTextContent(
      "Copied to the clipboard",
    );
    // A toast would be swallowed the second time it said the same sentence,
    // which is exactly why this feedback is local.
    expect(useErrorStore.getState().errors).toHaveLength(0);
  });

  it("returns to the copy icon once the confirmation has been read", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    clipboard = installClipboardMock();
    seedTwoMessages();

    renderWithQueryClient(<MessageList chatId={1} />);
    await screen.findByText(USER_TEXT);

    const bubble = bubbleContaining(USER_TEXT);
    const button = within(bubble).getByRole("button", {
      name: "Copy your message",
    });
    await user.click(button);
    await waitFor(() => {
      expect(button).toHaveAttribute("title", "Copied");
    });

    // Still there just before the deadline. Without this half the test would
    // also pass on a confirmation that vanished instantly, which is the
    // failure the reader would actually notice.
    vi.advanceTimersByTime(COPY_FEEDBACK_MS - 100);
    expect(button).toHaveAttribute("title", "Copied");

    // Read from the component, not a private copy: the auto-dismiss test one
    // folder over kept its own 4500 and stayed green when production moved.
    vi.advanceTimersByTime(200);
    await waitFor(() => {
      expect(button).toHaveAttribute("title", "Copy your message text");
    });
  });

  it("says so when the clipboard refuses, and keeps saying it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    clipboard = installClipboardMock(() =>
      Promise.reject(
        new DOMException("Document is not focused.", "NotAllowedError"),
      ),
    );
    seedTwoMessages();

    renderWithQueryClient(<MessageList chatId={1} />);
    await screen.findByText(USER_TEXT);

    const bubble = bubbleContaining(USER_TEXT);
    const button = within(bubble).getByRole("button", {
      name: "Copy your message",
    });
    await user.click(button);

    await waitFor(() => {
      expect(button).toHaveAttribute(
        "title",
        expect.stringContaining("Could not reach the clipboard"),
      );
    });
    expect(
      bubble.querySelector("[aria-live='polite']"),
    ).toHaveTextContent("Could not reach the clipboard");

    // "Keeps saying it" is the whole claim: a confirmation is transient, a
    // refusal is not. Three times the confirmation's lifetime later it must
    // still be there.
    vi.advanceTimersByTime(COPY_FEEDBACK_MS * 3);
    expect(button).toHaveAttribute(
      "title",
      expect.stringContaining("Could not reach the clipboard"),
    );
  });

  it("treats a missing clipboard api the same as a refusal", async () => {
    const user = userEvent.setup();
    undoRemoval = removeClipboardApi();
    seedTwoMessages();

    renderWithQueryClient(<MessageList chatId={1} />);
    await screen.findByText(USER_TEXT);

    const bubble = bubbleContaining(USER_TEXT);
    const button = within(bubble).getByRole("button", {
      name: "Copy your message",
    });
    await user.click(button);

    await waitFor(() => {
      expect(button).toHaveAttribute(
        "title",
        expect.stringContaining("Could not reach the clipboard"),
      );
    });
  });

  it("refuses to copy a reply that has no words yet", async () => {
    // A reply that is only a generated picture keeps an empty text row on
    // purpose. writeText("") RESOLVES and empties the clipboard, so an
    // ungated button would wipe the reader's work and then tick to say it
    // had worked.
    const user = userEvent.setup();
    clipboard = installClipboardMock();
    mockFetch({
      "/chats/1/messages": {
        body: [
          message(1, "user", USER_TEXT),
          message(2, "assistant", "   "),
        ],
      },
    });

    renderWithQueryClient(<MessageList chatId={1} />);
    await screen.findByText(USER_TEXT);

    // GROUND: the empty reply really rendered, so "disabled" is about the
    // text and not about a bubble that never appeared.
    const bubbles = document.querySelectorAll(".message-bubble-shell");
    expect(bubbles.length).toBe(2);

    const emptyBubble = bubbles[1] as HTMLElement;
    const button = within(emptyBubble).getByRole("button", {
      name: "Copy reply",
    });
    expect(button).toBeDisabled();

    await user.click(button);
    expect(clipboard.writeText).not.toHaveBeenCalled();
  });

  it("copies the variant on screen, not the group's active row", async () => {
    // The reason this component takes paneText rather than message.content.
    // Browsing back through variants leaves the ACTIVE row pointing at one
    // take while the reader is looking at another; copying the active row
    // would hand over a message that was never on screen. The same mistake
    // has been made twice in MessageBubble already, once with attachments
    // and once with Speak.
    const user = userEvent.setup();
    clipboard = installClipboardMock();

    const group = {
      chat_id: 1,
      created_at: "2026-01-01T00:00:00Z",
      variant_group: 2,
      variant_count: 2,
    };
    mockFetch({
      "/chats/1/messages": {
        body: [
          {
            id: 1, role: "user", content: "ask again", chat_id: 1,
            created_at: group.created_at, attachments: [],
            variant_group: null, active: true,
            variant_index: 0, variant_count: 1,
          },
          {
            ...group, id: 2, role: "assistant", attachments: [],
            content: "*TAKE A.* The active row.",
            active: true, variant_index: 0,
          },
          {
            ...group, id: 3, role: "assistant", attachments: [],
            content: "*TAKE B.* The one on screen.",
            active: false, variant_index: 1,
          },
          {
            id: 4, role: "user", content: "a later turn", chat_id: 1,
            created_at: group.created_at, attachments: [],
            variant_group: null, active: true,
            variant_index: 0, variant_count: 1,
          },
        ],
      },
    });

    renderWithQueryClient(<MessageList chatId={1} />);
    await screen.findByText("The active row.", { exact: false });

    await user.click(screen.getAllByRole("button", { name: "Next reply" })[0]);
    const bubble = bubbleContaining("The one on screen.");

    // GROUND: take A really left the screen. Without this the claim below
    // would also pass on a bubble showing both.
    expect(
      screen.queryByText("The active row.", { exact: false }),
    ).toBeNull();

    await user.click(
      within(bubble).getByRole("button", { name: "Copy reply" }),
    );

    expect(clipboard.writeText).toHaveBeenCalledWith(
      "*TAKE B.* The one on screen.",
    );
    // Named explicitly so the failure says WHAT went wrong, not just that a
    // string differed.
    expect(clipboard.writeText).not.toHaveBeenCalledWith(
      "*TAKE A.* The active row.",
    );
  });

  it("reaches the button with the keyboard, without a pointer ever hovering", async () => {
    const user = userEvent.setup();
    clipboard = installClipboardMock();
    seedTwoMessages();

    renderWithQueryClient(<MessageList chatId={1} />);
    await screen.findByText(USER_TEXT);

    const bubble = bubbleContaining(USER_TEXT);
    const button = within(bubble).getByRole("button", {
      name: "Copy your message",
    });
    button.focus();
    expect(button).toHaveFocus();

    await user.keyboard("{Enter}");
    expect(clipboard.writeText).toHaveBeenCalledWith(USER_TEXT);
  });
});
