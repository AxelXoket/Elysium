/**
 * StreamingLiveRegion.test.tsx - H-93: the arriving reply is announced.
 *
 * A reply lands one token at a time. Someone watching the screen sees it
 * start; someone listening to it had nothing at all - the streaming bubble's
 * wrapper carried no live region, so the words entered the DOM in silence and
 * the first thing announced was the finished row, minutes later.
 *
 * The pattern was already in the same file: ThinkingBubble announces itself
 * politely while it waits. These tests hold the streaming bubble to it, and
 * to the two things that make a live region actually usable rather than
 * merely present - one region that survives every token (so the appended
 * words merge into one announcement instead of a new region per frame), and
 * no `aria-atomic` (which would re-read the whole reply from the top on every
 * token).
 *
 * Everything here is measured off what a screen reader would be handed: the
 * region is found by walking UP from the streamed words, never by reaching
 * for a class name or reading the component's source.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen } from "@testing-library/react";
import type { QueryClient } from "@tanstack/react-query";
import {
  createTestQueryClient,
  renderWithQueryClient,
} from "@/test/helpers/renderWithQueryClient";
import { MessageList } from "@/components/chat/MessageList";
import { keys } from "@/lib/query/keys";
import { mockFetch } from "../mocks/api";
import type { Message } from "@/lib/schemas/chats";
import type { StreamingEntry } from "@/lib/chat/useStreamingCompletion";

const CHAT_ID = 1;
const HISTORY_TEXT = "the question that started it";
const STREAMED = "Half an answer so far";
const STREAMED_MORE = "Half an answer so far, and the rest of it";

function msg(id: number, role: "user" | "assistant", content: string): Message {
  return {
    id,
    chat_id: CHAT_ID,
    role,
    content,
    created_at: "2026-01-01T00:00:00Z",
  };
}

/** A client already holding one persisted turn, so nothing refetches. */
function seededClient(): QueryClient {
  const qc = createTestQueryClient();
  qc.setQueryData<Message[]>(keys.messages(CHAT_ID), [
    msg(1, "user", HISTORY_TEXT),
  ]);
  return qc;
}

function sending(text: string): StreamingEntry {
  return { kind: "send", text };
}

/**
 * The live region announcing the streamed words, found the way an assistive
 * technology finds it: from the text outward. Returns null when the words are
 * inside nothing that announces.
 */
function regionAnnouncing(text: string): HTMLElement | null {
  const paragraph = screen.getByText(text);
  return paragraph.closest<HTMLElement>("[aria-live]");
}

describe("MessageList - the streaming reply announces itself", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    // Cache is seeded per test; a bare mock keeps an unexpected refetch loud.
    mockFetch({});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("wraps the arriving words in a polite status region", () => {
    renderWithQueryClient(
      <MessageList chatId={CHAT_ID} isPending streaming={sending(STREAMED)} />,
      { client: seededClient() },
    );

    const live = regionAnnouncing(STREAMED);
    expect(live, "no [aria-live] ancestor above the streamed text").not.toBeNull();
    expect(live).toHaveAttribute("aria-live", "polite");
    expect(live).toHaveAttribute("role", "status");
    expect(live).toHaveTextContent(STREAMED);
  });

  it("POSITIVE CONTROL: the thinking indicator's region is found the same way", () => {
    // Same query, a region that has been correct all along. If this one goes
    // red the sweep itself is broken, not the bubble under test.
    renderWithQueryClient(
      <MessageList chatId={CHAT_ID} isPending streaming={null} />,
      { client: seededClient() },
    );

    const thinking = screen.getByRole("status", {
      name: "Assistant is thinking",
    });
    expect(thinking).toHaveAttribute("aria-live", "polite");
  });

  it("GROUND CONTROL: with nothing streaming there is no reply to announce", () => {
    const { rerender } = renderWithQueryClient(
      <MessageList chatId={CHAT_ID} streaming={null} />,
      { client: seededClient() },
    );

    // The words are not on screen at all, so the assertion above is not being
    // built on an empty render.
    expect(screen.queryByText(STREAMED)).toBeNull();

    // POSITIVE CONTROL for that absence: the same query finds them the moment
    // a stream is running, inside a region that announces.
    rerender(
      <MessageList chatId={CHAT_ID} isPending streaming={sending(STREAMED)} />,
    );
    expect(screen.getByText(STREAMED)).toBeInTheDocument();
    expect(regionAnnouncing(STREAMED)).not.toBeNull();
  });

  it("keeps ONE region across tokens so the announcements merge", () => {
    const { rerender } = renderWithQueryClient(
      <MessageList chatId={CHAT_ID} isPending streaming={sending(STREAMED)} />,
      { client: seededClient() },
    );

    const first = regionAnnouncing(STREAMED);

    rerender(
      <MessageList
        chatId={CHAT_ID}
        isPending
        streaming={sending(STREAMED_MORE)}
      />,
    );

    const second = regionAnnouncing(STREAMED_MORE);
    // Same node, longer text: the region is updated in place. A remounted
    // region would be announced from scratch on every single token.
    expect(second).toBe(first);
    expect(second).toHaveTextContent(STREAMED_MORE);
  });

  it("leaves aria-atomic off, so a token does not re-read the whole reply", () => {
    renderWithQueryClient(
      <MessageList chatId={CHAT_ID} isPending streaming={sending(STREAMED)} />,
      { client: seededClient() },
    );

    const live = regionAnnouncing(STREAMED);
    expect(live).not.toBeNull();
    // POSITIVE CONTROL for the absence: the copy affordance inside this very
    // bubble DOES set aria-atomic, so a reader that cannot see the attribute
    // would report both as absent and prove nothing.
    expect(live?.querySelector("[aria-atomic]")).not.toBeNull();
    expect(live?.hasAttribute("aria-atomic")).toBe(false);
  });
});
