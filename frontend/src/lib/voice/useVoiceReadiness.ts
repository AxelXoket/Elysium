/**
 * useVoiceReadiness.ts - one answer to "can this button speak yet?".
 *
 * Three controls ask it (SpeakButton, SpeakLiveButton, ContinuousVoiceToggle)
 * and they used to each re-derive it from `active.data`, which is how they
 * ended up agreeing about "no model chosen" and saying nothing at all about
 * "the model is still coming up".
 *
 * That second state is not rare - it is the normal first minute after unlock.
 * The model now preloads in the background (routers/vault.py), and a cold Fish
 * S2 pays a torch.compile that its own progress line calls "first compile is
 * slow". Before this, a button pressed in that window looked broken: no sound,
 * no spinner, no sentence, and (on the /speak path) an error toast for a
 * perfectly healthy engine that simply was not up yet.
 */

import { useTtsActive, useTtsState } from "@/lib/query/tts";

export type VoicePhase =
  /** No voice model chosen, or the chosen one cannot run - render nothing. */
  | "absent"
  /** The model is coming up. Show it, say so, do not act on a press. */
  | "loading"
  /** Ready to speak. */
  | "ready";

export interface VoiceReadiness {
  phase: VoicePhase;
  /** True while the engine is coming up - drives the pulse and the tooltip. */
  loading: boolean;
  /** Sentence for `title`, or null when there is nothing to explain. */
  hint: string | null;
}

/** What every voice control shows while the engine is coming up. */
export const VOICE_LOADING_HINT =
  "The voice model is still loading - this takes a moment after opening Elysium.";

/** Sentence for a worker that died under us. */
export const VOICE_CRASHED_HINT =
  "The voice engine stopped. Open Settings > Voice to load it again.";

export function useVoiceReadiness(): VoiceReadiness {
  const active = useTtsActive();
  // The heartbeat, mounted HERE (KÖK 15). /tts/state is the only endpoint that
  // calls poll_health(), and useTtsState was imported by nothing at all - so
  // the backend never got round to noticing a dead worker, /tts/active went on
  // reporting a loaded model, and the first anyone heard of it was pressing
  // Speak. This hook is already mounted by every voice control, which is
  // exactly the lifetime the poll needs.
  const health = useTtsState();
  const data = active.data;

  if (!data?.uid) return { phase: "absent", loading: false, hint: null };
  // A crashed engine can only produce an error toast, which is the one thing
  // this file refuses to offer a button for. It disappears; Settings > Voice
  // is where the death reason is spelled out.
  if (health.data?.state === "error") {
    return { phase: "absent", loading: false, hint: VOICE_CRASHED_HINT };
  }
  // A model whose readiness says it cannot run gets no affordance at all: an
  // icon that can only produce an error toast is a broken promise, and the
  // settings page already lists every blocker for it in words.
  if (data.readiness?.runnable === false) {
    return { phase: "absent", loading: false, hint: null };
  }
  if (data.state === "loading") {
    return { phase: "loading", loading: true, hint: VOICE_LOADING_HINT };
  }
  return { phase: "ready", loading: false, hint: null };
}
