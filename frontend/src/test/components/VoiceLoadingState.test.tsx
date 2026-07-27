/**
 * VoiceLoadingState.test.tsx - what the voice controls do while the engine is
 * still coming up.
 *
 * The model now preloads in the background right after unlock, and a cold Fish
 * S2 pays a torch.compile its own progress line calls "first compile is slow".
 * So the first minute of a session is normally spent in `state: "loading"` -
 * and before this, nothing said so: the buttons looked exactly like a ready
 * engine, a press produced no sound, and on the /speak path an error toast
 * blamed a perfectly healthy engine that simply was not up yet.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { SpeakButton } from "@/components/chat/SpeakButton";
import { SpeakLiveButton } from "@/components/chat/SpeakLiveButton";
import { ContinuousVoiceToggle } from "@/components/chat/ContinuousVoiceToggle";
import { VOICE_LOADING_HINT } from "@/lib/voice/useVoiceReadiness";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "../mocks/api";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

const READINESS = {
  uid: "u1",
  engine_id: "fish_s2",
  runnable: true,
  settings_available: true,
  runtime_state: "ready",
  issues: [],
  languages: ["en"],
  fit: null,
};

function stubActive(state: string, extra: Record<string, unknown> = {}) {
  mockFetch({
    "/tts/active": {
      body: {
        uid: "u1",
        state,
        engine_id: "fish_s2",
        vram_mb: null,
        error_code: null,
        readiness: READINESS,
        voice_installed: true,
        ...extra,
      },
    },
  });
}

describe("voice controls while the model loads", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({ continuousVoice: false });
  });

  it("the per-message Speak button says so and pulses", async () => {
    stubActive("loading");
    render(<SpeakButton messageId={7} />, { wrapper });

    const button = await screen.findByRole("button", { name: "Speak message" });
    await waitFor(() => {
      expect(button).toHaveAttribute("data-voice-loading", "true");
    });
    expect(button).toHaveAttribute("title", VOICE_LOADING_HINT);
    expect(button).toHaveAttribute("aria-busy", "true");
    // Not clickable YET - a press that produced nothing was the old failure.
    expect(button).toBeDisabled();
  });

  it("the live-speak button says so and pulses", async () => {
    stubActive("loading");
    render(<SpeakLiveButton chatId={1} />, { wrapper });

    const button = await screen.findByRole("button", { name: "Speak this reply" });
    await waitFor(() => {
      expect(button).toHaveAttribute("data-voice-loading", "true");
    });
    expect(button).toHaveAttribute("title", VOICE_LOADING_HINT);
    expect(button).toBeDisabled();
  });

  it("the composer toggle says so but stays usable", async () => {
    // Turning "speak replies" on applies from the NEXT message, so there is
    // nothing to wait for - refusing the press would be the lie here.
    stubActive("loading");
    render(<ContinuousVoiceToggle />, { wrapper });

    const toggle = await screen.findByRole("switch");
    await waitFor(() => {
      expect(toggle).toHaveAttribute("data-voice-loading", "true");
    });
    expect(toggle).toHaveAttribute("title", VOICE_LOADING_HINT);
    expect(toggle).not.toBeDisabled();
  });

  it("once loaded, the controls are ordinary again", async () => {
    stubActive("loaded");
    render(<SpeakButton messageId={7} />, { wrapper });

    const button = await screen.findByRole("button", { name: "Speak message" });
    await waitFor(() => expect(button).toBeEnabled());
    expect(button).not.toHaveAttribute("data-voice-loading");
    expect(button).toHaveAttribute("title", "Speak message");
  });

  it("a model that cannot run still renders nothing at all", async () => {
    // Loading is "not yet"; unrunnable is "not at all", and the settings page
    // already lists every blocker for it in words.
    stubActive("unloaded", {
      readiness: { ...READINESS, runnable: false },
    });
    const { container } = render(<SpeakButton messageId={7} />, { wrapper });
    await waitFor(() => expect(container.firstChild).toBeNull());
  });

  it("no model chosen still renders nothing at all", async () => {
    stubActive("unloaded", { uid: null, readiness: null });
    const { container } = render(<SpeakButton messageId={7} />, { wrapper });
    await waitFor(() => expect(container.firstChild).toBeNull());
  });
});
