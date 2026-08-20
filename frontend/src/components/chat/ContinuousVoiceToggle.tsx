/**
 * ContinuousVoiceToggle.tsx - "speak every reply", next to Send.
 *
 * It sits beside the send button rather than in Settings because the rule it
 * expresses is about SENDING: flipping it decides what the NEXT reply does, and
 * a control whose meaning is "from your next message onwards" belongs where the
 * next message is written. Settings gets an explanatory row too; both drive the
 * same store field, so they can never disagree.
 *
 * It renders nothing at all when no voice model is selected. A toggle that can
 * only ever produce an error toast is a broken promise - the same rule
 * `SpeakButton` already follows, and the readiness data exists precisely so
 * both can know better.
 */

import { Volume2, VolumeX } from "lucide-react";

import { useUiStore } from "@/lib/store/uiStore";
import { useVoiceReadiness } from "@/lib/voice/useVoiceReadiness";
import { speakWhenStreamBegins } from "@/lib/voice/speakWhenStreamBegins";

export function ContinuousVoiceToggle() {
  const voice = useVoiceReadiness();
  const on = useUiStore((s) => s.continuousVoice);
  const setOn = useUiStore((s) => s.setContinuousVoice);
  const chatId = useUiStore((s) => s.selectedChatId);

  const toggle = () => {
    const next = !on;
    setOn(next);
    // S16: switched on after sending but before the reply started arriving,
    // this speaks THAT reply rather than making the user wait for the next
    // one. Silent if the window has already closed - see the helper.
    if (next && chatId != null) void speakWhenStreamBegins(chatId);
  };

  if (voice.phase === "absent") return null;

  const label = on ? "Stop speaking replies" : "Speak replies aloud";
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      aria-busy={voice.loading || undefined}
      data-voice-loading={voice.loading ? "true" : undefined}
      title={
        voice.hint ??
        (on
          ? "Replies are spoken as they arrive. Turning this off takes effect immediately; turning it on starts from your next message."
          : "Speak each reply as it arrives, starting from your next message.")
      }
      onClick={toggle}
      className="composer-icon-button shrink-0 rounded-xl p-2.5"
      // The two fallbacks are what actually SHIP: neither
      // `--color-es-accent-soft` nor `--color-es-accent` is declared anywhere
      // in index.css, so every render has always resolved to the value after
      // the comma. Stated outright rather than reached through a dead var(),
      // for the reason index.css now gives at .settings-segment-button: the
      // day somebody declares either token, this button changes colour in a
      // diff that never mentions it.
      style={{
        backgroundColor: on
          ? "rgba(28, 38, 50, 0.16)"
          : "rgba(28, 38, 50, 0.08)",
        color: on ? "currentColor" : "var(--color-es-asst-bubble-text)",
      }}
    >
      {on ? <Volume2 size={15} /> : <VolumeX size={15} />}
    </button>
  );
}
