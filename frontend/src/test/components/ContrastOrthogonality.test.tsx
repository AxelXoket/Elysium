/**
 * ContrastOrthogonality.test.tsx - v1.1 E2 orthogonality contract.
 *
 * The two class families are independent and co-resident on the scroller:
 *   chat-bg-dark   -> bare-canvas chrome over a dark wallpaper
 *   msg-contrast-* -> bubble-surface vars
 * A dark wallpaper AND a High-contrast preset must both apply at once.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
    </QueryClientProvider>
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
    useUiStore.setState({ msgContrast: "default", selectedChatId: null });
  });

  it("applies chat-bg-dark AND msg-contrast-high simultaneously", () => {
    mockFetch({
      "/settings": { body: { api_key_set: true, proxy_required: false } },
      "/chats/1/messages": { body: [] },
      "/chats": { body: [{ id: 1, title: "c", character_id: 1, message_count: 0 }] },
    });
    const { container } = render(<ChatCanvas />, { wrapper });

    const scroller = container.querySelector(
      ".flex-1.overflow-y-auto",
    ) as HTMLElement;
    expect(scroller.className).toContain("chat-bg-dark");
    expect(scroller.className).toContain("msg-contrast-high");
  });
});
