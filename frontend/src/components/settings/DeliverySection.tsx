/**
 * DeliverySection.tsx - the two delivery dials, plus a way to hear them.
 *
 * The reference clip decides what the voice SOUNDS like; tags decide how it
 * PERFORMS. That distinction is the whole reason this section exists: "make
 * her deeper, slower, closer" is answerable here in one line of text, with no
 * hunting for a new recording and no DSP touching the audio.
 *
 * The palette is not decoration. These particular phrasings were the ones that
 * actually worked during the engine bake-off, and offering them saves a person
 * from discovering by trial and error that this engine takes free-form prose
 * rather than a fixed vocabulary.
 *
 * Preview matters for the same reason: a delivery dial you cannot hear is a
 * dial you tune by superstition.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Play } from "lucide-react";

import { getTagPrefs, saveTagPrefs, speakText, ttsAudioUrl } from "@/lib/api/tts";
import { isApiError } from "@/lib/api/client";
import { getErrorMessage } from "@/lib/errors/errorMessages";
import { useErrorStore } from "@/lib/errors/errorStore";
import { leaveStage, takeStage, type VoiceSource } from "@/lib/voice/stage";
import { keys } from "@/lib/query/keys";

/** Phrasings measured to work on the tag-reading engine, not invented here. */
const PALETTE = [
  "low voice, slow",
  "warm, close to the ear",
  "soft, intimate",
  "bright, playful",
  "cold, clipped",
  "breathless",
];

const PREVIEW_LINE = "I was starting to think you would never ask.";

export function DeliverySection() {
  const qc = useQueryClient();
  const [prefs, setPrefs] = useState<{
    density: number;
    tone: string;
    min: number;
    max: number;
    toneMax: number;
    speed: number;
    speedMin: number;
    speedMax: number;
    gap: number;
    gapMin: number;
    gapMax: number;
  } | null>(null);
  const [tone, setTone] = useState("");
  //: Set only when the fetch failed for a reason that is NOT "voice is not
  //: set up". Keeps the two cases apart, which is the whole fix.
  const [loadError, setLoadError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  // Request sequence: a preview abandoned by unmount or by a newer press must
  // not come back seconds later as ghost audio - the exact failure playerStore
  // documents and guards against, which this raw `new Audio()` did not.
  const seqRef = useRef(0);

  const stopPreview = useCallback(() => {
    seqRef.current += 1;
    audioRef.current?.pause();
    audioRef.current = null;
    setPreviewing(false);
  }, []);

  // The preview is a third voice source; without a seat on the shared stage it
  // talked over message speech and kept talking over the vault lock screen.
  const stageSource = useMemo<VoiceSource>(
    () => ({ silence: stopPreview }),
    [stopPreview],
  );

  useEffect(() => {
    let alive = true;
    getTagPrefs()
      .then((p) => {
        if (!alive) return;
        setPrefs({
          density: p.density,
          tone: p.tone,
          min: p.min,
          max: p.max,
          toneMax: p.tone_max_chars,
          speed: p.speed,
          speedMin: p.speed_min,
          speedMax: p.speed_max,
          gap: p.gap,
          gapMin: p.gap_min,
          gapMax: p.gap_max,
        });
        setTone(p.tone);
      })
      .catch((err: unknown) => {
        if (!alive) return;
        // "Voice may simply not be set up" is true for exactly one shape, and
        // this used to swallow every other one with it: a 500, a timeout or a
        // locked vault also produced `prefs === null`, and the `return null`
        // below then removed the whole section - presenting a broken backend
        // as a feature that does not exist. The sibling EngineSetupSection in
        // this same page does the opposite and says so in its comment.
        if (isApiError(err) && (err.status === 404 || err.detail === "tts_not_configured")) {
          return;
        }
        setLoadError(getErrorMessage(isApiError(err) ? err.detail : undefined));
      });
    return () => {
      alive = false;
      stopPreview();
      leaveStage(stageSource);
    };
  }, [stopPreview, stageSource]);

  // Two different silences, told apart. Nothing to show when voice is simply
  // not configured; a visible sentence when the settings existed and could not
  // be read - P4 forbids the second one disappearing as though it were the
  // first.
  if (loadError) {
    return (
      <p role="alert" className="settings-hint">
        {loadError}
      </p>
    );
  }
  if (!prefs) return null;

  const persist = (patch: {
    density?: number;
    tone?: string;
    speed?: number;
    gap?: number;
  }) => {
    saveTagPrefs(patch)
      .then((p) =>
        setPrefs((prev) =>
          prev
            ? {
                ...prev,
                density: p.density,
                tone: p.tone,
                speed: p.speed,
                gap: p.gap,
              }
            : prev,
        ),
      )
      .catch((err) => useErrorStore.getState().pushError(err))
      // The stream reads these through useTagPrefs, not from this
      // component. Without the invalidation a just-moved pause slider goes
      // on doing nothing to the next reply, which is the exact complaint
      // this dial exists to answer.
      .finally(() => {
        void qc.invalidateQueries({ queryKey: keys.ttsTagPrefs() });
      });
  };

  const preview = async () => {
    if (previewing) return;
    // Anything else speaking stops now, and this preview becomes the thing a
    // vault lock silences.
    takeStage(stageSource);
    const seq = ++seqRef.current;
    setPreviewing(true);
    try {
      // Through the REAL speak path, so what is heard is what a reply would
      // sound like - a preview rendered by some other route would be a
      // different promise from the one being tuned.
      const result = await speakText(PREVIEW_LINE);
      // Synthesis takes seconds. Unmounted (dialog closed), stopped, or
      // superseded meanwhile? Then this response is abandoned: playing it
      // would be a voice from nowhere with no control able to stop it.
      if (seqRef.current !== seq) return;
      audioRef.current?.pause();
      const audio = new Audio(ttsAudioUrl(result.audio_id));
      audioRef.current = audio;
      audio.onended = () => {
        if (seqRef.current === seq) setPreviewing(false);
      };
      audio.onerror = () => {
        if (seqRef.current === seq) setPreviewing(false);
      };
      await audio.play();
    } catch (err) {
      if (seqRef.current !== seq) return;
      setPreviewing(false);
      useErrorStore.getState().pushError(err);
    }
  };

  return (
    <section aria-label="Delivery" className="settings-voice-section space-y-2">
      <h3 className="settings-section-title">Delivery</h3>

      <label className="block space-y-1">
        <span className="settings-label">Standing tone</span>
        <span className="settings-hint opacity-70">
          Added to the start of every spoken reply. Plain words, no brackets.
        </span>
        <input
          type="text"
          value={tone}
          maxLength={prefs.toneMax}
          placeholder="e.g. low voice, slow"
          onChange={(e) => setTone(e.target.value)}
          onBlur={() => tone !== prefs.tone && persist({ tone })}
          className="settings-text-input w-full"
        />
      </label>

      <div className="flex flex-wrap gap-1">
        {PALETTE.map((option) => (
          <button
            key={option}
            type="button"
            className="settings-segment-button"
            data-active={tone === option ? "true" : undefined}
            onClick={() => {
              setTone(option);
              persist({ tone: option });
            }}
          >
            {option}
          </button>
        ))}
        {tone !== "" && (
          <button
            type="button"
            className="settings-segment-button"
            onClick={() => {
              setTone("");
              persist({ tone: "" });
            }}
          >
            Clear
          </button>
        )}
      </div>

      <label className="block space-y-1">
        <span className="settings-label">
          Direction density: {prefs.density}
        </span>
        <span className="settings-hint opacity-70">
          How many delivery directions one reply may keep. Lower is plainer;
          words are never removed.
        </span>
        <input
          type="range"
          aria-label="Direction density"
          min={prefs.min}
          max={prefs.max}
          value={prefs.density}
          onChange={(e) =>
            setPrefs({ ...prefs, density: Number(e.target.value) })
          }
          onPointerUp={() => persist({ density: prefs.density })}
          onKeyUp={() => persist({ density: prefs.density })}
          className="w-full"
        />
      </label>

      <label className="block space-y-1">
        <span className="settings-label">
          Reading speed: {prefs.speed.toFixed(2)}×
        </span>
        <span className="settings-hint opacity-70">
          How fast replies are spoken. Applied by Elysium, so it works the same
          on every voice model.
        </span>
        <input
          type="range"
          aria-label="Reading speed"
          min={prefs.speedMin}
          max={prefs.speedMax}
          step={0.05}
          value={prefs.speed}
          onChange={(e) =>
            setPrefs({ ...prefs, speed: Number(e.target.value) })
          }
          onPointerUp={() => persist({ speed: prefs.speed })}
          onKeyUp={() => persist({ speed: prefs.speed })}
          className="w-full"
        />
      </label>

      <label className="block space-y-1">
        <span className="settings-label">
          Pause between sentences:{" "}
          {prefs.gap === 0 ? "none" : `${prefs.gap.toFixed(2)}s`}
        </span>
        <span className="settings-hint opacity-70">
          Extra silence between spoken sentences. Playback only - it changes
          nothing about the audio itself.
        </span>
        <input
          type="range"
          aria-label="Pause between sentences"
          min={prefs.gapMin}
          max={prefs.gapMax}
          step={0.05}
          value={prefs.gap}
          onChange={(e) => setPrefs({ ...prefs, gap: Number(e.target.value) })}
          onPointerUp={() => persist({ gap: prefs.gap })}
          onKeyUp={() => persist({ gap: prefs.gap })}
          className="w-full"
        />
      </label>

      <button
        type="button"
        onClick={preview}
        disabled={previewing}
        className="settings-segment-button inline-flex items-center gap-1"
      >
        {previewing ? (
          <Loader2 size={12} className="animate-spin" />
        ) : (
          <Play size={12} />
        )}
        Hear it
      </button>
    </section>
  );
}
