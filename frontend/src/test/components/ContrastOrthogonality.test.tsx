/**
 * ContrastOrthogonality.test.tsx - what the appearance settings put ON the
 * chat scroller.
 *
 * The file started life for one bug, and that bug is still the first test:
 * chat-bg-dark and msg-contrast-* are applied through separate branches of
 * the same className expression, and an earlier build had one silently
 * overwrite the other instead of both landing. Nothing else renders
 * ChatCanvas with a mocked dark wallpaper AND a non-default preset at once.
 *
 * KADEME 19a widened it to its real subject. Four settings write to this one
 * element and three of them had NO render-level test at all - measured, not
 * guessed: a whole-tree grep for surface-glossy and surface-metallic found
 * only the CSS rule itself. They were proven at the store and the picker and
 * then simply assumed to arrive.
 *
 * jsdom applies no stylesheet, so the honest assertion is the class and the
 * custom property on the element, never a computed colour. index.css turns
 * these into real rules (surface-*.message-bubble-shell background gradients,
 * .msg-ink-custom overriding --msg-asst-fg); that half needs a browser.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "../mocks/api";
import type { ReactNode } from "react";

// Mock the wallpaper hook to report a dark image (jsdom has no image pipeline).
vi.mock("@/lib/appearance/useChatBackground", () => ({
  useChatBackground: () => ({
    style: { backgroundImage: 'linear-gradient(#000,#000), url("blob:x")' },
    dark: true,
  }),
  // ChatCanvas measures the scroller so a zoomed wallpaper crops against the
  // shape it is really painted on. This case is about class orthogonality,
  // not framing, so the stub reports "not measured" - which is also the
  // honest answer in jsdom, where nothing has a size.
  useAreaAspect: () => ({ ref: () => {}, aspect: null }),
}));

// Import AFTER the mock is registered.
import { ChatCanvas } from "@/components/chat/ChatCanvas";

function wrapper({ children }: { children: ReactNode }) {
  return (
    
      <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
    
  );
}

describe("E2 contrast/wallpaper orthogonality", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: 1,
      selectedCharacterId: 1,
      selectedModelId: "openai/gpt-4o",
      msgContrast: "high",
    });
  });

  afterEach(() => {
    useUiStore.setState({
      msgContrast: "default",
      selectedChatId: null,
      msgInk: null,
      surfaceFinish: "matte",
    });
  });

  /** The routes every render here needs; the scroller is what we inspect. */
  function renderScroller(): HTMLElement {
    mockFetch({
      "/settings": { body: { api_key_set: true, proxy_required: false } },
      "/chats/1/messages": { body: [] },
      "/chats": { body: [{ id: 1, title: "c", character_id: 1, message_count: 0 }] },
    });
    const { container } = renderWithQueryClient(<ChatCanvas />, { wrapper });
    const scroller = container.querySelector(".flex-1.overflow-y-auto");
    expect(scroller, "the chat scroller is gone").not.toBeNull();
    return scroller as HTMLElement;
  }

  it("applies chat-bg-dark AND msg-contrast-high simultaneously", () => {
    mockFetch({
      "/settings": { body: { api_key_set: true, proxy_required: false } },
      "/chats/1/messages": { body: [] },
      "/chats": { body: [{ id: 1, title: "c", character_id: 1, message_count: 0 }] },
    });
    const { container } = renderWithQueryClient(<ChatCanvas />, { wrapper });

    const scroller = container.querySelector(
      ".flex-1.overflow-y-auto",
    ) as HTMLElement;
    expect(scroller.className).toContain("chat-bg-dark");
    expect(scroller.className).toContain("msg-contrast-high");
  });
  it.each([
    ["glossy", "surface-glossy"],
    ["metallic", "surface-metallic"],
  ] as const)("carries the %s finish to the scroller", (finish, className) => {
    useUiStore.setState({ surfaceFinish: finish });
    expect(renderScroller().className).toContain(className);
  });

  it("says nothing at all for the default finish", () => {
    // The omission branch matters as much as the emission one: matte is
    // today's look, and it is expressed by adding NO class. A finish class
    // leaking in on matte would restyle every bubble for someone who never
    // touched the setting.
    useUiStore.setState({ surfaceFinish: "matte" });
    expect(renderScroller().className).not.toContain("surface-");
  });

  it("carries a custom ink as both a class and the value it needs", () => {
    // Two halves, and one without the other does nothing: the class is what
    // index.css hangs the override on, the custom property is what the
    // override reads. Until KADEME 19a neither was asserted anywhere - the
    // picker proved the store, and the store was assumed to arrive.
    useUiStore.setState({ msgInk: "#B9D4F0" });
    const scroller = renderScroller();
    expect(scroller.className).toContain("msg-ink-custom");
    expect(scroller.style.getPropertyValue("--msg-ink-custom")).toBe("#B9D4F0");
  });

  it("leaves the preset alone when no custom ink is chosen", () => {
    useUiStore.setState({ msgInk: null });
    const scroller = renderScroller();
    expect(scroller.className).not.toContain("msg-ink-custom");
    expect(scroller.style.getPropertyValue("--msg-ink-custom")).toBe("");
  });
});
