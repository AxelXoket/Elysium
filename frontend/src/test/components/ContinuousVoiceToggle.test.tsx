/**
 * V9-1 - the "speak every reply" switch.
 *
 * Two rules are worth a test each. It must not exist when there is no voice
 * model - a control that can only produce an error toast is a broken promise,
 * and the readiness data is there so it can know better. And it must default
 * OFF, because voice costs GPU time and makes noise: it is the one setting
 * that may never surprise anyone by being on.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { ContinuousVoiceToggle } from "@/components/chat/ContinuousVoiceToggle";
import { useUiStore } from "@/lib/store/uiStore";

const active = vi.hoisted(() => ({ value: undefined as unknown }));
const health = vi.hoisted(() => ({ value: { state: "loaded" } as { state: string } }));

vi.mock("@/lib/query/tts", () => ({
  useTtsActive: () => ({ data: active.value }),
  // The readiness hook mounts the /tts/state heartbeat now (KOK 15): it is
  // the only poller of the endpoint that lets the backend notice a dead
  // worker. Healthy here; the crash case has its own test.
  useTtsState: () => ({ data: health.value }),
}));

describe("ContinuousVoiceToggle", () => {
  beforeEach(() => {
    useUiStore.setState({ continuousVoice: false });
    active.value = { uid: "m1", readiness: { runnable: true } };
  });

  it("renders nothing when no voice model is selected", () => {
    active.value = undefined;
    const { container } = render(<ContinuousVoiceToggle />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when the selected model cannot actually run", () => {
    active.value = { uid: "m1", readiness: { runnable: false } };
    const { container } = render(<ContinuousVoiceToggle />);
    expect(container.firstChild).toBeNull();
  });

  it("starts off", () => {
    render(<ContinuousVoiceToggle />);
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
  });

  it("flips the shared store field, so Settings and the composer agree", () => {
    render(<ContinuousVoiceToggle />);
    fireEvent.click(screen.getByRole("switch"));
    expect(useUiStore.getState().continuousVoice).toBe(true);
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true");
  });

  it("says what turning it on will actually do", () => {
    // "starting from your next message" is the toggle's real contract; a label
    // that just said "speak replies" would promise the reply already on screen.
    render(<ContinuousVoiceToggle />);
    expect(screen.getByRole("switch").getAttribute("title")).toContain(
      "next message",
    );
  });
});
