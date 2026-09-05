/**
 * MessageAuthorSemantics - a chat that only a sighted reader can follow.
 *
 * Who said what was carried by two things and only two: which side of the
 * column the bubble sits on, and what colour it is. Both are invisible to a
 * screen reader, so the thread read back as an undifferentiated run of text
 * with no speaker changes in it. A message is also not announced as a unit,
 * so there was nothing to move between either.
 *
 * The fix is a role plus a visually hidden author label, and the label is the
 * bubble's accessible name rather than a second string beside it, so there is
 * one fact to keep true instead of two that can drift.
 *
 * The labels are IMPORTED. A test that retyped "You" would go green with the
 * component and itself wrong together, and the interesting failure here is
 * exactly the one where both bubbles end up saying the same thing - so the
 * first assertion below compares the two rendered names to EACH OTHER and
 * names no string at all.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { MessageList } from "@/components/chat/MessageList";
import { MESSAGE_AUTHOR_LABELS } from "@/components/chat/MessageBubble";
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

const EXCHANGE: Message[] = [
  message(1, "user", "what happened next?"),
  message(2, "assistant", "and then the lamps went out"),
];

/** The accessible name of the bubble holding `text`, read off the rendered
 *  tree rather than off a string this file chose. */
function bubbleNamed(text: string): string {
  const shell = screen.getByText(text).closest(".message-bubble-shell");
  expect(shell, `no bubble around "${text}"`).not.toBeNull();
  const el = shell as HTMLElement;
  const labelledBy = el.getAttribute("aria-labelledby");
  expect(labelledBy, "the bubble names no author").not.toBeNull();
  const label = document.getElementById(labelledBy as string);
  expect(label, "the bubble points at an author label that is not there")
    .not.toBeNull();
  return (label as HTMLElement).textContent ?? "";
}

describe("a message says who is speaking", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 1, selectedModelId: "openai/gpt-4o" });
    mockFetch({ "/chats/1/messages": { body: EXCHANGE } });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("is announced as a unit of its own", async () => {
    renderWithQueryClient(<MessageList chatId={1} />);
    await screen.findByText("what happened next?");

    // Two messages, two articles. Before this, a screen reader had no unit to
    // move between at all.
    expect(screen.getAllByRole("article")).toHaveLength(2);
  });

  it("tells the two speakers apart, whatever the words are", async () => {
    renderWithQueryClient(<MessageList chatId={1} />);
    await screen.findByText("what happened next?");

    const mine = bubbleNamed("what happened next?");
    const theirs = bubbleNamed("and then the lamps went out");

    // Deliberately no literal on either side. One label repeated on every
    // bubble would satisfy a test that only checked a name was present, and
    // it is the difference, not the presence, that carries the meaning.
    expect(mine).not.toBe(theirs);
    expect(mine).toMatch(/\S/);
    expect(theirs).toMatch(/\S/);
  });

  it("gives each row the label its own role declares", async () => {
    renderWithQueryClient(<MessageList chatId={1} />);
    await screen.findByText("what happened next?");

    // The wiring, not just the difference: two distinct labels swapped over
    // would pass the test above and read the conversation backwards.
    expect(bubbleNamed("what happened next?")).toBe(
      MESSAGE_AUTHOR_LABELS.user,
    );
    expect(bubbleNamed("and then the lamps went out")).toBe(
      MESSAGE_AUTHOR_LABELS.assistant,
    );
  });

  it("POSITIVE CONTROL: the label is reachable as the bubble's name", async () => {
    renderWithQueryClient(<MessageList chatId={1} />);
    await screen.findByText("what happened next?");

    // Without this the hand-walk above would pass on markup that wires the
    // label somewhere the accessibility tree never looks.
    expect(
      screen.getByRole("article", { name: MESSAGE_AUTHOR_LABELS.user }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("article", { name: MESSAGE_AUTHOR_LABELS.assistant }),
    ).toBeInTheDocument();
  });
});

describe("the label stays out of sight", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedChatId: 1, selectedModelId: "openai/gpt-4o" });
    mockFetch({ "/chats/1/messages": { body: EXCHANGE } });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("adds nothing a sighted reader can see", async () => {
    renderWithQueryClient(<MessageList chatId={1} />);
    await screen.findByText("what happened next?");

    const shell = screen
      .getByText("what happened next?")
      .closest(".message-bubble-shell") as HTMLElement;
    const label = document.getElementById(
      shell.getAttribute("aria-labelledby") as string,
    ) as HTMLElement;

    // A visible speaker chip would be a new element in a design language that
    // has none, so the label rides the same visually-hidden utility the
    // dialog close button and the model caveat already use.
    expect(label.className.split(/\s+/)).toContain("sr-only");
  });

  it("GROUND CONTROL: the colour and the alignment are exactly where they were", async () => {
    renderWithQueryClient(<MessageList chatId={1} />);
    await screen.findByText("what happened next?");

    const mine = screen
      .getByText("what happened next?")
      .closest(".message-bubble-shell") as HTMLElement;
    const theirs = screen
      .getByText("and then the lamps went out")
      .closest(".message-bubble-shell") as HTMLElement;

    // The accessibility layer is additive. If it had moved a class the whole
    // thread would change shape, and an announcement nobody can see would be
    // a poor trade for that.
    expect(mine.className.split(/\s+/)).toContain("is-user");
    expect(theirs.className.split(/\s+/)).toContain("is-assistant");
    expect(
      (mine.parentElement as HTMLElement).className.split(/\s+/),
    ).toContain("justify-end");
    expect(
      (theirs.parentElement as HTMLElement).className.split(/\s+/),
    ).toContain("justify-start");
  });
});
