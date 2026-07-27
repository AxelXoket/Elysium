/**
 * contrast.ts - the arithmetic behind letting somebody choose their own ink.
 *
 * The message-contrast presets each ship a MEASURED ratio (soft 6.5:1, default
 * 9.2:1, high 15.2:1 - all at or above AA, and deliberately ordered). Handing
 * over a free colour picker without this module would quietly throw that away:
 * the first pastel anybody likes the look of lands somewhere around 2:1 and the
 * app silently becomes unreadable, having been careful about exactly that for
 * three versions.
 *
 * So the picker is allowed, and the RATIO IS SHOWN. Not blocked - somebody
 * choosing a low-contrast look on their own screen for their own eyes is
 * entitled to; but they choose it knowing, instead of discovering it at
 * midnight on a long message.
 *
 * WCAG 2.x relative luminance, which is what the presets were measured with.
 * The formula is worth writing out rather than reaching for a library: it is
 * twenty lines, it never changes, and the dependency would be shipped in a
 * desktop app to compute six numbers.
 */

export interface Rgb {
  r: number;
  g: number;
  b: number;
}

/** WCAG thresholds for normal-size body text. */
export const AA_NORMAL = 4.5;
export const AAA_NORMAL = 7;

export function parseHex(hex: string): Rgb | null {
  const value = hex.trim().replace(/^#/, "");
  const full =
    value.length === 3
      ? value
          .split("")
          .map((c) => c + c)
          .join("")
      : value;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) return null;
  return {
    r: parseInt(full.slice(0, 2), 16),
    g: parseInt(full.slice(2, 4), 16),
    b: parseInt(full.slice(4, 6), 16),
  };
}

export function toHex({ r, g, b }: Rgb): string {
  const part = (n: number) =>
    Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, "0");
  return `#${part(r)}${part(g)}${part(b)}`;
}

/** WCAG relative luminance. */
export function luminance({ r, g, b }: Rgb): number {
  const channel = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/** Contrast ratio, 1..21. Order of arguments does not matter. */
export function contrastRatio(a: Rgb, b: Rgb): number {
  const la = luminance(a);
  const lb = luminance(b);
  const [hi, lo] = la > lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

export type ContrastVerdict = "aaa" | "aa" | "low";

export function verdict(ratio: number): ContrastVerdict {
  if (ratio >= AAA_NORMAL) return "aaa";
  if (ratio >= AA_NORMAL) return "aa";
  return "low";
}

/**
 * Nudge a colour until it clears a target ratio against `against`.
 *
 * Offered as a one-click repair beside the warning rather than as an automatic
 * correction: silently changing the colour somebody just picked is worse than
 * telling them it is hard to read. It walks towards black or white - whichever
 * the background is further from - so the result still resembles the choice.
 */
export function nudgeToRatio(
  colour: Rgb,
  against: Rgb,
  target = AA_NORMAL,
): Rgb {
  const towardsWhite = luminance(against) < 0.5;
  let best = colour;
  for (let step = 0; step <= 100; step += 1) {
    if (contrastRatio(best, against) >= target) return best;
    const mix = step / 100;
    const edge = towardsWhite ? 255 : 0;
    best = {
      r: colour.r + (edge - colour.r) * mix,
      g: colour.g + (edge - colour.g) * mix,
      b: colour.b + (edge - colour.b) * mix,
    };
  }
  return best;
}

/**
 * Hue/saturation/lightness -> RGB, for the circular picker.
 *
 * A colour WHEEL needs polar coordinates, and hue-around / saturation-outward
 * is the mapping people already know from every other picker they have used.
 */
export function hslToRgb(h: number, s: number, l: number): Rgb {
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  const [r, g, b] =
    h < 60 ? [c, x, 0]
    : h < 120 ? [x, c, 0]
    : h < 180 ? [0, c, x]
    : h < 240 ? [0, x, c]
    : h < 300 ? [x, 0, c]
    : [c, 0, x];
  return { r: (r + m) * 255, g: (g + m) * 255, b: (b + m) * 255 };
}

/**
 * The inverse of hslToRgb, for SEEDING the picker from a saved colour.
 *
 * Deliberately not used to keep the wheel in sync continuously - that round
 * trip is lossy, and at very low or very high lightness many (h,s) pairs
 * collapse to the same colour, which would make the marker jump somewhere the
 * person never clicked. It is used once, at mount: without it the picker
 * started at a hard-coded {h:210, s:0.35} and the Lightness slider repainted a
 * saved red as a blue-grey, from a control labelled "Lightness".
 */
export function rgbToHsl({ r, g, b }: Rgb): { h: number; s: number; l: number } {
  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const l = (max + min) / 2;
  const d = max - min;
  if (d === 0) return { h: 0, s: 0, l };
  const s = d / (1 - Math.abs(2 * l - 1));
  let h: number;
  if (max === rn) h = 60 * (((gn - bn) / d) % 6);
  else if (max === gn) h = 60 * ((bn - rn) / d + 2);
  else h = 60 * ((rn - gn) / d + 4);
  if (h < 0) h += 360;
  return { h, s, l };
}

/** Screen position on the wheel -> hue/saturation. Radius is clamped, not
 *  rejected: dragging past the rim should ride the edge, not stop dead. */
export function wheelToHs(
  dx: number,
  dy: number,
  radius: number,
): { h: number; s: number } {
  const angle = (Math.atan2(dy, dx) * 180) / Math.PI;
  const h = (angle + 360) % 360;
  const s = Math.min(1, Math.hypot(dx, dy) / radius);
  return { h, s };
}
