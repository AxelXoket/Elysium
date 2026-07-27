/**
 * springs.ts - the app's spring vocabulary, in ONE place.
 *
 * Written with `visualDuration` + `bounce` rather than stiffness/damping,
 * because that pair means what it says: `visualDuration` is when the movement
 * LOOKS finished (the settle happens after it), so a spring can be coordinated
 * with a plain CSS transition by matching the two numbers. Physics values
 * cannot be reasoned about that way - you tune them until they feel right and
 * then nobody dares touch them.
 *
 * THE CAVEAT THAT DECIDES WHICH TO USE, and it is easy to get wrong:
 * a spring configured with `visualDuration`/`bounce` IGNORES INHERITED
 * VELOCITY. For a discrete state change that is exactly what you want -
 * consistent every time. For anything continuing a gesture (drag release, a
 * thumb being thrown, an animation re-triggered mid-flight) it is wrong: the
 * movement visibly restarts from zero instead of carrying momentum, which is
 * the single most obvious tell of a cheap animation. Those cases need the
 * physics form, so both are here and the reason is next to each.
 *
 * Durations follow the bands the research converged on: micro-feedback around
 * 100-150ms, surfaces 150-250ms, large or infrequent moves 300-400ms. Nothing
 * in the app should exceed that without a deliberate reason - a 500ms entrance
 * is charming once and irritating by the tenth time.
 */

import type { Transition } from "motion/react";

/** Button press, toggle, checkbox - must feel like a direct response. */
export const SPRING_SNAP: Transition = {
  type: "spring",
  visualDuration: 0.12,
  bounce: 0,
};

/** Dropdowns, popovers, tooltips, dialogs: present quickly, settle slightly. */
export const SPRING_SURFACE: Transition = {
  type: "spring",
  visualDuration: 0.22,
  bounce: 0.15,
};

/** Settings sub-pages, drawers, anything large. Heavier, barely any overshoot
 *  - bounce at full-panel scale reads as toy-like rather than lively. */
export const SPRING_PANEL: Transition = {
  type: "spring",
  visualDuration: 0.35,
  bounce: 0.1,
};

/** A rare, deliberately cinematic moment (the vault lock). The one place a
 *  longer, springier move is earned, because it happens once per session. */
export const SPRING_CINEMATIC: Transition = {
  type: "spring",
  visualDuration: 0.5,
  bounce: 0.3,
};

/**
 * For movement that CONTINUES something the hand was doing.
 *
 * Physics form on purpose: this is the only one that inherits velocity, so an
 * interrupted or gesture-driven animation carries its momentum instead of
 * snapping back to zero and starting again.
 */
export const SPRING_GESTURE: Transition = {
  type: "spring",
  stiffness: 400,
  damping: 30,
  mass: 1,
};

/**
 * Per-item offset for a staggered entrance, capped by TOTAL time.
 *
 * A fixed per-item delay makes a long list feel slower purely for being long -
 * the last row of forty arrives two seconds late for no reason anyone can see.
 * Budgeting the whole sequence keeps the effect readable at any length.
 */
export function staggerStep(count: number, totalSeconds = 0.35): number {
  if (count <= 1) return 0;
  return Math.min(0.05, totalSeconds / count);
}
