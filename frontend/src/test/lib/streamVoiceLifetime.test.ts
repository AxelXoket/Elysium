/**
 * Audit regressions (2026-07-25): audio outliving the reply it belongs to.
 *
 * Both bugs had the same root - the only handle on a playing reply was a local
 * const inside one of three SSE handlers, so nothing outside that closure could
 * silence it. Locking the vault left the conversation being read aloud over the
 * lock screen, and pressing stop during the post-`done` voice-drain window
 * could not reach it either.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

import { createStreamVoice, stopAllStreamVoices } from "@/lib/voice/streamVoice";

function fakePlayer() {
  return {
    push: vi.fn(),
    finish: vi.fn(),
    stop: vi.fn(),
    active: true,
  };
}

describe("live stream voices are reachable from outside their stream", () => {
  beforeEach(() => stopAllStreamVoices());

  it("a voice that has started playing can be silenced globally", () => {
    const player = fakePlayer();
    const voice = createStreamVoice({ createPlayer: () => player as never });
    voice.handle({ type: "voice_chunk", audio_id: "a1", index: 0 } as never);
    expect(voice.active).toBe(true);

    stopAllStreamVoices();

    expect(player.stop).toHaveBeenCalledTimes(1);
    expect(voice.active).toBe(false);
  });

  it("silences every reply currently speaking, not just the newest", () => {
    const a = fakePlayer();
    const b = fakePlayer();
    createStreamVoice({ createPlayer: () => a as never })
      .handle({ type: "voice_chunk", audio_id: "a", index: 0 } as never);
    createStreamVoice({ createPlayer: () => b as never })
      .handle({ type: "voice_chunk", audio_id: "b", index: 0 } as never);

    stopAllStreamVoices();

    expect(a.stop).toHaveBeenCalled();
    expect(b.stop).toHaveBeenCalled();
  });

  it("a voice that stopped itself is not stopped twice", () => {
    const player = fakePlayer();
    const voice = createStreamVoice({ createPlayer: () => player as never });
    voice.handle({ type: "voice_chunk", audio_id: "a1", index: 0 } as never);
    voice.stop();
    stopAllStreamVoices();
    expect(player.stop).toHaveBeenCalledTimes(1);
  });

  it("a voice that never played is harmless to silence", () => {
    createStreamVoice({ createPlayer: () => fakePlayer() as never });
    expect(() => stopAllStreamVoices()).not.toThrow();
  });
});
