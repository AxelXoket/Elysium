/**
 * streamVoice.ts - the four lines each streaming handler is allowed to grow by.
 *
 * `useStreamingCompletion` already carries three near-identical handlers (send,
 * regenerate, edit) and each is dense with cache surgery. Teaching all three
 * about audio contexts, fetch ordering and error mapping would triple the
 * surface where a cleanup path can be forgotten. They get `handle(event)` and
 * `stop()` instead.
 *
 * Nothing is created until a `voice_chunk` actually arrives. Voice is off for
 * most replies, and an AudioContext built for a stream that never speaks is a
 * real cost - browsers cap how many a page may hold.
 */

import type { StreamEvent } from "../api/stream";
import { getCountMessage, getErrorMessage } from "../errors/errorMessages";
import { useErrorStore } from "../errors/errorStore";
import { VoiceStreamPlayer, voiceErrorCode } from "./streamPlayer";
import { leaveStage, takeStage, type VoiceSource } from "./stage";

export interface StreamVoice {
  /** Feed every stream event; non-voice ones are ignored. */
  handle: (event: StreamEvent) => void;
  /** Abort playback - user stopped, switched chat, or the stream died. */
  stop: () => void;
  /** True once audio has actually started arriving. */
  readonly active: boolean;
}

export interface StreamVoiceOptions {
  gapSeconds?: number;
  /**
   * The last chunk has finished PLAYING.
   *
   * Not the same moment as the stream ending, and the difference is the whole
   * reason this exists: audio is the one part of a reply that keeps going
   * after the request is over, so a caller that flips its button back to
   * "speak" when the fetch resolves does it while the voice is still talking.
   */
  onEnded?: () => void;
  /** Injected by tests; production uses the real player. */
  createPlayer?: (onError: (err: unknown) => void) => VoiceStreamPlayer;
}

/**
 * What the reader is told when the engine files a diagnostic, and why it is
 * never the diagnostic itself.
 *
 * `voice_notice.note` is free text written for whoever is debugging the
 * worker. Verified at the source: every note is a fixed string in
 * backend/tts/worker/fish_s2.py except one, which is
 * `f"{type(exc).__name__}: {exc}"` at backend/tts/worker/chatterbox.py:533,
 * and backend/tts/host.py relays all of them verbatim without the stage. So
 * the sentences that used to land on the reader were things like "falling
 * back to eager decoding (triton-windows + MSVC?)", "staying bf16", the name
 * of a cache environment variable, and a raw Python exception. Mid scene, in
 * red, over a private conversation. The catalogue record for `tts_notice`
 * already names this as a defect of its own.
 *
 * Of the three ways out, this is the mapped one: the known diagnostics become
 * sentences of ours, and anything unrecognised is not shown at all. The other
 * two were rejected for reasons worth keeping.
 *
 *  - Showing one calm line with the detail on request keeps the engine's text
 *    in the store and one render away from the screen, and the detail is not
 *    ours to hand over anyway.
 *  - Showing nothing at all would undo the reason this carrier exists (KÖK 1):
 *    a machine without MSVC spoke two to three times slower on EVERY load and
 *    nothing anywhere said so. That is worth a sentence. It is the wording
 *    that was never worth shipping.
 *
 * Dropping the unrecognised ones loses nothing that is not already written
 * down: backend/tts/worker_client.py logs every note at warning level WITH its
 * stage and the exception that caused it, which is strictly more than the
 * toast could ever carry. And a diagnostic the frontend does not recognise is
 * one it cannot vouch for, so the default has to be silence.
 *
 * The cost is honest: the retimer's exception carries a real signal (the
 * speaking-speed dial silently did nothing) and it arrives shaped as an
 * exception with no stage, so it is dropped here. Guessing its meaning from
 * the shape of the string would put the wrong sentence on every future
 * exception. That one needs a code from the backend, not a rescue here.
 */
const SLOWER_ALWAYS =
  "The voice engine could not finish setting itself up on this computer, so speech will be slower than it should be.";
const WARMING_UP =
  "The voice engine is preparing itself for this computer, so this reply will be slow to start speaking.";
const RELOADING =
  "The voice engine had to reload itself to fit in memory, so this reply will be slow to start speaking.";
const LENGTH_CAPPED =
  "The voice engine reached its own length limit for this reply, so the last part of it was not spoken.";
const LONG_FOR_SETTINGS =
  "This reply is long for the voice engine's current settings, so part of it may not be spoken.";

/**
 * One line per diagnostic the worker can send today, matched on the part of
 * the wording least likely to be reworded.
 *
 * Grouped by what is actually different FOR THE READER, which is the only
 * distinction a toast can carry: it will be slow forever, it is slow this
 * once, or their reply did not fit. Two notes that mean the same thing to a
 * person share a sentence on purpose, and the store then collapses them.
 */
const NOTICE_SENTENCES: ReadonlyArray<readonly [RegExp, string]> = [
  // Setup that failed and will keep failing. The engine runs, slowly, forever.
  [/bf16/i, SLOWER_ALWAYS],
  [/eager decoding/i, SLOWER_ALWAYS],
  [/compiling failed/i, SLOWER_ALWAYS],
  [/every load will be slow/i, SLOWER_ALWAYS],
  // Setup that is working, and costs this reply its head start.
  [/first compile is slow/i, WARMING_UP],
  [/compiling the model/i, WARMING_UP],
  [/recompiling once/i, WARMING_UP],
  // The model being moved in and out of memory around this reply.
  [/rebuilt from disk/i, RELOADING],
  [/rebuilding the model from disk/i, RELOADING],
  [/from system memory/i, RELOADING],
  [/was freed to let/i, RELOADING],
  [/^freeing /i, RELOADING],
  [/first spoken sentence will load/i, RELOADING],
  // The reply itself did not fit.
  [/hit the length limit/i, LENGTH_CAPPED],
  [/does not fit the chosen context/i, LONG_FOR_SETTINGS],
  [/less context than the length limit/i, LONG_FOR_SETTINGS],
];

/** Our sentence for a worker diagnostic, or null if we do not recognise it. */
function noticeSentence(note: string): string | null {
  for (const [pattern, sentence] of NOTICE_SENTENCES) {
    if (pattern.test(note)) return sentence;
  }
  return null;
}

/**
 * Every live stream voice, so something outside its closure can silence it.
 *
 * Without this the only handle on playing audio was a local const inside one
 * of three SSE handlers - so locking the vault left the conversation being
 * read aloud over the lock screen, and pressing stop after `done` (the entire
 * voice-drain window) could not reach it either. Audio is the one part of a
 * reply that keeps going after the request is over; it needs a way to be
 * reached after the request is over too.
 */
const LIVE = new Set<StreamVoice>();

/** Silence every reply currently being spoken. Vault lock, chat deletion,
 *  window teardown - anything that means "this conversation is over now". */
export function stopAllStreamVoices(): void {
  for (const voice of [...LIVE]) voice.stop();
  LIVE.clear();
}

export function createStreamVoice(options: StreamVoiceOptions = {}): StreamVoice {
  let player: VoiceStreamPlayer | null = null;
  let reported = false;
  // This reply's seat on the shared stage (see voice/stage.ts).
  const stageSource: VoiceSource = { silence: () => self.stop() };

  const report = (code: string) => {
    // Once per reply. A failed utterance produces one error event from the
    // backend and possibly a failed fetch here; two toasts for one silence
    // would read as two separate faults.
    if (reported) return;
    reported = true;
    useErrorStore.getState().pushErrorDirect(code, getErrorMessage(code));
  };

  const ensure = () => {
    if (!player) {
      // First chunk: this reply takes the stage. Whatever else is speaking -
      // the per-message Speak button, a previous reply still draining into the
      // SAME shared AudioContext, the Delivery preview - stops here rather
      // than mixing into the same destination.
      takeStage(stageSource);
      // Registered HERE, not at construction (KÖK 10). Every reply built
      // one of these - voice off included - and the only removal was in
      // stop(), which useStreamingCompletion calls only on abort. So an
      // ordinary chat added a permanent LIVE entry per message, forever.
      LIVE.add(self);
      const make =
        options.createPlayer ??
        ((onError) =>
          new VoiceStreamPlayer({
            gapSeconds: options.gapSeconds,
            onError,
            // A reply that finished speaking has nothing left to silence.
            // The caller's own onEnded still fires; this just stops the
            // set growing by one entry per spoken reply as well.
            onEnded: () => {
              LIVE.delete(self);
              options.onEnded?.();
            },
          }));
      // The player's own code, not a blanket device error: a 404 for an
      // expired wav is not a missing sound card (KÖK 14).
      player = make((err) => report(voiceErrorCode(err)));
    }
    return player;
  };

  const self: StreamVoice = {
    get active() {
      return player !== null;
    },

    handle(event: StreamEvent) {
      switch (event.type) {
        case "voice_chunk":
          ensure().push(event.audio_id);
          break;
        case "voice_error":
          // The backend stopped the utterance rather than skipping a sentence,
          // so the user is told. Silence with no explanation is the one
          // failure mode this feature is not allowed to have.
          report(event.code);
          player?.finish();
          break;
        case "voice_notice": {
          // A warning, not an error: the audio is playing. What the reader
          // gets is OUR sentence for what the engine reported, never the
          // engine's own words - see NOTICE_SENTENCES above for the whole
          // argument. An unrecognised note is not shown; the backend has
          // already logged it with more detail than a toast could hold.
          //
          // Still the one call in the app that passes a sentence the
          // catalogue did not write, and still named in the gate's exemption
          // list so a SECOND one cannot appear quietly.
          const sentence = noticeSentence(event.note);
          if (sentence) {
            useErrorStore
              .getState()
              .pushErrorDirect("tts_notice", sentence, "warning");
          }
          break;
        }
        case "voice_done":
          if (event.truncated) {
            useErrorStore
              .getState()
              .pushErrorDirect(
                "tts_text_truncated",
                getErrorMessage("tts_text_truncated"),
                "warning",
              );
          }
          if (event.dropped) {
            useErrorStore
              .getState()
              .pushErrorDirect(
                "tts_lines_dropped",
                getCountMessage("tts_lines_dropped", event.dropped),
                "warning",
              );
          }
          player?.finish();
          break;
        default:
          break;
      }
    },

    stop() {
      leaveStage(stageSource);
      player?.stop();
      player = null;
      LIVE.delete(self);
    },
  };

  return self;
}
