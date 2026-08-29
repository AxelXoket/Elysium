import { useCallback, useEffect, useRef, useState } from "react";

/**
 * useSequencedPanelToggle - keep the conversation still while a side panel moves.
 *
 * THE PROBLEM. The chat column is a flex sibling of both panels, so animating a
 * panel's width sweeps the column's width across the same frames. Message
 * bubbles are sized as a percentage of that column, which means every bubble
 * re-runs line breaking on every one of the ~25 frames. Line breaks arriving in
 * jumps is what makes an ordinary reflow read as the text being retyped.
 *
 * THE RULE. The two motions never overlap. The panel moves while the column
 * holds its width, then the column moves on its own:
 *
 *   collapsing (space appears):  panel closes  ->  then column widens
 *   expanding  (space needed):   column narrows ->  then panel opens
 *
 * The order flips because the column may never be wider than the canvas holding
 * it. On collapse the canvas grows first, so a held-back column always fits. On
 * expand the canvas is about to shrink, so the column has to give up its width
 * BEFORE the panel takes it, or it would overflow for the length of the
 * animation.
 *
 * WHY A PINNED PIXEL WIDTH AND NOT A DELAY. A `transition-delay` on the column
 * cannot help: the column has no width of its own to delay, it inherits the
 * canvas' flex sizing and follows it in the same frame. Pinning an explicit
 * width is what decouples the two, and once pinned the second motion is an
 * ordinary width transition.
 *
 * The pin is always cleared at the end, so the column is fluid again for window
 * resizes - which nobody complained about, because a hand-drag never moves a
 * wrap point far in one frame.
 */

/** Matches `--duration-panel` in index.css. */
export const PANEL_MS = 420;
/** Matches the `.chat-gutter` width transition in index.css. */
export const COLUMN_MS = 320;

export interface SequencedToggle {
  /** Pinned column width in px, or null while the column is fluid. */
  columnWidth: number | null;
  /** True while either half of the sequence is running. */
  animating: boolean;
  /** Wrap a panel toggle so it runs in sequence with the column. */
  run: (toggle: () => void, opening: boolean) => void;
}

export function useSequencedPanelToggle(
  /** The element whose width the column has to stay inside. */
  measure: () => number | null,
  /** Width the canvas is about to lose, when a panel is opening. */
  incomingPanelWidth: () => number,
): SequencedToggle {
  const [columnWidth, setColumnWidth] = useState<number | null>(null);
  const [animating, setAnimating] = useState(false);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  const clearTimers = useCallback(() => {
    for (const t of timers.current) clearTimeout(t);
    timers.current = [];
  }, []);

  useEffect(() => clearTimers, [clearTimers]);

  const after = useCallback((ms: number, fn: () => void) => {
    timers.current.push(setTimeout(fn, ms));
  }, []);

  const run = useCallback(
    (toggle: () => void, opening: boolean) => {
      // A second press mid-sequence abandons the first rather than queueing
      // behind it: the reader has changed their mind, and finishing a motion
      // they have already overridden is the one thing worse than the motion.
      clearTimers();
      const available = measure();
      if (available == null || available <= 0) {
        // No measurement, no sequencing - fall back to today's behaviour
        // rather than pinning the column to a number we invented.
        setColumnWidth(null);
        setAnimating(false);
        toggle();
        return;
      }

      setAnimating(true);
      if (opening) {
        // Narrow first, so the column has already made room by the time the
        // panel arrives. `Math.max` guards a window narrow enough that the
        // panel would leave nothing: the column stops at 1px rather than
        // going negative and disappearing.
        const target = Math.max(1, available - incomingPanelWidth());
        setColumnWidth(available);
        // One frame at the current width before the target lands, or the
        // browser has no start value to interpolate from and it snaps.
        after(0, () => setColumnWidth(target));
        after(COLUMN_MS, () => {
          toggle();
          after(PANEL_MS, () => {
            setColumnWidth(null);
            setAnimating(false);
          });
        });
        return;
      }

      // Collapsing: hold the column exactly where it is, let the panel go,
      // then take the space that opened up.
      setColumnWidth(available);
      toggle();
      after(PANEL_MS, () => {
        const grown = measure();
        setColumnWidth(grown != null && grown > 0 ? grown : null);
        after(COLUMN_MS, () => {
          setColumnWidth(null);
          setAnimating(false);
        });
      });
    },
    [after, clearTimers, incomingPanelWidth, measure],
  );

  return { columnWidth, animating, run };
}
