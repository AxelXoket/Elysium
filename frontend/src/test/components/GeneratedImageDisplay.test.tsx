/**
 * A picture the model drew, on screen.
 *
 * The render path was already role-blind before image output existed, which
 * sounds like good news and is actually the hazard: it means the images appear
 * with no component change, so nothing forced anyone to look at what happens
 * when they do. Two things happen, and both are here.
 *
 * The bubble renders the thumbnail (dormant capability, now exercised on an
 * ASSISTANT row for the first time), and the scroll machine has to notice. It
 * could not: both follow effects key on a text length, and an image finishing
 * its decode grows the page without changing any length. The reader silently
 * lost the bottom, and because `nearBottom` is only recomputed by the scroll
 * listener, the JumpToLatest affordance did not appear either.
 *
 * jsdom has no layout, so scroll geometry is stubbed exactly as
 * ChatCanvasScroll.test.tsx does it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import type { ReactNode } from "react";

import { ChatCanvas } from "@/components/chat/ChatCanvas";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useUiStore } from "@/lib/store/uiStore";
import { jsonResponse, mockFetchWithStreams } from "../helpers/streamMocks";
import { settingsFixture } from "../mocks/fixtures";
import type { Message } from "@/lib/schemas/chats";

function wrapper({ children }: { children: ReactNode }) {
  return (
    
      <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
    
  );
}

function msg(
  id: number,
  role: "user" | "assistant",
  content: string,
  attachments: Message["attachments"] = [],
): Message {
  return {
    id,
    chat_id: 1,
    role,
    content,
    created_at: "2026-01-01T00:00:00Z",
    attachments,
  } as Message;
}

const GENERATED = [{ id: 42, mime: "image/png", width: 512, height: 512 }];

function stubScroller(container: HTMLElement) {
  const el = container.querySelector(".flex-1.overflow-y-auto") as HTMLElement;
  expect(el).not.toBeNull();
  let scrollTop = 0;
  Object.defineProperty(el, "scrollHeight", { value: 2000, configurable: true });
  Object.defineProperty(el, "clientHeight", { value: 600, configurable: true });
  Object.defineProperty(el, "scrollTop", {
    configurable: true,
    get: () => scrollTop,
    set: (v: number) => {
      scrollTop = v;
    },
  });
  const scrollToSpy = vi.fn((opts: ScrollToOptions) => {
    if (typeof opts?.top === "number") {
      scrollTop = opts.top;
      el.dispatchEvent(new Event("scroll"));
    }
  });
  Object.defineProperty(el, "scrollTo", {
    configurable: true,
    value: scrollToSpy,
  });
  return {
    el,
    scrollToSpy,
    setDistanceFromBottom(px: number) {
      scrollTop = 2000 - 600 - px;
      fireEvent.scroll(el);
    },
  };
}

describe("a generated image in the transcript", () => {
  let body: Message[];

  beforeEach(() => {
    vi.restoreAllMocks();
    body = [
      msg(1, "assistant", "greeting"),
      msg(2, "user", "draw me something"),
      msg(3, "assistant", "here you go", GENERATED),
    ];
    useUiStore.setState({
      selectedChatId: 1,
      selectedCharacterId: 1,
      selectedModelId: "openai/gpt-4o",
    });
    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { response: () => jsonResponse(body) },
      "/chats": { response: () => jsonResponse([]) },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  async function renderCanvas() {
    const utils = renderWithQueryClient(<ChatCanvas />, { wrapper });
    const stub = stubScroller(utils.container);
    await screen.findByText("here you go");
    return { ...utils, ...stub };
  }

  it("shows the picture on an assistant reply", async () => {
    await renderCanvas();
    const thumb = await screen.findByRole("button", {
      name: /view attached image 1 of 1/i,
    });
    expect(thumb).toBeInTheDocument();
    const img = thumb.querySelector("img");
    expect(img).not.toBeNull();
    expect(img!.getAttribute("src")).toContain("/uploads/images/42");
  });

  it("asks the server for the bytes rather than carrying them inline", async () => {
    await renderCanvas();
    const img = (
      await screen.findByRole("button", { name: /view attached image/i })
    ).querySelector("img")!;
    const src = img.getAttribute("src") ?? "";
    expect(src.startsWith("data:")).toBe(false);
    expect(src).not.toContain("base64");
  });

  it("re-locks the scroll when the picture finishes loading", async () => {
    const { el, scrollToSpy } = await renderCanvas();
    await waitFor(() => expect(scrollToSpy).toHaveBeenCalled());
    scrollToSpy.mockClear();

    const img = (
      await screen.findByRole("button", { name: /view attached image/i })
    ).querySelector("img")!;
    // The reader is at the bottom, and the image just grew the page.
    fireEvent.load(img);
    expect(scrollToSpy).toHaveBeenCalledWith({
      top: el.scrollHeight,
      behavior: "instant",
    });
  });

  it("does not yank a reader who has scrolled up", async () => {
    const { scrollToSpy, setDistanceFromBottom } = await renderCanvas();
    await waitFor(() => expect(scrollToSpy).toHaveBeenCalled());
    setDistanceFromBottom(900);
    scrollToSpy.mockClear();

    const img = (
      await screen.findByRole("button", { name: /view attached image/i })
    ).querySelector("img")!;
    fireEvent.load(img);
    expect(scrollToSpy).not.toHaveBeenCalled();
  });

  // Weaker than its neighbours, deliberately labelled so: the affordance is
  // pre-existing behaviour of the scroll listener, so this passes with or
  // without the load handler. It is here as a regression guard on the
  // COMBINATION - a picture landing while the reader is away must not be the
  // thing that hides their way back - not as coverage of the handler itself.
  // The handler is covered by the two tests above.
  it("still offers a way back to the bottom while the reader is away", async () => {
    const { scrollToSpy, setDistanceFromBottom } = await renderCanvas();
    await waitFor(() => expect(scrollToSpy).toHaveBeenCalled());
    setDistanceFromBottom(900);
    const img = (
      await screen.findByRole("button", { name: /view attached image/i })
    ).querySelector("img")!;
    fireEvent.load(img);
    expect(
      await screen.findByRole("button", { name: /jump to latest/i }),
    ).toBeInTheDocument();
  });

  it("opens the picture full size", async () => {
    await renderCanvas();
    const thumb = await screen.findByRole("button", {
      name: /view attached image/i,
    });
    fireEvent.click(thumb);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});
