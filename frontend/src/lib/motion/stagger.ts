/**
 * stagger.ts - the staggered-entrance budget.
 *
 * This file was `springs.ts` and exported five spring presets alongside this
 * function. They were deleted on 2026-08-09, and the reason is worth keeping
 * because the same idea will look attractive again.
 *
 * The presets were written as a vocabulary first and were never fitted to
 * anything: for the whole life of the file, not one component imported one of
 * them. The app animates with plain duration/ease tweens by choice, and has
 * exactly one hand-written spring - the vault lock's shackle snap, tuned
 * against a fixed 2000ms choreography and a commit at 820ms.
 *
 * The preset whose own comment named that lock as its use case was simulated
 * against it twice, independently, on motion 12.40: roughly twice as long to
 * settle and about a third of the overshoot. The two runs disagreed on the
 * exact milliseconds, so no number is quoted here; the direction and the
 * magnitude were the same both times and that is the part that decided it.
 * Adopting the preset would have changed what the user sees AND moved a timer
 * that a documented double-lock race was once traced to.
 *
 * The tests guarding the presets went with them. They asserted that the
 * constants were internally consistent, which was true and meant nothing:
 * nothing rendered them. A test that guards an unused constant reports green
 * about a thing that cannot fail.
 *
 * `staggerStep` stayed because `AnimatedList` actually calls it.
 */

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
