/**
 * VoiceStage.test.ts - two voices must never talk over each other.
 *
 * Audit HIGH: playerStore's speak() stopped only its own HTMLAudioElement and
 * streamVoice's scheduler knew nothing about it, so pressing Speak on a message
 * while a reply was being read aloud played BOTH - the same character speaking
 * two messages at once, with a Stop face on only one of them. The Delivery
 * preview was a third such source, and it kept talking over the vault lock
 * screen because stopVoicePlayback() could not reach it either.
 *
 * These pin the arbiter (lib/voice/stage.ts): taking the stage silences
 * whoever holds it, and leaving is identity-guarded so a superseded source
 * cannot evict its own replacement.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  takeStage,
  leaveStage,
  clearStage,
  stageOccupied,
  type VoiceSource,
} from "@/lib/voice/stage";

function source(): VoiceSource & { silence: ReturnType<typeof vi.fn> } {
  const s = { silence: vi.fn() };
  return s;
}

describe("voice stage", () => {
  beforeEach(() => {
    // Leave no occupant behind between cases (clearStage silences it).
    clearStage();
  });

  it("taking the stage silences the previous occupant", () => {
    const a = source();
    const b = source();

    takeStage(a);
    expect(a.silence).not.toHaveBeenCalled();

    takeStage(b);
    expect(a.silence).toHaveBeenCalledTimes(1);
    expect(b.silence).not.toHaveBeenCalled();
  });

  it("re-taking the stage does not silence yourself", () => {
    const a = source();
    takeStage(a);
    takeStage(a);
    expect(a.silence).not.toHaveBeenCalled();
  });

  it("a displaced source's own teardown cannot evict its replacement", () => {
    // This is the real shape: silence() runs the old source's stop(), which
    // calls leaveStage. Unguarded, that would clear the newcomer.
    const b = source();
    const a: VoiceSource = { silence: vi.fn(() => leaveStage(a)) };

    takeStage(a);
    takeStage(b);

    expect(stageOccupied()).toBe(true);
    clearStage();
    expect(b.silence).toHaveBeenCalledTimes(1);
  });

  it("clearStage silences the occupant and empties the stage", () => {
    const a = source();
    takeStage(a);
    clearStage();
    expect(a.silence).toHaveBeenCalledTimes(1);
    expect(stageOccupied()).toBe(false);
    // Idempotent: a second lock must not re-silence a gone source.
    clearStage();
    expect(a.silence).toHaveBeenCalledTimes(1);
  });

  it("leaveStage by a non-occupant is a no-op", () => {
    const a = source();
    const b = source();
    takeStage(a);
    leaveStage(b);
    expect(stageOccupied()).toBe(true);
    clearStage();
    expect(a.silence).toHaveBeenCalledTimes(1);
  });
});
