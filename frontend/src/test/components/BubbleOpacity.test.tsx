/**
 * Seeing the wallpaper through the messages.
 *
 * The setting only earns its place if the bubble thins and the WORDS DO NOT.
 * That is the whole reason it is a colour function rather than `opacity`, and
 * it is the one property that cannot be checked by reading the slider: the
 * naive implementation looks correct in the store and unreadable on screen.
 */
import { screen } from "@testing-library/react";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { ChatCanvas } from "@/components/chat/ChatCanvas";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useUiStore, MSG_OPACITY_DEFAULT } from "@/lib/store/uiStore";
import { mockFetch } from "../mocks/api";
import { messageFixture } from "../mocks/fixtures";

function wrapper({ children }: { children: ReactNode }) {
  return (
    
      <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
    
  );
}

async function bubbles() {
  const text = await screen.findByText(messageFixture.content);
  const shell = text.closest(".message-bubble-shell");
  expect(shell).not.toBeNull();
  return shell as HTMLElement;
}

beforeEach(() => {
  vi.restoreAllMocks();
  mockFetch({
    "/chats/1/messages": { body: [messageFixture] },
  });
  useUiStore.setState({ selectedChatId: 1, msgOpacity: MSG_OPACITY_DEFAULT });
});

describe("a chat nobody has restyled looks exactly as it did", () => {
  it("paints the bubble with the plain variable at full solidity", async () => {
    // THE ZERO-CHANGE CONTRACT at the point it is actually visible. Not a
    // 100% colour mix, which paints the same but makes today's look depend on
    // a colour function being a perfect identity.
    renderWithQueryClient(<ChatCanvas />, { wrapper });
    const shell = await bubbles();
    expect(shell.style.backgroundColor).toBe(
      "var(--msg-asst-bg, var(--color-es-asst-bubble))",
    );
  });
});

describe("thinning the bubble leaves the words alone", () => {
  it("mixes the fill towards transparent", async () => {
    useUiStore.setState({ msgOpacity: 0.5 });
    renderWithQueryClient(<ChatCanvas />, { wrapper });
    const shell = await bubbles();
    expect(shell.style.backgroundColor).toContain("color-mix");
    expect(shell.style.backgroundColor).toContain("50%");
  });

  it("never touches `opacity`, which would fade the text with it", async () => {
    // The failure this exists for: `opacity: 0.4` on the shell looks right in
    // a screenshot of an empty bubble and makes the message unreadable in a
    // full one. Half the range would be unusable and nobody would know why.
    useUiStore.setState({ msgOpacity: 0.4 });
    renderWithQueryClient(<ChatCanvas />, { wrapper });
    const shell = await bubbles();
    expect(shell.style.opacity).toBe("");
  });

  it("leaves the ink colour at full strength", async () => {
    useUiStore.setState({ msgOpacity: 0.4 });
    renderWithQueryClient(<ChatCanvas />, { wrapper });
    const shell = await bubbles();
    expect(shell.style.color).toBe(
      "var(--msg-asst-fg, var(--color-es-asst-bubble-text))",
    );
  });
});
