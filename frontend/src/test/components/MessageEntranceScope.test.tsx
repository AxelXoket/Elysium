/**
 * MessageEntranceScope.test.tsx - only the newest messages animate in.
 *
 * The list has gated its stagger to a tail window since FF5, but every bubble
 * ALSO wrapped itself in its own `FadeIn`, and that one was ungated. Opening a
 * three-hundred-message chat therefore started three hundred opacity tweens at
 * once, whatever the list said about the rows around them. It showed up as
 * the entrance animation touching every bubble instead of the visible ones.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { FadeIn } from "@/components/motion/FadeIn";
import { createTestQueryClient } from "@/test/helpers/renderWithQueryClient";
import { keys } from "@/lib/query/keys";
import { useUiStore } from "@/lib/store/uiStore";
import { MessageList } from "@/components/chat/MessageList";
import type { Message } from "@/lib/schemas/chats";

/** Mirrors ANIMATED_TAIL_GROUPS in MessageList; a chat longer than this is
 *  the only case where the gate is observable at all. */
const TAIL = 16;

function msg(id: number, role: "user" | "assistant", content: string): Message {
  return {
    id,
    chat_id: 1,
    role,
    content,
    created_at: "2026-01-01T00:00:00Z",
  };
}

const opacityOf = (el: Element | null) =>
  el ? (el as HTMLElement).style.opacity : "";

describe("FadeIn can be told not to fade", () => {
  it("mounts hidden when enabled, and at rest when not", () => {
    // Both halves together: "does not animate" is only meaningful next to
    // proof that this component animates at all.
    const { container: on } = render(
      <FadeIn>
        <span>a</span>
      </FadeIn>,
    );
    expect(opacityOf(on.firstElementChild)).toBe("0");

    const { container: off } = render(
      <FadeIn enabled={false}>
        <span>b</span>
      </FadeIn>,
    );
    expect(
      opacityOf(off.firstElementChild),
      "a disabled fade still mounted at zero opacity",
    ).not.toBe("0");
  });
});

describe("only the tail of a long chat animates in", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedModelId: "openai/gpt-4o" });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ selectedModelId: null });
  });

  /** Long enough that there is history well outside the tail window. */
  const long: Message[] = Array.from({ length: TAIL * 3 }, (_, i) =>
    msg(i + 1, i % 2 === 0 ? "user" : "assistant", `message ${i + 1}`),
  );

  it("leaves old history at rest and fades only the newest window", () => {
    // Measured at MOUNT, synchronously, with the cache primed so the first
    // render already has every message and no `await` is needed. A fade
    // finishes, so anything asserted after a frame has run is a race: this
    // test failed inside the full suite and passed alone until it was pinned
    // to the one instant where "hidden" and "visible" are distinguishable.
    const qc = createTestQueryClient();
    qc.setQueryData(keys.messages(1), long);
    render(
      <QueryClientProvider client={qc}>
        <MessageList chatId={1} onEditMessage={vi.fn()} />
      </QueryClientProvider>,
    );

    const bubbles = document.querySelectorAll(".message-bubble-shell");
    expect(
      bubbles.length,
      "the chat is not long enough for the gate to be observable",
    ).toBeGreaterThan(TAIL + 4);

    /** The FadeIn wrapper is the nearest ancestor motion gave an opacity. */
    const faded = (bubble: Element) =>
      (bubble.closest("[style*='opacity']") as HTMLElement | null)?.style
        .opacity === "0";

    expect(
      faded(bubbles[bubbles.length - 1]),
      "the newest message did not animate in; the gate is inverted or dead",
    ).toBe(true);
    expect(
      faded(bubbles[0]),
      "old history is animating, so a long chat opens with one tween per message",
    ).toBe(false);
  });
});
