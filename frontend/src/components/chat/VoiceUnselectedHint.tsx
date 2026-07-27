/**
 * VoiceUnselectedHint.tsx - the one line that breaks the silence.
 *
 * Every in-chat voice control - the per-message Speak button, the live-speak
 * button, the composer toggle - renders nothing until a model is SELECTED.
 * That rule is right: an affordance that can only produce an error toast is a
 * broken promise.
 *
 * But it treated two very different states as one. "Voice does not exist on
 * this machine" deserves silence; there is nothing to offer. "An engine is
 * installed, a model is downloaded, a reference voice is recorded - and
 * nothing is selected" deserves a sentence, because the person did every part
 * of the setup and their chat looks identical to a fresh install, with nothing
 * anywhere to explain why.
 *
 * `voice_installed` (from /tts/active, a cheap runtimes.json read) is what
 * tells them apart. Dismissible and remembered: a hint that cannot be silenced
 * is a nag, and somebody who deliberately keeps voice off should be able to
 * say so once.
 */

import { Volume2, X } from "lucide-react";

import { useTtsActive } from "@/lib/query/tts";
import { useUiStore } from "@/lib/store/uiStore";

export function VoiceUnselectedHint() {
  const active = useTtsActive();
  const dismissed = useUiStore((s) => s.voiceHintDismissed);
  const dismiss = useUiStore((s) => s.dismissVoiceHint);
  const openSettings = useUiStore((s) => s.openSettings);

  if (dismissed) return null;
  // An engine is set up...
  if (!active.data?.voice_installed) return null;
  // ...and nothing is chosen. A selected-but-unrunnable model is NOT this case:
  // the settings page already lists every blocker for it, in words.
  if (active.data.uid) return null;

  return (
    <div className="voice-hint" role="status">
      <Volume2 size={13} aria-hidden="true" className="shrink-0" />
      <span className="min-w-0 flex-1">
        Voice is set up, but no voice is chosen yet.{" "}
        <button
          type="button"
          className="voice-hint-link"
          onClick={() => openSettings("voice")}
        >
          Choose one
        </button>{" "}
        to hear replies read aloud.
      </span>
      <button
        type="button"
        className="voice-hint-dismiss"
        aria-label="Dismiss voice hint"
        onClick={dismiss}
      >
        <X size={12} />
      </button>
    </div>
  );
}
