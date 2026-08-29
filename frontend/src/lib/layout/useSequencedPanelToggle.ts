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
 *   expanding  (space needed):   both go at once, column faster
 *
 * The two directions differ because the column may never be wider than the
 * canvas holding it. On collapse the canvas grows first, so a column held at
 * its old width always fits and can wait its turn. On expand the canvas is
 * about to shrink, so the column has to be giving up width at least as fast as
 * the panel takes it - which a shorter duration on the same easing guarantees
 * at every instant. Making expand wait its turn instead was tried and felt like
 * lag: nothing moved for a third of a second after the press.
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
        // BOTH START NOW. An earlier version waited COLUMN_MS before letting
        // the panel go, so that the column had already made room; pressing the
        // handle then did nothing visible for a third of a second and read as
        // lag, which is worse than anything it was buying.
        //
        // Starting together is safe because the column is the FASTER of the
        // two. The panel takes its width on an eased curve over PANEL_MS while
        // the column gives it up on the same curve over the shorter
        // COLUMN_MS, and an easing function is monotonic - so at every instant
        // the column has already surrendered at least as much as the panel has
        // claimed, and it never overflows the canvas. (`max-width: 100%` on
        // the column is the belt to that braces.)
        //
        // `Math.max` guards a window narrow enough that the panel would leave
        // nothing: the column stops at 1px rather than going negative.
        const target = Math.max(1, available - incomingPanelWidth());
        setColumnWidth(available);
        toggle();
        // One frame at the current width before the target lands, or the
        // browser has no start value to interpolate from and it snaps.
        after(0, () => setColumnWidth(target));
        after(PANEL_MS, () => {
          setColumnWidth(null);
          setAnimating(false);
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
