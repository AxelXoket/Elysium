import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { ChatCanvas } from "@/components/chat/ChatCanvas";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "../mocks/api";
import { messageFixture } from "../mocks/fixtures";
import type { Message } from "@/lib/schemas/chats";
import type { ReactNode } from "react";

function wrapper({ children }: { children: ReactNode }) {
  return (
    
      <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
    
  );
}

describe("Messages", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: null,
      selectedCharacterId: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // T-27: Empty state when no chat selected. The welcome line is now a
  // "Welcome to" label + the Elysium wordmark (two nodes), so assert on the
  // stable instruction copy instead of the split heading.
  it("T-27: shows empty state when no chat selected", () => {
    renderWithQueryClient(<ChatCanvas />, { wrapper });
    expect(
      screen.getByText("Select a character and start a chat to begin."),
    ).toBeInTheDocument();
    expect(screen.getByText("Welcome to")).toBeInTheDocument();
  });

  // T-28: Messages rendered in read-only mode
  it("T-28: renders messages read-only for selected chat", async () => {
    mockFetch({
      "/chats/1/messages": { body: [messageFixture] },
    });
    useUiStore.setState({ selectedChatId: 1 });

    renderWithQueryClient(<ChatCanvas />, { wrapper });

    expect(
      await screen.findByText("Hello! I'm a test character."),
    ).toBeInTheDocument();
  });

  it("a history that failed to load does not read as a chat with no history", async () => {
    // The worst sentence this surface can produce. MessageList checks `error`
    // before the empty branch, and the empty branch says "no messages yet" -
    // so if those two ever swap, or the error stops reaching the component,
    // somebody whose conversation failed to load is told their conversation
    // is empty. Nothing was driving the error branch at all: no test in this
    // suite ever made /chats/:id/messages fail, which meant the ordering that
    // keeps the two apart was never exercised.
    mockFetch({
      "/chats/1/messages": { status: 500, body: { detail: "internal_error" } },
    });
    useUiStore.setState({ selectedChatId: 1 });

    renderWithQueryClient(<ChatCanvas />, { wrapper });

    expect(
      await screen.findByText(/something went wrong/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/no messages yet/i),
      "a failed load was presented as an empty conversation",
    ).not.toBeInTheDocument();
  });

  // T-29: Composer send is disabled
  it("T-29: composer send button is disabled", () => {
    renderWithQueryClient(<ChatCanvas />, { wrapper });
    const sendBtn = screen.getByRole("button", { name: /send message/i });
    expect(sendBtn).toBeDisabled();
  });

  // T-30: Composer input is disabled when no chat selected
  it("T-30: composer input is disabled", () => {
    renderWithQueryClient(<ChatCanvas />, { wrapper });
    const input = screen.getByLabelText("Message");
    expect(input).toBeDisabled();
  });
});

describe("Message attachments", () => {
  const userMessageWithImages: Message = {
    id: 2,
    chat_id: 1,
    role: "user",
    content: "Look at these",
    created_at: "2026-01-01T00:01:00",
    attachments: [
      { id: 9, mime: "image/png", width: 640, height: 480 },
      { id: 10, mime: "image/webp", width: 320, height: 200 },
    ],
  };

  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: 1,
      selectedCharacterId: 1,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders a thumbnail per attachment above the message text", async () => {
    mockFetch({
      "/chats/1/messages": {
        body: [messageFixture, userMessageWithImages],
      },
    });

    renderWithQueryClient(<ChatCanvas />, { wrapper });

    expect(await screen.findByText("Look at these")).toBeInTheDocument();
    const thumbs = screen.getAllByAltText("attached image");
    expect(thumbs).toHaveLength(2);
    expect(thumbs[0]).toHaveAttribute(
      "src",
      "http://127.0.0.1:8787/api/v1/uploads/images/9",
    );
    expect(thumbs[1]).toHaveAttribute(
      "src",
      "http://127.0.0.1:8787/api/v1/uploads/images/10",
    );
  });

  it("opens a lightbox on thumbnail click and closes it with Escape", async () => {
    const user = userEvent.setup();
    mockFetch({
      "/chats/1/messages": {
        body: [messageFixture, userMessageWithImages],
      },
    });

    renderWithQueryClient(<ChatCanvas />, { wrapper });
    await screen.findByText("Look at these");

    // Two thumbnails; the lightbox is closed
    expect(screen.getAllByAltText("attached image")).toHaveLength(2);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "View attached image 1 of 2" }),
    );

    // Lightbox dialog shows the full-size image (same binary URL)
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(screen.getAllByAltText("attached image")).toHaveLength(3);

    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(screen.getAllByAltText("attached image")).toHaveLength(2);
  });

  // U1: broken/404 attachment binary → graceful placeholder, not the glyph.
  it("U1: a thumbnail that fails to load shows an image-unavailable placeholder", async () => {
    mockFetch({
      "/chats/1/messages": {
        body: [messageFixture, userMessageWithImages],
      },
    });

    renderWithQueryClient(<ChatCanvas />, { wrapper });

    await screen.findByText("Look at these");
    const thumbs = screen.getAllByAltText("attached image");
    expect(thumbs).toHaveLength(2);

    // Simulate the first binary 404ing.
    fireEvent.error(thumbs[0]);

    await waitFor(() => {
      expect(screen.getByText("Image unavailable")).toBeInTheDocument();
    });
    // Only the failed thumbnail swapped; the other still renders its image.
    expect(screen.getAllByAltText("attached image")).toHaveLength(1);
    // Layout stays clickable - the button is still there.
    expect(
      screen.getByRole("button", { name: "View attached image 1 of 2" }),
    ).toBeInTheDocument();
  });

  it("U1: the lightbox shows a placeholder when the full image fails", async () => {
    const user = userEvent.setup();
    mockFetch({
      "/chats/1/messages": {
        body: [messageFixture, userMessageWithImages],
      },
    });

    renderWithQueryClient(<ChatCanvas />, { wrapper });
    await screen.findByText("Look at these");

    await user.click(
      screen.getByRole("button", { name: "View attached image 1 of 2" }),
    );
    const dialog = await screen.findByRole("dialog");
    fireEvent.error(within(dialog).getByAltText("attached image"));

    await waitFor(() => {
      expect(within(dialog).getByText("Image unavailable")).toBeInTheDocument();
    });
  });

  it("renders text-only messages without attachment thumbnails", async () => {
    mockFetch({
      "/chats/1/messages": { body: [messageFixture] },
    });

    renderWithQueryClient(<ChatCanvas />, { wrapper });

    expect(
      await screen.findByText("Hello! I'm a test character."),
    ).toBeInTheDocument();
    expect(screen.queryByAltText("attached image")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /view attached image/i }),
    ).not.toBeInTheDocument();
  });

  // ── v1.1 A1: reserved thumbnail box - layout height is right BEFORE any
  // image bytes load (the root cause of the send-scroll undershoot).
  it("A1: attachment img carries a metadata-derived fixed box before load", async () => {
    useUiStore.setState({ selectedChatId: 1, selectedCharacterId: 1 });
    mockFetch({
      "/chats/1/messages": {
        body: [
          {
            ...messageFixture,
            id: 2,
            role: "user",
            content: "with image",
            attachments: [
              // 800x600 -> scale = min(1, 200/600, 320/800) = 1/3 -> 267x200
              { id: 11, mime: "image/png", width: 800, height: 600 },
            ],
          } as Message,
        ],
      },
    });
    renderWithQueryClient(<ChatCanvas />, { wrapper });

    const img = (await screen.findByAltText("attached image")) as HTMLImageElement;
    // The box is styled explicitly - NOT h-auto/w-auto - so the unloaded img
    // occupies its final size and scrollHeight is truthful at send time.
    expect(img.style.width).toBe("267px");
    expect(img.style.height).toBe("200px");
    expect(img.className).not.toMatch(/h-auto|w-auto|max-h-\[200px\]/);
    expect(img.getAttribute("decoding")).toBe("async");
  });

  // ── v1.1 FF5: long chats render older history statically - only the
  // newest window animates (no multi-second stagger cascade).
  it("FF5: only the newest ~16 groups get the animated wrapper", async () => {
    useUiStore.setState({ selectedChatId: 1, selectedCharacterId: 1 });
    const many: Message[] = Array.from({ length: 40 }, (_, i) => ({
      ...messageFixture,
      id: i + 1,
      role: i % 2 === 0 ? "user" : "assistant",
      content: `msg ${i + 1}`,
    })) as Message[];
    mockFetch({ "/chats/1/messages": { body: many } });
    const { container } = renderWithQueryClient(<ChatCanvas />, { wrapper });

    await screen.findByText("msg 40");
    const list = container.querySelector(".space-y-4") as HTMLElement;
    const wrappers = Array.from(list.children) as HTMLElement[];
    expect(wrappers).toHaveLength(40);
    // Static history: plain divs, no motion inline style.
    expect(wrappers[0].getAttribute("style")).toBeNull();
    expect(wrappers[10].getAttribute("style")).toBeNull();
    // Animated tail: motion wrappers carry an inline style.
    expect(wrappers[39].getAttribute("style")).not.toBeNull();
    expect(wrappers[24].getAttribute("style")).not.toBeNull();
  });
});
