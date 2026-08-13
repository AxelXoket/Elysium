/**
 * V9-4 - the delivery dials.
 *
 * The dials are worth testing at this level for one reason: they are the
 * answer to "make the voice deeper/closer" WITHOUT a new reference clip, so a
 * silently-not-saving control here would send somebody back to hunting for
 * recordings. Saving, and saving only what changed, is the contract.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";

import { DeliverySection } from "@/components/settings/DeliverySection";
import { clearStage, stageOccupied } from "@/lib/voice/stage";

const getTagPrefs = vi.hoisted(() => vi.fn());
const saveTagPrefs = vi.hoisted(() => vi.fn());
const speakText = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api/tts", () => ({
  getTagPrefs,
  saveTagPrefs,
  speakText,
  ttsAudioUrl: (id: string) => `/audio/${id}`,
}));

const PREFS = {
  density: 8, tone: "", min: 0, max: 16, tone_max_chars: 60,
  speed: 1.0, speed_min: 0.8, speed_max: 1.25,
  gap: 0, gap_min: 0, gap_max: 1.5,
};

/**
 * The section now tells the query cache when a dial changed, because the
 * STREAM reads these values (useTagPrefs) - not this panel. Rendering it
 * without a provider tests a configuration the app never has.
 */

describe("DeliverySection", () => {
  beforeEach(() => {
    getTagPrefs.mockReset().mockResolvedValue(PREFS);
    saveTagPrefs.mockReset().mockImplementation((patch) =>
      Promise.resolve({ ...PREFS, ...patch }),
    );
    speakText.mockReset().mockResolvedValue({ audio_id: "a1" });
  });

  it("renders nothing until the current values are known", () => {
    getTagPrefs.mockReturnValue(new Promise(() => {}));
    const { container } = renderWithQueryClient(<DeliverySection />);
    expect(container.firstChild).toBeNull();
  });

  it("stays out of the way when voice is not set up", async () => {
    // The rest of Settings must keep working; a failed fetch here is not an
    // error worth a toast.
    getTagPrefs.mockRejectedValue(new Error("not configured"));
    const { container } = renderWithQueryClient(<DeliverySection />);
    await waitFor(() => expect(container.firstChild).toBeNull());
  });

  it("saves a tone picked from the palette immediately", async () => {
    renderWithQueryClient(<DeliverySection />);
    await screen.findByText("Delivery");
    fireEvent.click(screen.getByText("low voice, slow"));
    await waitFor(() =>
      expect(saveTagPrefs).toHaveBeenCalledWith({ tone: "low voice, slow" }),
    );
  });

  it("saves a typed tone on blur, not on every keystroke", async () => {
    renderWithQueryClient(<DeliverySection />);
    const input = await screen.findByPlaceholderText("e.g. low voice, slow");
    fireEvent.change(input, { target: { value: "hushed" } });
    expect(saveTagPrefs).not.toHaveBeenCalled();
    fireEvent.blur(input);
    await waitFor(() =>
      expect(saveTagPrefs).toHaveBeenCalledWith({ tone: "hushed" }),
    );
  });

  it("does not write when the tone was not actually changed", async () => {
    renderWithQueryClient(<DeliverySection />);
    const input = await screen.findByPlaceholderText("e.g. low voice, slow");
    fireEvent.blur(input);
    expect(saveTagPrefs).not.toHaveBeenCalled();
  });

  it("saves density when the slider is released, not while dragging", async () => {
    renderWithQueryClient(<DeliverySection />);
    const slider = await screen.findByLabelText("Direction density");
    fireEvent.change(slider, { target: { value: "3" } });
    expect(saveTagPrefs).not.toHaveBeenCalled();
    fireEvent.pointerUp(slider);
    await waitFor(() => expect(saveTagPrefs).toHaveBeenCalledWith({ density: 3 }));
  });

  it("says so when a dial refuses to save, instead of looking saved", async () => {
    // The one failure this section cannot afford. Every control here writes
    // on release and then leaves the new position on screen; if the write
    // loses and nobody says anything, the dial reads as the setting while
    // the vault still holds the old one, and the next reply comes back
    // unchanged for no visible reason. Until KADEME 18b the `.catch` that
    // reports it had no test at all - deleting the whole handler left the
    // entire suite green.
    const { useErrorStore } = await import("@/lib/errors");
    useErrorStore.getState().clearAll();
    saveTagPrefs.mockRejectedValue(new Error("vault_locked"));

    renderWithQueryClient(<DeliverySection />);
    fireEvent.click(await screen.findByText("low voice, slow"));

    await waitFor(() =>
      expect(
        useErrorStore.getState().errors,
        "the dial failed to save and nothing told the reader",
      ).toHaveLength(1),
    );
    useErrorStore.getState().clearAll();
  });

  it("previews through the real speak path", async () => {
    // A preview rendered by some other route would be a different promise
    // from the one being tuned.
    window.HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined);
    renderWithQueryClient(<DeliverySection />);
    fireEvent.click(await screen.findByText("Hear it"));
    await waitFor(() => expect(speakText).toHaveBeenCalledTimes(1));
  });

  // ── Audit: the reading-speed dial had no UI anywhere ───────────────────
  //
  // matrix.APP_LEVEL greys the engine's own rate knob and tells the user the
  // dial is "set under Delivery" - but Delivery had only tone and density, and
  // no frontend code ever sent a rate. The whole feature (tts/speed.py, the
  // worker WSOLA path) was unreachable, and the panel pointed at a control
  // that was never built.

  it("offers the reading-speed dial the settings panel points at", async () => {
    renderWithQueryClient(<DeliverySection />);
    const slider = await screen.findByLabelText("Reading speed");
    expect(slider).toHaveValue("1");
    expect(slider).toHaveAttribute("min", "0.8");
    expect(slider).toHaveAttribute("max", "1.25");
  });

  it("saves the speed when the slider is released, not while dragging", async () => {
    renderWithQueryClient(<DeliverySection />);
    const slider = await screen.findByLabelText("Reading speed");
    fireEvent.change(slider, { target: { value: "1.25" } });
    expect(saveTagPrefs).not.toHaveBeenCalled();
    fireEvent.pointerUp(slider);
    await waitFor(() =>
      expect(saveTagPrefs).toHaveBeenCalledWith({ speed: 1.25 }),
    );
  });

  // ── Audit: the preview was a THIRD, unreachable audio source ────────────

  it("a preview abandoned by unmount never starts playing", async () => {
    // Synthesis takes seconds. Closing the Settings dialog meanwhile used to
    // leave the awaited response to create and play a fresh Audio element that
    // nothing held a handle to - a voice from nowhere with no stop control.
    const play = vi.fn().mockResolvedValue(undefined);
    window.HTMLMediaElement.prototype.play = play;
    let release!: (v: { audio_id: string }) => void;
    speakText.mockReturnValue(
      new Promise<{ audio_id: string }>((resolve) => {
        release = resolve;
      }),
    );

    const view = renderWithQueryClient(<DeliverySection />);
    fireEvent.click(await screen.findByText("Hear it"));
    await waitFor(() => expect(speakText).toHaveBeenCalledTimes(1));

    view.unmount();
    release({ audio_id: "late" });
    await Promise.resolve();
    await Promise.resolve();

    expect(play).not.toHaveBeenCalled();
  });

  it("a preview takes the shared stage, so a vault lock silences it", async () => {
    window.HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined);
    const pause = vi.fn();
    window.HTMLMediaElement.prototype.pause = pause;

    renderWithQueryClient(<DeliverySection />);
    fireEvent.click(await screen.findByText("Hear it"));
    await waitFor(() => expect(speakText).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(stageOccupied()).toBe(true));

    // What VaultGate calls on lock.
    clearStage();
    expect(pause).toHaveBeenCalled();
    expect(stageOccupied()).toBe(false);
  });
});
