import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatCanvas } from "@/components/chat/ChatCanvas";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "../mocks/api";
import { messageFixture } from "../mocks/fixtures";
import type { Message } from "@/lib/schemas/chats";
import type { ReactNode } from "react";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={qc}>
      <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
    </QueryClientProvider>
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
    render(<ChatCanvas />, { wrapper });
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

    render(<ChatCanvas />, { wrapper });

    expect(
      await screen.findByText("Hello! I'm a test character."),
    ).toBeInTheDocument();
  });

  // T-29: Composer send is disabled
  it("T-29: composer send button is disabled", () => {
    render(<ChatCanvas />, { wrapper });
    const sendBtn = screen.getByRole("button", { name: /send message/i });
    expect(sendBtn).toBeDisabled();
  });

  // T-30: Composer input is disabled when no chat selected
  it("T-30: composer input is disabled", () => {
    render(<ChatCanvas />, { wrapper });
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

    render(<ChatCanvas />, { wrapper });

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

    render(<ChatCanvas />, { wrapper });
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

    render(<ChatCanvas />, { wrapper });

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

    render(<ChatCanvas />, { wrapper });
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

    render(<ChatCanvas />, { wrapper });

    expect(
      await screen.findByText("Hello! I'm a test character."),
    ).toBeInTheDocument();
    expect(screen.queryByAltText("attached image")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /view attached image/i }),
    ).not.toBeInTheDocument();
  });
});
