/**
 * V9-2 - Speak, pressed while the reply is still arriving.
 *
 * The button's whole job is one request; the audio comes back on the SSE
 * stream the app is already reading. So what is worth testing is when it
 * exists at all, and that a refusal is SAID rather than swallowed - a Speak
 * button that did nothing and reported nothing is the one outcome a person
 * cannot diagnose by looking at it.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

import { SpeakLiveButton } from "@/components/chat/SpeakLiveButton";
import { useUiStore } from "@/lib/store/uiStore";
import { useErrorStore } from "@/lib/errors/errorStore";

const active = vi.hoisted(() => ({ value: undefined as unknown }));
const health = vi.hoisted(() => ({ value: { state: "loaded" } as { state: string } }));
const speakLive = vi.hoisted(() => vi.fn());

vi.mock("@/lib/query/tts", () => ({
  useTtsActive: () => ({ data: active.value }),
  useTtsState: () => ({ data: health.value }),
}));
vi.mock("@/lib/api/tts", () => ({ speakLive }));

describe("SpeakLiveButton", () => {
  beforeEach(() => {
    speakLive.mockReset().mockResolvedValue({ speaking: true });
    useUiStore.setState({ continuousVoice: false });
    useErrorStore.setState({ errors: [] });
    active.value = { uid: "m1", readiness: { runnable: true } };
  });

  it("renders nothing without a usable voice model", () => {
    active.value = undefined;
    const { container } = render(<SpeakLiveButton chatId={1} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when the selected model cannot run", () => {
    active.value = { uid: "m1", readiness: { runnable: false } };
    const { container } = render(<SpeakLiveButton chatId={1} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing while continuous mode is already speaking", () => {
    // Offering to start something that is already running would be a lie
    // about what pressing it does.
    useUiStore.setState({ continuousVoice: true });
    const { container } = render(<SpeakLiveButton chatId={1} />);
    expect(container.firstChild).toBeNull();
  });

  it("asks the server to wake the reply streaming in THIS chat", async () => {
    render(<SpeakLiveButton chatId={42} />);
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(speakLive).toHaveBeenCalledWith(42));
  });

  it("does not fire twice while the first request is in flight", async () => {
    let release: (v: unknown) => void = () => {};
    speakLive.mockReturnValue(new Promise((r) => { release = r; }));
    render(<SpeakLiveButton chatId={1} />);
    const button = screen.getByRole("button");
    fireEvent.click(button);
    // KADEME 19b: the call count alone was satisfied by the JS guard
    // (`if (busy) return`), so the `disabled` attribute could be deleted and
    // this stayed green. A live-looking button that silently swallows presses
    // is a different bug from one that refuses them visibly.
    expect(button, "the button never showed it was busy").toBeDisabled();
    fireEvent.click(button);
    expect(speakLive).toHaveBeenCalledTimes(1);
    release({ speaking: true });
  });

  it("says so when there is nothing streaming any more", async () => {
    speakLive.mockRejectedValue({ status: 404, detail: "tts_nothing_streaming" });
    render(<SpeakLiveButton chatId={1} />);
    fireEvent.click(screen.getByRole("button"));
    // KADEME 19b: "says so" was only `errors.length > 0` - ANY error, from
    // anywhere, satisfied it. The whole point of this path is that the user
    // reads the contract sentence telling them to use the per-message button
    // instead, so the code that carries that sentence is what gets pinned.
    await waitFor(() =>
      expect(useErrorStore.getState().errors.length).toBeGreaterThan(0),
    );
    expect(
      useErrorStore.getState().errors.map((e) => e.code),
      "the refusal arrived without the code that explains it",
    ).toContain("tts_nothing_streaming");
  });

  it("becomes pressable again after a failure", async () => {
    speakLive.mockRejectedValue({ status: 404, detail: "tts_nothing_streaming" });
    render(<SpeakLiveButton chatId={1} />);
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(screen.getByRole("button")).not.toBeDisabled());
  });
});
