/**
 * voiceCrossSource.test.ts - the per-message player and the live reply stream
 * are two different audio devices; only one of them may speak.
 *
 * Audit HIGH: `speak()` stopped the previous HTMLAudioElement and nothing else,
 * and a streamed reply's ChunkScheduler connected straight to the shared
 * AudioContext destination. Pressing Speak while a reply was being read aloud
 * produced two simultaneous voices of the same character. These tests drive the
 * REAL stores against each other, not the arbiter in isolation.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";

import { stubAudioContext } from "../helpers/fakeAudioContext";

vi.mock("@/lib/api/tts", () => ({
  // The Speak button streams now: `/tts/speak` could not make a sound until
  // the LAST sentence was finished, so a long reply was silence first.
  streamMessageSpeech: vi.fn(
    async (
      _body: unknown,
      { onEvent }: { onEvent: (e: never) => void },
    ) => {
      // One chunk and NO voice_done: this store is "still speaking" for the
      // whole of these tests. Ending the utterance would flip it to idle and
      // there would be nothing left for a rival source to interrupt.
      onEvent({ type: "voice_chunk", audio_id: "aud-1", index: 0 } as never);
    },
  ),
  ttsAudioUrl: (id: string) => `/audio/${id}`,
}));

import { useVoicePlayer, stopVoicePlayback } from "@/lib/voice/playerStore";
import { createStreamVoice, stopAllStreamVoices } from "@/lib/voice/streamVoice";
import { clearStage } from "@/lib/voice/stage";

function fakePlayer() {
  return { push: vi.fn(), finish: vi.fn(), stop: vi.fn(), active: true };
}

function chunk(id: string) {
  return { type: "voice_chunk", audio_id: id, index: 0 } as never;
}

describe("only one voice source speaks at a time", () => {
  beforeEach(() => {
    stubAudioContext();
    stopAllStreamVoices();
    clearStage();
    useVoicePlayer.setState({ messageId: null, phase: "idle", requestSeq: 0 });
  });

  it("pressing Speak stops a reply that is being read aloud", async () => {
    const player = fakePlayer();
    const reply = createStreamVoice({ createPlayer: () => player as never });
    reply.handle(chunk("live-1"));
    expect(reply.active).toBe(true);

    await useVoicePlayer.getState().speak(42);

    expect(player.stop).toHaveBeenCalledTimes(1);
    expect(reply.active).toBe(false);
    expect(useVoicePlayer.getState().phase).toBe("playing");
  });

  it("a reply starting to speak stops the per-message player", async () => {
    await useVoicePlayer.getState().speak(7);
    expect(useVoicePlayer.getState().phase).toBe("playing");

    const player = fakePlayer();
    createStreamVoice({ createPlayer: () => player as never }).handle(chunk("x"));

    expect(useVoicePlayer.getState().phase).toBe("idle");
    expect(useVoicePlayer.getState().messageId).toBeNull();
  });

  it("a second reply supersedes the first instead of mixing with it", () => {
    const first = fakePlayer();
    const second = fakePlayer();
    createStreamVoice({ createPlayer: () => first as never }).handle(chunk("a"));
    createStreamVoice({ createPlayer: () => second as never }).handle(chunk("b"));

    expect(first.stop).toHaveBeenCalledTimes(1);
    expect(second.stop).not.toHaveBeenCalled();
  });

  it("locking the vault silences whichever source is speaking", () => {
    const player = fakePlayer();
    const reply = createStreamVoice({ createPlayer: () => player as never });
    reply.handle(chunk("live-2"));

    stopVoicePlayback();

    expect(player.stop).toHaveBeenCalledTimes(1);
    expect(reply.active).toBe(false);
  });

  it("a superseded speak() response does not resurrect as ghost audio", async () => {
    const player = fakePlayer();
    const pending = useVoicePlayer.getState().speak(9);
    // The reply takes the stage while the synthesis request is still flying.
    createStreamVoice({ createPlayer: () => player as never }).handle(chunk("c"));
    await pending;

    // The abandoned response must not have left anything sounding.
    expect(useVoicePlayer.getState().phase).toBe("idle");
    expect(useVoicePlayer.getState().messageId).toBeNull();
  });
});
