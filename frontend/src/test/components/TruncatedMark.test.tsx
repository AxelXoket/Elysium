/**
 * The truncation mark (owner's ask, verbatim): when a reply stopped because
 * it hit the token ceiling rather than because the model finished its
 * sentence, the bubble must show a mark - not render the half-sentence as if
 * it were whole with nothing to say otherwise.
 *
 * `truncated` is a backend-added field on the message contract. It must
 * round-trip through MessageSchema (a stripped or rejected field would fail
 * silently or 400 an otherwise-valid vault) and reach the bubble only for
 * the assistant row it actually describes.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { MessageList } from "@/components/chat/MessageList";
import { TRUNCATED_MARK_TEXT } from "@/components/chat/MessageBubble";
import { MessageSchema } from "@/lib/schemas/chats";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "../mocks/api";
import type { ReactNode } from "react";
import type { Message } from "@/lib/schemas/chats";

function wrapper({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

function message(
  id: number,
  role: "user" | "assistant",
  content: string,
  extra: Partial<Message> = {},
): Message {
  return {
    id,
    chat_id: 1,
    role,
    content,
    created_at: `2026-01-01T00:0${id}:00Z`,
    ...extra,
  };
}

function getBubbleByText(text: string): HTMLElement {
  const bubble = screen.getByText(text).closest(".message-bubble-shell");
  expect(bubble).not.toBeNull();
  return bubble as HTMLElement;
}

describe("truncated mark", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: 1,
      selectedCharacterId: 1,
      selectedModelId: "openai/gpt-4o",
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("schema: MessageSchema keeps truncated: true rather than stripping it", () => {
    const parsed = MessageSchema.parse({
      id: 1,
      chat_id: 1,
      role: "assistant",
      content: "cut off mid-",
      created_at: "2026-01-01T00:00:00Z",
      truncated: true,
    });
    expect(parsed.truncated).toBe(true);
  });

  it("schema: an older vault row with no truncated column still parses (nullish)", () => {
    const parsed = MessageSchema.parse({
      id: 1,
      chat_id: 1,
      role: "assistant",
      content: "complete sentence.",
      created_at: "2026-01-01T00:00:00Z",
    });
    expect(parsed.truncated).toBeUndefined();
  });

  it("GROUND: a normal complete reply shows no truncated mark", async () => {
    mockFetch({
      "/chats/1/messages": {
        body: [
          message(1, "user", "A question"),
          message(2, "assistant", "A whole answer."),
        ],
      },
    });

    renderWithQueryClient(<MessageList chatId={1} />, { wrapper });

    const bubble = await screen.findByText("A whole answer.").then((el) =>
      el.closest(".message-bubble-shell"),
    );
    expect(bubble).not.toBeNull();
    expect(
      within(bubble as HTMLElement).queryByText(TRUNCATED_MARK_TEXT),
    ).not.toBeInTheDocument();
  });

  it("POSITIVE CONTROL: an assistant reply with truncated: true shows the mark", async () => {
    mockFetch({
      "/chats/1/messages": {
        body: [
          message(1, "user", "A question"),
          message(2, "assistant", "Cut off mid-sent", { truncated: true }),
        ],
      },
    });

    renderWithQueryClient(<MessageList chatId={1} />, { wrapper });

    await screen.findByText("Cut off mid-sent");
    const bubble = getBubbleByText("Cut off mid-sent");
    expect(
      within(bubble).getByText(TRUNCATED_MARK_TEXT),
    ).toBeInTheDocument();
  });

  it("a user message is never marked truncated, even if the field is set", async () => {
    // The field only ever describes a GENERATED reply - a user row carrying
    // it (a stray value from a bad merge, say) must not show the mark.
    mockFetch({
      "/chats/1/messages": {
        body: [message(1, "user", "User text", { truncated: true } as never)],
      },
    });

    renderWithQueryClient(<MessageList chatId={1} />, { wrapper });

    await screen.findByText("User text");
    const bubble = getBubbleByText("User text");
    expect(
      within(bubble).queryByText(TRUNCATED_MARK_TEXT),
    ).not.toBeInTheDocument();
  });

  it("truncated: false shows no mark (only true marks the bubble)", async () => {
    mockFetch({
      "/chats/1/messages": {
        body: [
          message(1, "user", "A question"),
          message(2, "assistant", "A whole answer, really.", {
            truncated: false,
          }),
        ],
      },
    });

    renderWithQueryClient(<MessageList chatId={1} />, { wrapper });

    const bubble = await screen
      .findByText("A whole answer, really.")
      .then((el) => el.closest(".message-bubble-shell"));
    expect(bubble).not.toBeNull();
    expect(
      within(bubble as HTMLElement).queryByText(TRUNCATED_MARK_TEXT),
    ).not.toBeInTheDocument();
  });
});
