/**
 * VariantCarousel - in-place horizontal page-flip between message variants.
 *
 * House motion primitive (sibling of FadeIn/SlideIn/Collapse). The viewport
 * clips; changing paneKey remounts the single pane, which slides in from the
 * pressed direction. The bubble height snaps to the new content (no layout
 * morph: motion's `layout` animates via scale projection, which visibly
 * distorts plain text children mid-tween and re-measures on every rAF while
 * a variant streams - a clean snap under the slide reads better and costs
 * nothing).
 *
 * Deliberately enter-only - no AnimatePresence. Exit animations of popped
 * panes proved unreliable under StrictMode (the old pane could linger
 * fully visible inside the bubble); an instant swap under a directional
 * slide+fade reads just as "page turned" and can never leak stale panes.
 *
 * direction: +1 = right arrow pressed (new page enters from the right),
 * -1 = left. Captured at press time by the caller (arrow-mash safe).
 */
import { type ReactNode } from "react";
import { motion as m } from "motion/react";
import { useReducedMotion } from "./ReducedMotion";

const EASE = [0.4, 0, 0.2, 1] as const;

interface VariantCarouselProps {
  /** Key of the displayed pane - changing it drives the flip. */
  paneKey: string;
  /** +1 right / -1 left, captured when the arrow was pressed. */
  direction: 1 | -1;
  /** Enable the enter slide. Callers pass false until the user has actually
   * navigated, so panes never slide on plain mounts (chat open/refetch). */
  animateEnter: boolean;
  children: ReactNode;
}

export function VariantCarousel({
  paneKey,
  direction,
  animateEnter,
  children,
}: VariantCarouselProps) {
  const reduced = useReducedMotion();
  const still = reduced || !animateEnter;
  return (
    <div style={{ overflow: "hidden", position: "relative" }}>
      <m.div
        key={paneKey}
        initial={still ? false : { x: direction * 24, opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        transition={{ duration: still ? 0 : 0.18, ease: EASE }}
        style={{ width: "100%" }}
      >
        {children}
      </m.div>
    </div>
  );
}
