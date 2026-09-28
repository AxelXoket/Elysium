import { useCallback, useLayoutEffect, useState, type ReactNode } from "react";
import { AnimatePresence, motion as m, useIsPresent } from "motion/react";

import { cn } from "@/lib/utils";
import { durationSeconds, easeValues } from "@/lib/theme/tokens";
import { useReducedMotion } from "./ReducedMotion";

interface CrossFadeProps {
  /**
   * Which pane is showing. A CHANGE of this string is the swap, so it has
   * to name the branch and not the condition: two branches that resolve to
   * the same string never animate between each other.
   */
  pane: string;
  children: ReactNode;
  className?: string;
  /**
   * `span` for a swap that sits inside phrasing content, which a button's
   * label is. The default `div` is wrong there and browsers repair it by
   * closing the paragraph around it.
   */
  as?: "div" | "span";
}

/**
 * CrossFade - the swap. One branch leaves, then the other arrives, and the
 * two never overlap.
 *
 * This is the one motion shape the rest of `components/motion/` has no
 * primitive for: a REPLACEMENT rather than an arrival. `Collapse` is for a
 * block that changes the document's height, `FadeIn` for a control that
 * turns up inside a row that already exists; a ternary that swaps an Undo
 * trigger for its confirmation is neither, and without a presence around it
 * the swap pops in one frame while every neighbouring change on the same
 * card is animated.
 *
 * It lived inline in NotebookPanel until a second panel needed the same
 * gesture. Restating it there would have made the duration and the easing
 * two values that drift; it is one component now and both call sites read
 * the same seconds.
 *
 * `mode="wait"` is what makes it a replacement: the outgoing pane finishes
 * its exit before the incoming one mounts. `initial` is false for the first
 * presence, which keeps the FIRST mount still - the pane is already there
 * when the block opens, so it has nothing to arrive from, and fading it in
 * would double the Collapse that usually wraps this. Under reduced motion
 * both halves take no time, which is the plain instant swap the ternary
 * always was.
 *
 * The hazard VariantCarousel and VaultGate record in their own comments -
 * a popped pane lingering under StrictMode - is a problem of LISTS of panes.
 * There is no list here: exactly one keyed child at a time, `mode="wait"`,
 * and the tests wait for the incoming pane rather than assuming it, so a
 * pane that failed to leave is a red test instead of something on screen.
 *
 * THE EXITING PANE IS DEAD, and that is not decoration. `mode="wait"` keeps
 * the outgoing pane MOUNTED for the length of its exit, so for that tick the
 * old branch is still on screen, still focusable and still clickable - and
 * the branches this component swaps are confirm buttons. A press landing in
 * that tick ran the pane that was leaving: on the backfill strip, an armed
 * "Remove all 12" belonging to the run that just ended, pressed while the new
 * run's pane was arriving, sent the OLD job's undo. The fix is the language
 * `Collapse` puts on its own closed state - `inert` for focus, hit
 * testing and the accessibility tree in one attribute, `aria-hidden` for
 * browsers without it, and `pointer-events-none` for the paint that has not
 * caught up - so exactly one pane is live at any moment: the arriving one.
 *
 * Under reduced motion the exit takes no time, so there is no tick to land
 * in and nothing about this changes what a reader sees.
 *
 * A PANE THAT ARRIVES ALREADY SUPERSEDED STARTS THE SWAP OVER. When the
 * outgoing pane's exit finishes in the same render as a newer change of
 * `pane`, `AnimatePresence` mounts the pane that was waiting already marked
 * as leaving. motion never runs an exit for a child that was never present:
 * it plays the entry and then waits on that child for good. And when the
 * pane's key has left once before, `AnimatePresence` ignores its completion
 * as well, because what it recorded about that earlier exit is only cleared
 * on a render in which the key is present. Either way `mode="wait"` never
 * mounts the newest pane, and the swap stops on an inert pane nobody can
 * press. The backfill strip's undo reaches exactly that: the confirm swaps
 * back to its trigger while the request is out, and the answer can land in
 * the render that finishes the confirm's exit. So a pane that is leaving on
 * the render that mounts it renders nothing - it was never seen - and
 * restarts the presence under a new key, which mounts the current pane.
 *
 * The restarted pane FADES IN. The first mount of a swap is still because
 * the pane is already there when the block opens; a restart is not that:
 * the block is open and the reader has just watched the old pane leave, so
 * the new one arrives the way every pane in a swap arrives (KURAL 5.5).
 * Only a restarted presence plays the entry, so the first mount stays as it
 * was, and under reduced motion the entry takes no time.
 *
 * The restart runs in a LAYOUT effect. The render in which the superseded
 * pane draws nothing is committed, and a passive effect would run after the
 * browser painted it: one frame with no pane, and the block collapsing under
 * the reader. A layout effect's update is processed before paint, so that
 * frame never reaches the screen.
 *
 * Tests ask "is this block inside the swap" through `data-slot`, and "which
 * branch is showing" through `data-pane`, the same way the Base UI
 * primitives expose theirs.
 */
export function CrossFade({
  pane,
  children,
  className,
  as = "div",
}: CrossFadeProps) {
  // Moves only when a pane arrives already superseded; see `Pane`. The
  // first presence mounts its pane still, a restarted one fades it in.
  const [presence, setPresence] = useState(0);
  const restart = useCallback(() => setPresence((n) => n + 1), []);
  return (
    <AnimatePresence key={presence} mode="wait" initial={presence > 0}>
      <Pane
        key={pane}
        pane={pane}
        className={className}
        as={as}
        onSuperseded={restart}
      >
        {children}
      </Pane>
    </AnimatePresence>
  );
}

/**
 * One keyed pane. Separate from `CrossFade` because `useIsPresent` reads the
 * presence context `AnimatePresence` puts around its keyed child, so the hook
 * has to be called BELOW that boundary; called in `CrossFade` itself it would
 * answer for whatever presence wraps the whole swap instead.
 *
 * `onSuperseded` is how a pane that is already leaving on its first render
 * says so. It is read once, at mount: a pane that was present and then
 * leaves is the ordinary exit, and only the pane that was never present is
 * the case `CrossFade` restarts for.
 */
function Pane({
  pane,
  children,
  className,
  as,
  onSuperseded,
}: Required<Pick<CrossFadeProps, "pane" | "children" | "as">> &
  Pick<CrossFadeProps, "className"> & { onSuperseded: () => void }) {
  const reduced = useReducedMotion();
  const present = useIsPresent();
  const [supersededAtMount] = useState(() => !present);
  useLayoutEffect(() => {
    if (supersededAtMount) onSuperseded();
  }, [supersededAtMount, onSuperseded]);
  if (supersededAtMount) return null;
  const Tag = as === "span" ? m.span : m.div;
  return (
    <Tag
      data-slot="cross-fade"
      data-pane={pane}
      inert={!present ? true : undefined}
      aria-hidden={!present || undefined}
      className={cn(className, !present && "pointer-events-none")}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{
        duration: reduced ? 0 : durationSeconds.exit,
        ease: easeValues.exit,
      }}
    >
      {children}
    </Tag>
  );
}
