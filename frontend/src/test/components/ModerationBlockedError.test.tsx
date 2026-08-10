/**
 * A refused message must not send the reader to the API key screen.
 *
 * The backend used to map the provider's 403 moderation block onto the same
 * reason as a 401, so it arrived here as `auth_failed`. Two things then went
 * wrong at once, and only one of them is a sentence: the banner said
 * "Authentication failed. Please check your API key.", AND the composer put a
 * gear button next to it that jumps straight to the Secrets tab. So the app did
 * not merely misdescribe the failure, it offered a one-click path to the wrong
 * fix. That button is what these tests are really about; the wording is the
 * easy half.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { ChatCanvas } from "@/components/chat/ChatCanvas";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useUiStore } from "@/lib/store/uiStore";
import { getErrorMessage } from "@/lib/errors/errorMessages";
import { mockFetch } from "../mocks/api";
import { settingsFixture, messageFixture } from "../mocks/fixtures";
import type { ReactNode } from "react";

function wrapper({ children }: { children: ReactNode }) {
  return (
    
      <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
    
  );
}

function setupReadyState() {
  useUiStore.setState({
    selectedChatId: 1,
    selectedModelId: "openai/gpt-4o",
    selectedCharacterId: 1,
  });
}

/** Send one message against a /complete that fails with the given code. */
async function sendAgainst(status: number, detail: string) {
  const user = userEvent.setup();
  setupReadyState();
  mockFetch({
    "/settings": { body: settingsFixture },
    "/chats/1/messages": { body: [messageFixture] },
    "/chats/1/complete": { status, body: { detail } },
  });
  renderWithQueryClient(<ChatCanvas />, { wrapper });

  await waitFor(() => {
    expect(screen.getByLabelText("Message")).not.toBeDisabled();
  });
  await user.type(screen.getByLabelText("Message"), "Test");
  await user.click(screen.getByRole("button", { name: /send message/i }));
  await waitFor(() => {
    expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
  });
}

describe("a moderation block", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: null,
      selectedModelId: null,
      selectedCharacterId: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does not offer the go-to-Secrets button", async () => {
    await sendAgainst(403, "openrouter_moderation_blocked");
    expect(
      screen.queryByRole("button", { name: "Go to Secrets" }),
    ).not.toBeInTheDocument();
  });

  it("still offers it for a real auth failure", async () => {
    // The negative above is only worth anything next to this: it proves the
    // button was reachable in this exact harness and that the moderation code
    // is what suppresses it, rather than the test never rendering a CTA at all.
    await sendAgainst(401, "auth_failed");
    expect(
      await screen.findByRole("button", { name: "Go to Secrets" }),
    ).toBeInTheDocument();
  });

  it("does not blame the API key in its wording", async () => {
    await sendAgainst(403, "openrouter_moderation_blocked");
    const banner = screen.getAllByRole("alert")[0];
    expect(banner.textContent?.toLowerCase()).not.toContain("api key");
    expect(banner.textContent?.toLowerCase()).not.toContain("authentication");
  });

  it("says something specific rather than falling through", async () => {
    // A missing map entry is a silent regression: the code still renders, just
    // as "Something went wrong. Please try again."
    const message = getErrorMessage("openrouter_moderation_blocked");
    expect(message).not.toBe("Something went wrong. Please try again.");
    expect(message).not.toBe(getErrorMessage("auth_failed"));
    await sendAgainst(403, "openrouter_moderation_blocked");
    expect(screen.getAllByText(message).length).toBeGreaterThan(0);
  });
});
