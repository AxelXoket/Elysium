/**
 * SpeakButton.tsx - the per-message "hear this" affordance.
 *
 * Renders nothing at all unless a voice model is actually selected - a speaker
 * icon that only ever produces an error toast would be a broken promise, and
 * the readiness system upstream exists precisely so we can know better.
 *
 * Speaking uses message_id, never the visible text: the client only ever holds
 * the STRIPPED view, and the delivery tags that make the voice worth hearing
 * live in the raw row on the backend.
 *
 * One button, three faces: speaker (idle), spinner (synthesizing - a real
 * model takes seconds), square (playing - press to stop). The shared player
 * store guarantees two messages never talk over each other.
 */

import { Loader2, Square, Volume2 } from "lucide-react";

import { useVoicePlayer } from "@/lib/voice/playerStore";
import { useVoiceReadiness } from "@/lib/voice/useVoiceReadiness";

export function SpeakButton({ messageId }: { messageId: number }) {
  const voice = useVoiceReadiness();
  const playerMessageId = useVoicePlayer((s) => s.messageId);
  const phase = useVoicePlayer((s) => s.phase);
  const speak = useVoicePlayer((s) => s.speak);
  const stop = useVoicePlayer((s) => s.stop);

  // No selected voice model -> no affordance; and a SELECTED model whose
  // readiness says it cannot run gets none either (audit-2) - a speaker icon
  // that can only produce an error toast is a broken promise, and the verdict
  // travels with the active payload precisely so this cannot be forgotten.
  //
  // "Still coming up" is NOT that case: the button stays, pulses, and says so.
  // The model preloads after unlock and a cold engine takes tens of seconds;
  // hiding the button for that whole window - or letting a press fail - would
  // be the same broken promise from the other side.
  if (voice.phase === "absent") return null;

  const mine = playerMessageId === messageId;
  const busy = mine && phase === "requesting";
  const playing = mine && phase === "playing";

  const label = playing ? "Stop speaking" : "Speak message";
  return (
    <button
      type="button"
      className="message-action-button"
      data-voice-loading={voice.loading ? "true" : undefined}
      aria-label={label}
      aria-busy={voice.loading || undefined}
      title={voice.hint ?? (busy ? "Preparing audio…" : label)}
      aria-pressed={playing}
      disabled={voice.loading}
      onClick={() => {
        if (playing || busy) stop();
        else void speak(messageId);
      }}
    >
      {busy ? (
        <Loader2 size={13} className="animate-spin" />
      ) : playing ? (
        <Square size={13} />
      ) : (
        <Volume2 size={13} />
      )}
    </button>
  );
}
