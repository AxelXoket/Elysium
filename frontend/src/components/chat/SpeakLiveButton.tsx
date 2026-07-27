/**
 * SpeakLiveButton.tsx - "read this one out" while it is still arriving.
 *
 * `SpeakButton` speaks a stored message by id. This one cannot: during a
 * stream the assistant row has not been written yet - deliberately, because a
 * row opened up front and then abandoned resurrects an emptied chat - so there
 * is no id, and the client cannot send the text either since it only holds the
 * stripped view.
 *
 * So it asks the server to wake the reply it is already streaming. The audio
 * then arrives on the SSE stream the app is already reading, which is why
 * nothing here plays anything: pressing it is the whole job.
 *
 * Like every other voice affordance it renders NOTHING without a usable model.
 * A button that can only produce an error toast is a broken promise.
 */

import { useState } from "react";
import { Loader2, Volume2 } from "lucide-react";

import { speakLive } from "@/lib/api/tts";
import { useVoiceReadiness } from "@/lib/voice/useVoiceReadiness";
import { useErrorStore } from "@/lib/errors/errorStore";
import { useUiStore } from "@/lib/store/uiStore";

export function SpeakLiveButton({ chatId }: { chatId: number }) {
  const voice = useVoiceReadiness();
  const continuous = useUiStore((s) => s.continuousVoice);
  const [state, setState] = useState<"idle" | "starting" | "speaking">("idle");

  if (voice.phase === "absent") return null;
  // Continuous mode is already speaking this reply; a second control offering
  // to start it again would be a lie about what pressing it does.
  if (continuous) return null;

  const busy = state !== "idle";
  const onClick = async () => {
    if (busy) return;
    setState("starting");
    try {
      await speakLive(chatId);
      setState("speaking");
    } catch (err) {
      setState("idle");
      // Through the shared map, so the user reads the contract sentence -
      // `tts_nothing_streaming` says to use the per-message button instead.
      useErrorStore.getState().pushError(err);
    }
  };

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy || voice.loading}
      data-voice-loading={voice.loading ? "true" : undefined}
      aria-busy={voice.loading || undefined}
      aria-label="Speak this reply"
      title={voice.hint ?? "Read this reply aloud as it arrives"}
      className="message-action-button"
    >
      {state === "starting" ? (
        <Loader2 size={14} className="animate-spin" />
      ) : (
        <Volume2 size={14} style={{ opacity: state === "speaking" ? 1 : 0.7 }} />
      )}
    </button>
  );
}
