/**
 * A question nobody answered needs a way to be asked again.
 *
 * Delete a reply and its question stays, now at the end of the chat with
 * nothing after it - and there is no route back. The regenerate arrow lives on
 * the assistant bubble, which is the row that was just deleted. The composer's
 * Send needs new text, so an empty composer reads as a dead button. The only
 * way through was to press Edit and then Save without changing a word.
 *
 * That trick called the right endpoint all along: the backend's edit target
 * only has to be a user row, and with nothing after it there is nothing to
 * sweep, so it simply writes a fresh reply. This turns the trick into a button.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";

import { MessageBubble } from "@/components/chat/MessageBubble";
import { useUiStore } from "@/lib/store/uiStore";
import type { Message } from "@/lib/schemas/chats";


function msg(id: number, role: "user" | "assistant", content: string): Message {
  return {
    id,
    chat_id: 1,
    role,
    content,
    created_at: "2026-01-01T00:00:00Z",
    attachments: [],
    variant_group: null,
    active: true,
    variant_index: 0,
    variant_count: 1,
  } as Message;
}

const QUESTION = msg(2, "user", "what happened next?");
const REPLY = msg(3, "assistant", "and then...");

function show(messages: Message[], message: Message, onEditMessage = vi.fn()) {
  renderWithQueryClient(<MessageBubble
      chatId={1}
      message={message}
      messages={messages}
      onEditMessage={onEditMessage}
    />);
  return onEditMessage;
}

describe("a user message with no reply", () => {
  beforeEach(() => {
    useUiStore.setState({ selectedModelId: "openai/gpt-4o" });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("offers a way to get one", () => {
    show([msg(1, "assistant", "hi"), QUESTION], QUESTION);
    expect(
      screen.getByRole("button", { name: /get a reply/i }),
    ).toBeInTheDocument();
  });

  it("asks again with the text untouched", () => {
    const onEdit = show([msg(1, "assistant", "hi"), QUESTION], QUESTION);
    fireEvent.click(screen.getByRole("button", { name: /get a reply/i }));
    expect(onEdit).toHaveBeenCalledWith(QUESTION.id, "what happened next?");
  });

  it("stays out of the way once the message HAS a reply", () => {
    show([msg(1, "assistant", "hi"), QUESTION, REPLY], QUESTION);
    expect(
      screen.queryByRole("button", { name: /get a reply/i }),
    ).not.toBeInTheDocument();
  });

  it("never appears on an assistant row", () => {
    show([msg(1, "assistant", "hi"), QUESTION, REPLY], REPLY);
    expect(
      screen.queryByRole("button", { name: /get a reply/i }),
    ).not.toBeInTheDocument();
  });

  it("waits for a model, like send and edit do", () => {
    useUiStore.setState({ selectedModelId: null });
    show([msg(1, "assistant", "hi"), QUESTION], QUESTION);
    expect(screen.getByRole("button", { name: /get a reply/i })).toBeDisabled();
  });

  it("leaves the edit button exactly where it was", () => {
    show([msg(1, "assistant", "hi"), QUESTION], QUESTION);
    expect(
      screen.getByRole("button", { name: /edit message/i }),
    ).toBeInTheDocument();
  });
});
