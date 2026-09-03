import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { ChatCanvas } from "@/components/chat/ChatCanvas";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "../mocks/api";
import {
  LAUNCH_TOKEN_HEADER,
  __setLaunchTokenForTest,
} from "@/lib/api/launchToken";
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

  /** Routes that serve both attachment binaries. The thumbnail FETCHES them
   *  now (with the launch token) rather than letting the element load a URL,
   *  so a test that does not serve them is testing the failure path. */
  const servesBothImages = {
    "/uploads/images/9": { body: {} },
    "/uploads/images/10": { body: {} },
  };

  let blobCount = 0;

  beforeEach(() => {
    vi.restoreAllMocks();
    // jsdom implements neither half of the object-URL API. Naming each blob
    // after the call that made it is what lets the assertions below tell one
    // attachment's bytes from the other's.
    blobCount = 0;
    URL.createObjectURL = vi.fn(() => `blob:attachment-${++blobCount}`);
    URL.revokeObjectURL = vi.fn();
    useUiStore.setState({
      selectedChatId: 1,
      selectedCharacterId: 1,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    Reflect.deleteProperty(URL, "createObjectURL");
    Reflect.deleteProperty(URL, "revokeObjectURL");
    __setLaunchTokenForTest(null);
  });

  it("renders a thumbnail per attachment above the message text", async () => {
    // This case used to assert the src WAS the API url, which is the shape
    // that forced the launch-token gate to accept a browser header in the
    // token's place on this route. The bytes are fetched now and the element
    // is pointed at a blob, so the blob is what gets asserted.
    mockFetch({
      "/chats/1/messages": {
        body: [messageFixture, userMessageWithImages],
      },
      ...servesBothImages,
    });

    renderWithQueryClient(<ChatCanvas />, { wrapper });

    expect(await screen.findByText("Look at these")).toBeInTheDocument();
    const thumbs = screen.getAllByAltText("attached image");
    expect(thumbs).toHaveLength(2);
    await waitFor(() =>
      expect(thumbs[0]).toHaveAttribute("src", "blob:attachment-1"),
    );
    await waitFor(() =>
      expect(thumbs[1]).toHaveAttribute("src", "blob:attachment-2"),
    );
    // Nothing is still pointed at the raw binary route.
    for (const thumb of thumbs) {
      expect(thumb.getAttribute("src")).not.toContain("/uploads/images/");
    }
  });

  it("AC-2: the thumbnail asks for the bytes with the launch token", async () => {
    // What is measured is the ARGUMENT the mocked fetch received. Without the
    // header the backend gate answers 403 and no picture ever arrives.
    __setLaunchTokenForTest("token-for-this-launch");
    const fetchMock = mockFetch({
      "/chats/1/messages": {
        body: [messageFixture, userMessageWithImages],
      },
      ...servesBothImages,
    });

    const view = renderWithQueryClient(<ChatCanvas />, { wrapper });
    await screen.findByText("Look at these");

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) =>
        String(c[0]).includes("/uploads/images/9"),
      );
      expect(call).toBeDefined();
      const headers = (call![1]?.headers ?? {}) as Record<string, string>;
      expect(headers[LAUNCH_TOKEN_HEADER]).toBe("token-for-this-launch");
    });
    view.unmount();

    // GROUND CONTROL: the header comes from the token module rather than
    // being a constant this test hardcodes on both sides. With no token
    // issued - the developer path, where the gate is unarmed - the very same
    // request goes out carrying no such header at all.
    __setLaunchTokenForTest(null);
    fetchMock.mockClear();
    const second = renderWithQueryClient(<ChatCanvas />, { wrapper });
    await screen.findByText("Look at these");
    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) =>
        String(c[0]).includes("/uploads/images/9"),
      );
      expect(call).toBeDefined();
      const headers = (call![1]?.headers ?? {}) as Record<string, string>;
      expect(headers[LAUNCH_TOKEN_HEADER]).toBeUndefined();
    });
    second.unmount();
  });

  it("AC-2: the blob is revoked when the thumbnail goes away", async () => {
    // A blob: URL nobody revokes pins its bytes for the life of the document,
    // which here is the life of the window. Fetching the picture instead of
    // letting the element load it is only correct if the lifetime closes too.
    mockFetch({
      "/chats/1/messages": {
        body: [messageFixture, userMessageWithImages],
      },
      ...servesBothImages,
    });

    const view = renderWithQueryClient(<ChatCanvas />, { wrapper });
    await screen.findByText("Look at these");
    await waitFor(() =>
      expect(screen.getAllByAltText("attached image")[0]).toHaveAttribute(
        "src",
        "blob:attachment-1",
      ),
    );
    expect(URL.revokeObjectURL).not.toHaveBeenCalledWith("blob:attachment-1");

    view.unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:attachment-1");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:attachment-2");
  });

  it("opens a lightbox on thumbnail click and closes it with Escape", async () => {
    const user = userEvent.setup();
    mockFetch({
      "/chats/1/messages": {
        body: [messageFixture, userMessageWithImages],
      },
      ...servesBothImages,
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
    // The first binary really 404s. This used to fire a synthetic `error`
    // event at the element, which was the only way in while the BROWSER did
    // the loading; fetching makes the real refusal reachable.
    mockFetch({
      "/chats/1/messages": {
        body: [messageFixture, userMessageWithImages],
      },
      "/uploads/images/9": {
        status: 404,
        body: { detail: "attachment_not_found" },
      },
      "/uploads/images/10": { body: {} },
    });

    renderWithQueryClient(<ChatCanvas />, { wrapper });

    await screen.findByText("Look at these");

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
      "/uploads/images/9": {
        status: 404,
        body: { detail: "attachment_not_found" },
      },
      "/uploads/images/10": { body: {} },
    });

    renderWithQueryClient(<ChatCanvas />, { wrapper });
    await screen.findByText("Look at these");

    await user.click(
      screen.getByRole("button", { name: "View attached image 1 of 2" }),
    );
    const dialog = await screen.findByRole("dialog");

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
