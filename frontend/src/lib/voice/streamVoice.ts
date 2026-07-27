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
import { getErrorMessage } from "../errors/errorMessages";
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
        case "voice_notice":
          // A warning, not an error: the audio is playing. Everything below
          // was detected correctly by the backend and had no carrier at all -
          // on a machine without MSVC/triton the engine fell back to eager
          // decoding on every load, speech ran 2-3x slower forever, and
          // nothing anywhere said so.
          useErrorStore
            .getState()
            .pushErrorDirect("tts_notice", event.note, "warning");
          break;
        case "voice_done":
          if (event.truncated) {
            useErrorStore
              .getState()
              .pushErrorDirect(
                "tts_text_truncated",
                "The reply was too long to read in full, so the end was not spoken.",
                "warning",
              );
          }
          if (event.dropped) {
            useErrorStore
              .getState()
              .pushErrorDirect(
                "tts_lines_dropped",
                event.dropped === 1
                  ? "One line of the reply could not be spoken."
                  : `${event.dropped} lines of the reply could not be spoken.`,
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
