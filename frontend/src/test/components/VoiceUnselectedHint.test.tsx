/**
 * VoiceUnselectedHint.test.tsx - the one line that breaks the silence.
 *
 * Every in-chat voice control (SpeakButton, SpeakLiveButton,
 * ContinuousVoiceToggle) returns null until a model is SELECTED. That rule is
 * right - an affordance that can only produce an error toast is a broken
 * promise - but it collapsed two very different states into one. Somebody who
 * installed an engine, downloaded a model and recorded a reference voice, and
 * simply never found the (previously unlabelled) select control, saw a chat
 * identical to a fresh install with nothing anywhere to explain why.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { VoiceUnselectedHint } from "@/components/chat/VoiceUnselectedHint";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "../mocks/api";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

const ACTIVE_BASE = {
  uid: null,
  state: "unloaded",
  engine_id: null,
  vram_mb: null,
  error_code: null,
  readiness: null,
};

function stubActive(body: Record<string, unknown>) {
  mockFetch({ "/tts/active": { body: { ...ACTIVE_BASE, ...body } } });
}

describe("VoiceUnselectedHint", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      voiceHintDismissed: false,
      settingsOpen: false,
      settingsInitialPage: null,
    });
  });

  it("says so when voice is installed but nothing is chosen", async () => {
    stubActive({ voice_installed: true, uid: null });
    render(<VoiceUnselectedHint />, { wrapper });

    const hint = await screen.findByRole("status");
    expect(hint).toHaveTextContent(/no voice is chosen/i);
  });

  it("stays silent when no engine is installed", async () => {
    // Nothing to offer - a nag about a feature that does not exist here.
    stubActive({ voice_installed: false, uid: null });
    const { container } = render(<VoiceUnselectedHint />, { wrapper });
    await waitFor(() => expect(container.firstChild).toBeNull());
  });

  it("stays silent once a model IS chosen", async () => {
    stubActive({
      voice_installed: true,
      uid: "u1",
      readiness: null,
    });
    const { container } = render(<VoiceUnselectedHint />, { wrapper });
    await waitFor(() => expect(container.firstChild).toBeNull());
  });

  it("opens Settings on the Voice page", async () => {
    stubActive({ voice_installed: true, uid: null });
    render(<VoiceUnselectedHint />, { wrapper });

    await userEvent.click(await screen.findByRole("button", { name: "Choose one" }));

    expect(useUiStore.getState().settingsOpen).toBe(true);
    expect(useUiStore.getState().settingsInitialPage).toBe("voice");
  });

  it("can be dismissed, and stays dismissed", async () => {
    // A hint that cannot be silenced is a nag; somebody who deliberately keeps
    // voice off should be able to say so once.
    stubActive({ voice_installed: true, uid: null });
    const view = render(<VoiceUnselectedHint />, { wrapper });

    await userEvent.click(
      await screen.findByRole("button", { name: "Dismiss voice hint" }),
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(useUiStore.getState().voiceHintDismissed).toBe(true);

    view.unmount();
    const again = render(<VoiceUnselectedHint />, { wrapper });
    await waitFor(() => expect(again.container.firstChild).toBeNull());
  });
});
