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
 * Deliberately enter-only - no AnimatePresence, by choice rather than by
 * constraint. An earlier version of this comment blamed StrictMode for old
 * panes lingering fully visible; measured on 16 September 2026 (motion
 * 12.40.0, React 19.2.6, headless Edge 153; production and development
 * builds, StrictMode on and off; single, rapid, swap-back and 50Hz burst
 * paging, with and without a streaming re-render storm; 240 scenario runs)
 * no exiting pane ever lingered. It stays enter-only because an instant swap
 * under a directional slide+fade reads just as "page turned", and a
 * crossfading exit would stack the old pane under the new one for 180ms
 * (motion's wait mode avoids that only by delaying the new pane).
 *
 * direction: +1 = right arrow pressed (new page enters from the right),
 * -1 = left. Captured at press time by the caller (arrow-mash safe).
 */
import { type ReactNode } from "react";
import { motion as m } from "motion/react";
import { durationSeconds, easeValues } from "@/lib/theme/tokens";
import { useReducedMotion } from "./ReducedMotion";

const EASE = easeValues.smooth;

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
        transition={{ duration: still ? 0 : durationSeconds.fast, ease: EASE }}
        style={{ width: "100%" }}
      >
        {children}
      </m.div>
    </div>
  );
}
