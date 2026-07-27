/**
 * voiceErrorReporting.test.ts - the handlers nothing was watching.
 *
 * Audit KÖK 13, verified experimentally: the word `voice_error` appeared
 * NOWHERE under src/test, and commenting out both handlers left all 1095 tests
 * green. They are the only thing standing between a failed utterance and
 * silence with no explanation - the one failure mode voice is not allowed to
 * have - so this is what watches them.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";

import { createStreamVoice } from "@/lib/voice/streamVoice";
import { useErrorStore } from "@/lib/errors/errorStore";
import type { VoiceStreamPlayer } from "@/lib/voice/streamPlayer";

function stubPlayer(): { player: VoiceStreamPlayer; finished: () => number } {
  let finishes = 0;
  const player = {
    push: () => undefined,
    finish: () => {
      finishes += 1;
    },
    stop: () => undefined,
  } as unknown as VoiceStreamPlayer;
  return { player, finished: () => finishes };
}

beforeEach(() => {
  useErrorStore.setState({ errors: [] });
});

function errors() {
  return useErrorStore.getState().errors;
}

describe("a backend voice_error reaches the user", () => {
  it("reports the code the backend sent, not a guess", () => {
    const voice = createStreamVoice({ createPlayer: () => stubPlayer().player });
    voice.handle({ type: "voice_error", code: "tts_out_of_memory" });

    expect(errors()).toHaveLength(1);
    expect(errors()[0].code).toBe("tts_out_of_memory");
    // The shared map, so the user reads the contract sentence rather than a
    // string invented at the call site.
    expect(errors()[0].message).toMatch(/GPU memory/i);
  });

  it("ends the utterance rather than leaving it hanging", () => {
    const stub = stubPlayer();
    const voice = createStreamVoice({ createPlayer: () => stub.player });
    voice.handle({ type: "voice_chunk", audio_id: "a", index: 0 });
    voice.handle({ type: "voice_error", code: "tts_synthesis_failed" });

    expect(stub.finished()).toBe(1);
  });

  it("says it once, even when the fetch fails behind it", () => {
    // One silence, one explanation. The backend emits an error event and the
    // chunk fetch that was already in flight fails too; two toasts would read
    // as two separate faults.
    let onError: ((err: unknown) => void) | undefined;
    const voice = createStreamVoice({
      createPlayer: (handler) => {
        onError = handler;
        return stubPlayer().player;
      },
    });
    voice.handle({ type: "voice_chunk", audio_id: "a", index: 0 });
    voice.handle({ type: "voice_error", code: "tts_synthesis_failed" });
    onError?.(new Error("and the fetch died as well"));

    expect(errors()).toHaveLength(1);
  });

  it("an unknown code still produces a sentence", () => {
    const voice = createStreamVoice({ createPlayer: () => stubPlayer().player });
    voice.handle({ type: "voice_error", code: "tts_from_the_future" });
    expect(errors()[0].message.length).toBeGreaterThan(0);
  });
});

describe("the warnings that are not errors", () => {
  it("a truncated reply is a warning, because the audio still played", () => {
    const voice = createStreamVoice({ createPlayer: () => stubPlayer().player });
    voice.handle({ type: "voice_done", truncated: true });

    expect(errors()[0].severity).toBe("warning");
    expect(errors()[0].message).toMatch(/too long/i);
  });

  it("a dropped line names how many", () => {
    const voice = createStreamVoice({ createPlayer: () => stubPlayer().player });
    voice.handle({ type: "voice_done", dropped: 3 });

    expect(errors()[0].severity).toBe("warning");
    expect(errors()[0].message).toMatch(/3 lines/);
  });

  it("one dropped line is not called '1 lines'", () => {
    const voice = createStreamVoice({ createPlayer: () => stubPlayer().player });
    voice.handle({ type: "voice_done", dropped: 1 });
    expect(errors()[0].message).toMatch(/One line/);
  });

  it("a worker notice is a warning too - the speech is fine, just slower", () => {
    const voice = createStreamVoice({ createPlayer: () => stubPlayer().player });
    voice.handle({
      type: "voice_notice",
      note: "compiled with eager decoding; speech will be slower",
    });

    expect(errors()[0].severity).toBe("warning");
    expect(errors()[0].message).toMatch(/eager decoding/);
  });

  it("a clean reply says nothing at all", () => {
    const voice = createStreamVoice({ createPlayer: () => stubPlayer().player });
    voice.handle({ type: "voice_done", count: 4 });
    expect(errors()).toHaveLength(0);
  });
});
