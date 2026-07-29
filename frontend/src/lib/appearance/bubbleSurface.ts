/**
 * bubbleSurface.ts - how solid a message bubble's fill is.
 *
 * WHY THIS IS NOT `opacity`
 *   The obvious implementation - `opacity` on the bubble - fades the words
 *   with the surface, because opacity applies to an element and everything
 *   inside it. Past about 0.6 the text is unreadable, so the useful half of
 *   the range would not exist. `color-mix` thins the FILL and nothing else,
 *   which is why the `color` declaration next to it is left alone: the ink
 *   stays at full strength however transparent the bubble gets.
 *
 * WHY IT TAKES A CSS STRING RATHER THAN A COLOUR
 *   The bubble's fill is `var(--msg-user-bg, var(--color-es-user-bubble))` -
 *   a preset variable layered over a theme variable, resolved by the browser
 *   at paint time and different under every contrast preset, the custom-ink
 *   override and the dark-wallpaper chrome. Reading it back in JS to mix it
 *   ourselves would mean reimplementing that cascade and then keeping the
 *   copy in step forever. `color-mix` composes with the variable instead, so
 *   this knows nothing about which colour it is thinning.
 */

/**
 * The `background-color` for a bubble whose fill is `base`, at `alpha`.
 *
 * Returns `base` UNCHANGED at full opacity. That is the zero-change contract
 * and it is exact rather than approximate: everyone who never touches this
 * setting keeps the declaration they already had, so nothing about the
 * default look depends on a colour function behaving identically to a plain
 * variable reference.
 */
export function bubbleSurface(base: string, alpha: number): string {
  if (!Number.isFinite(alpha) || alpha >= 1) return base;
  const pct = Math.max(0, Math.min(100, Math.round(alpha * 100)));
  // srgb, not oklab: this is a fade to nothing rather than a blend between
  // two colours, so the perceptual space buys nothing and srgb is what the
  // rest of the bubble stack already works in.
  return `color-mix(in srgb, ${base} ${pct}%, transparent)`;
}
