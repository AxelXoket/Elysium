/**
 * voiceErrorReporting.test.ts - the handlers nothing was watching.
 *
 * Audit KÖK 13, verified experimentally: the word `voice_error` appeared
 * NOWHERE under src/test, and commenting out both handlers left all 1095 tests
 * green. They are the only thing standing between a failed utterance and
 * silence with no explanation - the one failure mode voice is not allowed to
 * have - so this is what watches them.
 */
import { describe, it, expect, beforeEach } from "vitest";

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
  // Both lists. The queue takes part in the dedupe, so a leftover queued event
  // would let one test silence the next one's toast.
  useErrorStore.setState({ errors: [], queuedErrors: [] });
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
      note: "falling back to eager decoding (triton-windows + MSVC?)",
    });

    expect(errors()[0].severity).toBe("warning");
    expect(errors()[0].message).toMatch(/slower/i);
  });

  it("a clean reply says nothing at all", () => {
    const voice = createStreamVoice({ createPlayer: () => stubPlayer().player });
    voice.handle({ type: "voice_done", count: 4 });
    expect(errors()).toHaveLength(0);
  });
});

/**
 * The engine's diagnostics, as read by somebody who is not debugging it.
 *
 * Every string below was copied from the worker, not imagined: the fixed ones
 * are in backend/tts/worker/fish_s2.py and the last is the interpolated
 * exception at backend/tts/worker/chatterbox.py:533. backend/tts/host.py
 * forwards all of them verbatim and drops the stage, so free text is the whole
 * of what the frontend receives. These used to be the toast, word for word,
 * over a private conversation.
 */
const WORKER_NOTES = [
  "staying bf16; generation will be slower",
  "first compile is slow; a warm TORCHINDUCTOR_CACHE_DIR makes it ~59s",
  "compiling the model for this GPU",
  "falling back to eager decoding (triton-windows + MSVC?)",
  "compiling failed; retrying without it",
  "compiling into a temporary cache; every load will be slow",
  "the model will be rebuilt from disk instead",
  "restoring text2semantic from system memory",
  "rebuilding the model from disk instead",
  "the model was freed to let the last decode finish",
  "the first spoken sentence will load it instead",
  "freeing text2semantic so the codec fits",
  "this request needs a longer context; recompiling once",
  "the request does not fit the chosen context window",
  "the text and reference leave less context than the length limit asks for",
  "this text hit the length limit and was cut short - raise Max length, or say it in smaller pieces",
];

/** Words that belong in a log and nowhere near a person mid scene. */
const NEVER_SHOWN = [
  "bf16",
  "triton",
  "MSVC",
  "TORCHINDUCTOR",
  "text2semantic",
  "eager",
  "codec",
  "GPU",
];

describe("a worker diagnostic is translated, not forwarded", () => {
  function say(note: string) {
    const voice = createStreamVoice({ createPlayer: () => stubPlayer().player });
    voice.handle({ type: "voice_notice", note });
    return errors();
  }

  it("says something of ours for every diagnostic the worker can send", () => {
    for (const note of WORKER_NOTES) {
      useErrorStore.setState({ errors: [], queuedErrors: [] });
      const shown = say(note);

      expect(shown, `nothing was said for: ${note}`).toHaveLength(1);
      expect(shown[0].message, `forwarded verbatim: ${note}`).not.toBe(note);
      for (const word of NEVER_SHOWN) {
        expect(
          shown[0].message.toLowerCase(),
          `"${word}" reached the toast via: ${note}`,
        ).not.toContain(word.toLowerCase());
      }
    }
  });

  it("keeps the reason the carrier exists: a slow machine is still told", () => {
    // KÖK 1. On a box with no MSVC the engine falls back on every load and
    // speech runs two to three times slower forever. Translating the wording
    // is the fix; going quiet about it would be a different bug.
    expect(say("compiling into a temporary cache; every load will be slow")[0].message)
      .toMatch(/slower/i);
  });

  it("GROUND: a diagnostic we do not recognise is not shown at all", () => {
    // The retimer's note is `f"{type(exc).__name__}: {exc}"` with no stage
    // attached, so nothing here can tell what it means - and a sentence we
    // cannot vouch for is worse than the silence. The backend logs it with
    // the stage and the exception, which is more than a toast could hold.
    expect(say("TypeError: unsupported operand type(s) for *: 'NoneType'")).toHaveLength(0);

    useErrorStore.setState({ errors: [], queuedErrors: [] });
    expect(say("some future note nobody has written yet")).toHaveLength(0);

    // POSITIVE CONTROL, same helper: silence here has to be a decision, not a
    // handler that stopped pushing anything at all.
    useErrorStore.setState({ errors: [], queuedErrors: [] });
    expect(say("staying bf16; generation will be slower")).toHaveLength(1);
  });

  it("two different diagnostics in one reply are both said", () => {
    // The pair the host really does drain together: setup work and a reply
    // that did not fit are two different things to know, and the second used
    // to vanish into the first's toast.
    const voice = createStreamVoice({ createPlayer: () => stubPlayer().player });
    voice.handle({ type: "voice_notice", note: "compiling the model for this GPU" });
    voice.handle({
      type: "voice_notice",
      note: "this text hit the length limit and was cut short - raise Max length, or say it in smaller pieces",
    });

    const shown = errors();
    expect(shown).toHaveLength(2);
    expect(new Set(shown.map((e) => e.message)).size).toBe(2);
  });

  it("GROUND: two diagnostics that mean the same thing are said once", () => {
    // Both of these mean "slow forever" to a reader, so they share a sentence
    // and the store collapses them. Without this the fix above would just be
    // a dedupe that never fires.
    const voice = createStreamVoice({ createPlayer: () => stubPlayer().player });
    voice.handle({ type: "voice_notice", note: "staying bf16; generation will be slower" });
    voice.handle({
      type: "voice_notice",
      note: "falling back to eager decoding (triton-windows + MSVC?)",
    });

    expect(errors()).toHaveLength(1);
  });
});
