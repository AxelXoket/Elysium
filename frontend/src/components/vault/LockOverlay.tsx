/**
 * LockOverlay - the "vault snapping shut" moment, played OVER the live app.
 *
 * Choreography (~1.65s, transform/opacity only - no backdrop-filter per the
 * living-mist law):
 *   phase 1  ON the main screen (a light veil only - the app stays visible):
 *     0.00s  gentle dim veil fades in
 *     0.08s  the lock DRAWS itself - body and open shackle stroke in
 *            (pathLength), floating into place; two mist wisps curl beneath
 *     0.50s  the shackle SNAPS down (stiff spring, slight overshoot);
 *            the body answers with a compression pulse, a ring of light
 *            expands, and the brand sparkle twinkles beside the shackle
 *     0.70s  COMMIT - the actual lock API fires here (onCommit), so the
 *            gate flips only once the lock has visibly closed
 *   phase 2  the handoff:
 *     0.72s  the veil deepens to full ink (the world closes) while the
 *            keyhole glows awake
 *     1.20s  everything dissolves, the glyph drifts up and fades - the lock
 *            screen is already breathing in underneath
 *
 * Reduced motion: a plain veil fade; commit at 0.12s, total 0.55s.
 */
import { useEffect } from "react";
import { motion as m } from "motion/react";
import { useReducedMotion } from "@/components/motion/ReducedMotion";

/* The commit (real lock API -> gate flip) must land INSIDE the full-ink
 * window, or the app->lock-screen swap shows through the half-drawn veil:
 * ink ramps 540-760ms (right after the 500ms snap), commit at 820ms, ink
 * holds SOLID until 1600ms (a proper beat of darkness - the user asked for
 * breathing room here), then a 400ms reveal. Timer lateness only pushes the
 * commit deeper into the covered window - never earlier. */
const TOTAL_MS = 2000;
const COMMIT_MS = 820;
const REDUCED_TOTAL_MS = 550;
const REDUCED_COMMIT_MS = 240;

const EASE_SMOOTH: [number, number, number, number] = [0.4, 0, 0.2, 1];
const STROKE = "#DEE9F5";

export function LockOverlay({
  onCommit,
  onDone,
}: {
  onCommit: () => void;
  onDone: () => void;
}) {
  const reduced = useReducedMotion();

  useEffect(() => {
    const commit = setTimeout(onCommit, reduced ? REDUCED_COMMIT_MS : COMMIT_MS);
    const done = setTimeout(onDone, reduced ? REDUCED_TOTAL_MS : TOTAL_MS);
    return () => {
      clearTimeout(commit);
      clearTimeout(done);
    };
  }, [onCommit, onDone, reduced]);

  const totalS = (reduced ? REDUCED_TOTAL_MS : TOTAL_MS) / 1000;

  return (
    <div className="lock-overlay" role="status" aria-label="Locking Elysium">
      {/* phase 1 veil: light - the app remains clearly visible beneath */}
      <m.div
        className="lock-overlay-dim"
        initial={{ opacity: 0 }}
        animate={{ opacity: [0, 1, 1, 0] }}
        transition={{ duration: totalS, times: [0, 0.08, 0.8, 1], ease: "easeOut" }}
      />
      {/* phase 2 veil: full ink - sweeps in right after the snap and is
          FULLY opaque before the commit fires (the gate flip hides under it) */}
      <m.div
        className="lock-overlay-ink"
        initial={{ opacity: 0 }}
        animate={{ opacity: reduced ? [0, 1, 1, 0] : [0, 0, 1, 1, 0] }}
        transition={
          reduced
            ? { duration: totalS, times: [0, 0.35, 0.7, 1], ease: "easeInOut" }
            : {
                duration: totalS,
                times: [0, 0.27, 0.38, 0.8, 1],
                ease: ["linear", EASE_SMOOTH, "linear", EASE_SMOOTH],
              }
        }
      />
      {!reduced && (
        <m.div
          className="lock-overlay-glyph"
          initial={{ scale: 0.9, y: 10, opacity: 0 }}
          animate={{
            scale: [0.9, 1, 1, 1.02],
            y: [10, 0, 0, -10],
            opacity: [0, 1, 1, 0],
          }}
          transition={{
            duration: totalS,
            times: [0, 0.11, 0.75, 1],
            ease: ["easeOut", "linear", EASE_SMOOTH],
          }}
        >
          <svg width="238" height="238" viewBox="0 0 120 120" fill="none" aria-hidden>
            {/* mist wisps grounding the lock - brand's fog, in miniature */}
            <m.path
              d="M 18 104 C 38 97 54 109 76 102 C 88 98 96 101 104 98"
              stroke={STROKE}
              strokeWidth="1.6"
              strokeLinecap="round"
              initial={{ pathLength: 0, opacity: 0 }}
              animate={{ pathLength: 1, opacity: 0.26 }}
              transition={{ delay: 0.16, duration: 0.6, ease: "easeOut" }}
            />
            <m.path
              d="M 30 111 C 48 106 62 114 92 108"
              stroke={STROKE}
              strokeWidth="1.3"
              strokeLinecap="round"
              initial={{ pathLength: 0, opacity: 0 }}
              animate={{ pathLength: 1, opacity: 0.18 }}
              transition={{ delay: 0.24, duration: 0.6, ease: "easeOut" }}
            />

            {/* click ring - expands at the snap */}
            <m.circle
              cx="60"
              cy="72"
              r="40"
              stroke={STROKE}
              strokeWidth="1.4"
              initial={{ scale: 0.55, opacity: 0 }}
              animate={{ scale: [0.55, 0.55, 1.32], opacity: [0, 0.5, 0] }}
              transition={{ duration: 0.58, delay: 0.52, times: [0, 0.1, 1], ease: "easeOut" }}
              style={{ transformOrigin: "60px 72px" }}
            />

            {/* shackle - draws itself open, then snaps down */}
            <m.path
              d="M 42 60 V 44 C 42 32.4 49.8 24 60 24 C 70.2 24 78 32.4 78 44 V 60"
              stroke={STROKE}
              strokeWidth="4.6"
              strokeLinecap="round"
              initial={{ pathLength: 0, y: -16 }}
              animate={{ pathLength: 1, y: 0 }}
              transition={{
                pathLength: { delay: 0.1, duration: 0.42, ease: "easeOut" },
                y: { delay: 0.5, type: "spring", stiffness: 600, damping: 24, mass: 0.95 },
              }}
            />

            {/* body - draws itself in, then answers the snap with a pulse */}
            <m.g
              initial={{ scaleY: 1 }}
              animate={{ scaleY: [1, 0.95, 1.012, 1], scaleX: [1, 1.026, 0.996, 1] }}
              transition={{ duration: 0.32, delay: 0.6, times: [0, 0.35, 0.75, 1], ease: "easeOut" }}
              style={{ transformOrigin: "60px 78px" }}
            >
              {/* faint ink pane so the app shimmers through the body */}
              <m.path
                d="M 44 58 H 76 Q 88 58 88 70 V 86 Q 88 98 76 98 H 44 Q 32 98 32 86 V 70 Q 32 58 44 58 Z"
                fill="rgba(16, 24, 35, 0.38)"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.34, duration: 0.3 }}
              />
              <m.path
                d="M 44 58 H 76 Q 88 58 88 70 V 86 Q 88 98 76 98 H 44 Q 32 98 32 86 V 70 Q 32 58 44 58 Z"
                stroke={STROKE}
                strokeWidth="4.6"
                initial={{ pathLength: 0 }}
                animate={{ pathLength: 1 }}
                transition={{ delay: 0.08, duration: 0.46, ease: "easeOut" }}
              />
              {/* inner accent line - the double-stroke elegance */}
              <m.path
                d="M 46 63 H 74 Q 83 63 83 71 V 85 Q 83 93 74 93 H 46 Q 37 93 37 85 V 71 Q 37 63 46 63 Z"
                stroke={STROKE}
                strokeWidth="1.1"
                initial={{ pathLength: 0, opacity: 0 }}
                animate={{ pathLength: 1, opacity: 0.45 }}
                transition={{ delay: 0.2, duration: 0.44, ease: "easeOut" }}
              />
              {/* keyhole - glows awake as the world closes */}
              <m.circle
                cx="60"
                cy="75"
                r="8.5"
                fill="rgba(160, 195, 235, 0.22)"
                initial={{ opacity: 0 }}
                animate={{ opacity: [0, 0, 0.75, 0.45] }}
                transition={{ duration: totalS, times: [0, 0.44, 0.55, 0.8] }}
              />
              <m.g
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.72, duration: 0.24 }}
              >
                <circle cx="60" cy="73.5" r="4" fill={STROKE} />
                <rect x="58.1" y="75.5" width="3.8" height="8.5" rx="1.9" fill={STROKE} />
              </m.g>
            </m.g>

            {/* brand sparkle - twinkles at the click, like the icon's own star */}
            <m.path
              d="M 0 -7.5 L 1.9 -1.9 L 7.5 0 L 1.9 1.9 L 0 7.5 L -1.9 1.9 L -7.5 0 L -1.9 -1.9 Z"
              fill={STROKE}
              initial={{ scale: 0, rotate: -18, opacity: 0 }}
              animate={{ scale: [0, 1.15, 0], rotate: 24, opacity: [0, 0.95, 0] }}
              transition={{ delay: 0.58, duration: 0.55, times: [0, 0.4, 1], ease: "easeOut" }}
              style={{ transformOrigin: "0px 0px", x: 89, y: 32 }}
            />
          </svg>
        </m.div>
      )}
    </div>
  );
}
