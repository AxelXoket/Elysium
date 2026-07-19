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
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MessageList } from "@/components/chat/MessageList";
import { keys } from "@/lib/query/keys";
import { mockFetch } from "../mocks/api";
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
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
}

function createWrapper(qc: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
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
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [
      greeting,
      msg(-1001, "user", "hello there"),
    ]);

    render(<MessageList chatId={1} />, { wrapper: createWrapper(qc) });

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
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [
      greeting,
      msg(5, "user", "swap me"),
    ]);

    render(<MessageList chatId={1} />, { wrapper: createWrapper(qc) });

    const originalNode = screen.getByText("swap me");

    // Same position but the PREVIOUS id was not optimistic - the stable-key
    // map must not kick in, so the new id remounts a new node.
    await setMessagesAndFlush(qc, [greeting, msg(9, "user", "swap me v2")]);

    const replacedNode = screen.getByText("swap me v2");
    expect(replacedNode).not.toBe(originalNode);
    expect(screen.queryByText("swap me")).not.toBeInTheDocument();
  });

  it("does not reuse the key when content differs at the swap index", async () => {
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [
      greeting,
      msg(-1002, "user", "draft text"),
    ]);

    render(<MessageList chatId={1} />, { wrapper: createWrapper(qc) });

    const draftNode = screen.getByText("draft text");

    await setMessagesAndFlush(qc, [greeting, msg(8, "user", "different text")]);

    const newNode = screen.getByText("different text");
    expect(newNode).not.toBe(draftNode);
    expect(screen.queryByText("draft text")).not.toBeInTheDocument();
  });
});
