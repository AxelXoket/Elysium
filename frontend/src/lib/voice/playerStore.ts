/**
 * playerStore.ts - the ONE audio element voice speaks through.
 *
 * A store rather than per-button state for one reason: two messages must never
 * talk over each other. Pressing speak on message B while A is playing stops A
 * first - the alternative (overlapping voices of the same character) is
 * instantly, obviously broken in a way no error message could excuse.
 *
 * The request sequence is the whole correctness story (audit-2): EVERY
 * transition that abandons a request - a new speak(), AND stop() - advances
 * `requestSeq`, so a response that arrives for an abandoned request finds the
 * guard closed. The audit demonstrated the original sin precisely: stop()
 * reset the visible state but not the sequence, so a stopped synthesis came
 * back seconds later as GHOST AUDIO - playing, with no button anywhere
 * showing a stop face, unstoppable except by speaking something else.
 *
 * Not persisted: playback is session state, and the backend wipes the audio
 * cache on lock/unload/exit anyway - a persisted audio_id would point at a
 * file that is deliberately gone.
 *
 * Errors surface through the shared error store with the backend's own code
 * (tts_* codes all have human sentences in errorMessages.ts). Silence is the
 * one failure mode this feature is not allowed to have - but a rejection we
 * CAUSED by stopping is not an error, and must not raise a spurious toast.
 */

import { create } from "zustand";

import { stopAllStreamVoices } from "./streamVoice";
import { VoiceStreamPlayer, voiceErrorCode } from "./streamPlayer";
import { clearStage, leaveStage, takeStage, type VoiceSource } from "./stage";

import { streamMessageSpeech } from "../api/tts";
import { getErrorMessage } from "../errors/errorMessages";
import { useErrorStore } from "../errors/errorStore";

export type VoicePhase = "idle" | "requesting" | "playing";

interface VoicePlayerState {
  /** The message currently being spoken (or fetched), else null. */
  messageId: number | null;
  phase: VoicePhase;
  /** Advanced by every speak() AND every stop(): any async continuation
   * holding an older seq is abandoned and must do nothing. */
  requestSeq: number;
  speak: (messageId: number) => Promise<void>;
  stop: () => void;
}

// Module-level, not in the store: a player and an in-flight request are
// devices to drive, not state to render from.
//
// There used to be an HTMLAudioElement here, holding the ONE joined file
// `/tts/speak` returned. The Speak button streams now, so the audio arrives as
// chunks and is scheduled on the Web Audio clock - the same path a live reply
// takes, and the same crossfade between pieces.
let streaming: VoiceStreamPlayer | null = null;
let inflight: AbortController | null = null;

function dropAudio() {
  // Abort FIRST. Stopping the player while the request is still running would
  // let the next chunk arrive and create a new one underneath the silence -
  // the ghost-audio shape, one layer down.
  inflight?.abort();
  inflight = null;
  streaming?.stop();
  streaming = null;
}

/**
 * This store's seat on the shared stage. `silence` is stop()'s teardown minus
 * the stage bookkeeping: another source (a streamed reply, the Delivery
 * preview) calls it when it takes over, and the requestSeq bump keeps an
 * in-flight speak() response from resurrecting as ghost audio underneath it.
 */
const stageSource: VoiceSource = {
  silence: () => {
    dropAudio();
    useVoicePlayer.setState((s) => ({
      messageId: null,
      phase: "idle",
      requestSeq: s.requestSeq + 1,
    }));
  },
};

export const useVoicePlayer = create<VoicePlayerState>((set, get) => ({
  messageId: null,
  phase: "idle",
  requestSeq: 0,

  stop: () => {
    leaveStage(stageSource);
    dropAudio();
    // The seq bump IS the fix for ghost audio: an in-flight speak() response
    // now fails its guard instead of resurrecting into a state no button
    // can stop.
    set((s) => ({
      messageId: null,
      phase: "idle",
      requestSeq: s.requestSeq + 1,
    }));
  },

  speak: async (messageId: number) => {
    const seq = get().requestSeq + 1;
    // Whatever was playing stops NOW - not when the new audio arrives, and
    // not only when it happens to be THIS store's element. dropAudio() alone
    // could not see a streamed reply or the Delivery preview, so Speak used to
    // start a second simultaneous voice instead of replacing the first.
    takeStage(stageSource);
    dropAudio();
    set({ messageId, phase: "requesting", requestSeq: seq });

    try {
      // The pieces arrive as they are made and go straight to the scheduler,
      // which owns the seam between them. Waiting for the whole reply was the
      // old behaviour and it was the whole latency: `/tts/speak` joins every
      // sentence into one file and answers at the end, so a long message was
      // twenty seconds of silence before any sound at all.
      const controller = new AbortController();
      inflight = controller;
      let heard = false;
      // One toast per silence. Its sibling streamVoice.ts has carried this
      // flag from the start, with the comment that says why: a backend
      // voice_error arriving before any chunk produced the specific code
      // AND then the generic tts_synthesis_failed from the `!heard` branch
      // below - two toasts for one failure, reading as two separate faults.
      let reported = false;
      const report = (code: string) => {
        if (reported || get().requestSeq !== seq) return;
        reported = true;
        useErrorStore.getState().pushErrorDirect(code, getErrorMessage(code));
      };
      // The player DIRECTLY, not `createStreamVoice`: that helper takes its own
      // seat on the shared stage, so wrapping it here made this store silence
      // ITSELF the moment the first chunk arrived - it had taken the stage two
      // lines earlier. One source, one seat.
      const player = new VoiceStreamPlayer({
        // The player's own code (KÖK 14). Every failure used to be reported
        // as tts_audio_device_error - "No audio output device is available"
        // - so a backend restart or an expired wav sent people looking for
        // a sound-card driver.
        onError: (err) => report(voiceErrorCode(err)),
        // Fires when the LAST chunk finishes PLAYING, which is well after the
        // request ends: the stream stops arriving long before the audio stops
        // sounding, and the button has to keep offering Stop until it does.
        onEnded: () => {
          if (get().requestSeq === seq) {
            set({ messageId: null, phase: "idle" });
          }
        },
      });
      streaming = player;
      await streamMessageSpeech(
        { messageId },
        {
          signal: controller.signal,
          onEvent: (event) => {
            // The guard is checked per EVENT, not once around the request: a
            // stream lives for seconds and stop() can land at any point in it.
            if (get().requestSeq !== seq) return;
            if (event.type === "voice_chunk") {
              if (!heard) {
                heard = true;
                set({ phase: "playing" });
              }
              player.push(event.audio_id);
            } else if (event.type === "voice_error") {
              // The backend stopped the utterance rather than skipping a
              // sentence, so the user is told which failure it was. Silence
              // with no explanation is the one failure mode voice is not
              // allowed to have.
              report(event.code);
              player.finish();
            } else if (event.type === "voice_done") {
              player.finish();
            }
          },
        },
      );
      if (get().requestSeq !== seq) return;
      if (!heard) {
        // The utterance ended without producing a single chunk. Something was
        // wrong and nobody said so - exactly the silent failure this feature
        // is not allowed to have.
        set({ messageId: null, phase: "idle" });
        // Only if nothing has already explained the silence.
        report("tts_synthesis_failed");
        return;
      }
      // Deliberately NOT setting idle here: the request has finished
      // arriving, the audio has not finished playing. `onEnded` above closes
      // that out when the scheduler really is done.
    } catch (err) {
      if (get().requestSeq !== seq) {
        // We caused this: stop() aborted the stream, or a newer speak took
        // over. Not an error, no toast - a user-initiated stop must never
        // scold the user.
        return;
      }
      dropAudio();
      set({ messageId: null, phase: "idle" });
      // pushError runs parseApiError itself; the backend detail code carries
      // through to the mapped sentence.
      useErrorStore.getState().pushError(err);
    }
  },
}));

/** Stop playback from outside React (the vault-lock path). The synthesized
 * speech of a conversation must not keep playing over the lock screen. */
export function stopVoicePlayback(): void {
  useVoicePlayer.getState().stop();
  // Belt and braces: the stage silences the current occupant, and these two
  // sweep any source that never took it (or lost it to a newer one).
  clearStage();
  // The per-message player is not the only thing that can be speaking. A reply
  // streamed with continuous mode on plays through its own scheduler, outside
  // this store - so locking the vault used to leave the conversation being read
  // aloud over the lock screen. Both surfaces have to go silent together.
  stopAllStreamVoices();
}
