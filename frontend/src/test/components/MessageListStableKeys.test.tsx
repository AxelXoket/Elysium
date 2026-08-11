/**
 * MessageListStableKeys.test.tsx - deferred finding L10.
 *
 * When the optimistic (negative-id) user message is swapped for the real
 * persisted row, the bubble's React key must not change: a key change
 * remounts the DOM node and replays the entrance animation (visible
 * flicker). These tests assert DOM-node identity across the swap.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import type { QueryClient } from "@tanstack/react-query";
import {
  createTestQueryClient,
  renderWithQueryClient,
} from "@/test/helpers/renderWithQueryClient";
import { MessageList } from "@/components/chat/MessageList";
import { keys } from "@/lib/query/keys";
import { mockFetch } from "../mocks/api";
import type { Message } from "@/lib/schemas/chats";
import { useEffect } from "react";
import { AnimatedListItem } from "@/components/motion/AnimatedList";

function msg(id: number, role: "user" | "assistant", content: string): Message {
  return {
    id,
    chat_id: 1,
    role,
    content,
    created_at: "2026-01-01T00:00:00Z",
  };
}

const greeting = msg(1, "assistant", "greeting");

/**
 * Update the messages cache and flush the observer notification. TanStack
 * Query v5 schedules query notifications with setTimeout(0), so a bare
 * act(() => setQueryData(...)) returns BEFORE the component re-renders.
 */
async function setMessagesAndFlush(qc: QueryClient, messages: Message[]) {
  await act(async () => {
    qc.setQueryData<Message[]>(keys.messages(1), messages);
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

describe("MessageList - stable keys across the optimistic→real swap", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    // Defensive: cache is seeded fresh per test, so no refetch should fire.
    mockFetch({});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps the SAME DOM node when the optimistic user message becomes real", async () => {
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [
      greeting,
      msg(-1001, "user", "hello there"),
    ]);

    renderWithQueryClient(<MessageList chatId={1} />, { client: qc });

    const optimisticNode = screen.getByText("hello there");
    // Only the persisted greeting has message actions at this point
    expect(screen.getAllByLabelText("Delete message")).toHaveLength(1);

    // Backend persisted the row - same index, same role+content, real id
    await setMessagesAndFlush(qc, [greeting, msg(7, "user", "hello there")]);

    // Re-render proof: the now-persisted user bubble gained its actions
    expect(screen.getAllByLabelText("Delete message")).toHaveLength(2);

    const swappedNodes = screen.getAllByText("hello there");
    expect(swappedNodes).toHaveLength(1); // no duplicate bubble
    expect(swappedNodes[0]).toBe(optimisticNode); // same node - no remount
    expect(swappedNodes[0]).toBeInTheDocument();
  });

  it("a genuinely new message (real→real id change) still gets a new node", async () => {
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [
      greeting,
      msg(5, "user", "swap me"),
    ]);

    renderWithQueryClient(<MessageList chatId={1} />, { client: qc });

    const originalNode = screen.getByText("swap me");

    // Same position but the PREVIOUS id was not optimistic - the stable-key
    // map must not kick in, so the new id remounts a new node.
    await setMessagesAndFlush(qc, [greeting, msg(9, "user", "swap me v2")]);

    const replacedNode = screen.getByText("swap me v2");
    expect(replacedNode).not.toBe(originalNode);
    expect(screen.queryByText("swap me")).not.toBeInTheDocument();
  });

  it("does not reuse the key when content differs at the swap index", async () => {
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [
      greeting,
      msg(-1002, "user", "draft text"),
    ]);

    renderWithQueryClient(<MessageList chatId={1} />, { client: qc });

    const draftNode = screen.getByText("draft text");

    await setMessagesAndFlush(qc, [greeting, msg(8, "user", "different text")]);

    const newNode = screen.getByText("different text");
    expect(newNode).not.toBe(draftNode);
    expect(screen.queryByText("draft text")).not.toBeInTheDocument();
  });
});

/**
 * Audit: a bubble remounted (losing its local state) each time it fell out of
 * the animated tail window.
 *
 * AnimatedListItem returned a plain <div> when animated=false and a motion
 * component when true, so the per-index flag flipping as the list grows changed
 * the ELEMENT TYPE under an unchanged key - React unmounts and remounts the
 * whole MessageBubble subtree.
 */
describe("AnimatedListItem keeps its identity", () => {
  it("renders the same element type animated and not", () => {
    const animated = render(
      <AnimatedListItem animated className="probe">
        <span data-testid="child">x</span>
      </AnimatedListItem>,
    );
    const animatedTag = animated.container.firstElementChild?.tagName;
    // The floor. This comparison used to be the whole test, and with optional
    // chaining on both sides a build where AnimatedListItem returned null in
    // BOTH branches gave undefined === undefined and passed: the two renders
    // agreed on nothing at all. The tag has to be a real element, and the
    // child has to actually be in it.
    expect(animatedTag).toBe("DIV");
    expect(animated.getByTestId("child")).toBeInTheDocument();
    animated.unmount();

    const plain = render(
      <AnimatedListItem animated={false} className="probe">
        <span data-testid="child">x</span>
      </AnimatedListItem>,
    );
    expect(plain.getByTestId("child")).toBeInTheDocument();
    expect(plain.container.firstElementChild?.tagName).toBe(animatedTag);
  });

  it("does not remount its child when the flag flips", () => {
    let mounts = 0;
    function Probe() {
      useEffect(() => {
        mounts += 1;
      }, []);
      return <span data-testid="child">x</span>;
    }

    const view = render(
      <AnimatedListItem animated className="probe">
        <Probe />
      </AnimatedListItem>,
    );
    expect(mounts).toBe(1);

    // Exactly what happens when the list grows past ANIMATED_TAIL_GROUPS.
    view.rerender(
      <AnimatedListItem animated={false} className="probe">
        <Probe />
      </AnimatedListItem>,
    );
    expect(mounts).toBe(1);
  });
});
