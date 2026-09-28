/**
 * glassSurfaceCss.ts - measuring a colour against the REAL cascade.
 *
 * jsdom has no layout engine, and its CSS parser silently drops several
 * constructs Tailwind v4 actually emits (nested `@layer`, `@property`, the
 * generated utility rules themselves land as "Could not parse CSS
 * stylesheet" on stderr) - confirmed by hand before this module was written:
 * `.text-muted-foreground`'s own `color` rule never applies in jsdom, so
 * `getComputedStyle(el).color` comes back as the browser default black for a
 * class whose real, shipped rule is `color: var(--color-muted-foreground)`.
 *
 * What DOES survive, reliably and reproducibly, is CSS custom PROPERTY
 * inheritance: a value assigned to `--some-token` on an ancestor element is
 * visible via `getComputedStyle(el).getPropertyValue('--some-token')` on any
 * descendant, correctly preferring the nearest redefinition over an outer
 * one. That is the exact mechanism `.glass-right` uses to re-theme this
 * surface, so it is what these tests measure - not `.color`, which cannot be
 * trusted here for a class Tailwind generates rather than one written by
 * hand in index.css.
 *
 * One consequence worth being explicit about: `:root`'s own custom
 * properties do not reliably reach `getPropertyValue` in jsdom either (also
 * confirmed by hand - `--color-es-danger`, declared only at `:root`, reads
 * back empty here regardless of which class is on the element). Tests that
 * depend on a `:root`-only token therefore cannot use this technique and say
 * so at the point they fall back to something else.
 *
 * The stylesheet under test is not a hand-copied excerpt: it is index.css
 * run through the project's OWN Vite + Tailwind pipeline (the same one that
 * ships to the browser), so a real rename or a real colour change here is a
 * real red test, not a fixture that quietly stopped matching reality.
 */
import { createServer, type ViteDevServer } from "vite";
import path from "path";

import {
  AA_NORMAL,
  contrastRatio,
  parseHex,
  type Rgb,
} from "@/lib/appearance/contrast";
import { splitTopLevel } from "./compiledSheet";

let cached: Promise<string> | null = null;

/** Vite's dev transform of a CSS file is a JS module that hands the actual
 *  CSS to the browser as a string literal (`const __vite__css = "..."`) for
 *  hot-reload to inject. This pulls that literal back out, scanning for the
 *  matching unescaped quote rather than assuming no interior `"` - the real
 *  stylesheet has plenty (attribute selectors, font stacks). */
function extractCssLiteral(code: string): string {
  const marker = "const __vite__css = \"";
  const start = code.indexOf(marker);
  if (start < 0) {
    throw new Error(
      "index.css did not transform into the expected __vite__css literal - " +
        "has Vite's dev CSS injection format changed?",
    );
  }
  let i = start + marker.length;
  while (i < code.length) {
    if (code[i] === "\\") { i += 2; continue; }
    if (code[i] === "\"") break;
    i += 1;
  }
  return JSON.parse(code.slice(start + marker.length - 1, i + 1));
}

/**
 * Compiles index.css through the real dev pipeline and caches the result for
 * the life of the test process - a Vite dev server costs several hundred ms
 * to spin up, and every file that imports this helper needs the same output.
 */
export function loadGlassRightCss(): Promise<string> {
  if (!cached) {
    cached = (async () => {
      // helpers/ -> test -> src -> frontend (this file's own root).
      const root = path.resolve(__dirname, "..", "..", "..");
      let server: ViteDevServer | undefined;
      try {
        server = await createServer({
          root,
          server: { middlewareMode: true },
          logLevel: "error",
          configFile: path.resolve(root, "vite.config.ts"),
        });
        const result = await server.transformRequest("/src/index.css");
        if (!result) throw new Error("index.css did not transform");
        return extractCssLiteral(result.code);
      } finally {
        await server?.close();
      }
    })();
  }
  return cached;
}

/** Appends real CSS text as a stylesheet jsdom can match selectors against. */
export function injectCss(css: string): HTMLStyleElement {
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);
  return style;
}

/** A `.glass-right` island, attached to the document so the cascade (and
 *  jsdom's selector matching) can actually see it. Callers own detaching it. */
export function wrapInGlassRight(): HTMLDivElement {
  const wrap = document.createElement("div");
  wrap.className = "glass-right";
  document.body.appendChild(wrap);
  return wrap;
}

/** The value a custom property resolves to AT this element, following the
 *  real cascade - not a hand-copied hex the stylesheet could drift away
 *  from. Empty string means unset (see the module comment on `:root`). */
export function surfaceToken(el: Element, name: string): string {
  return getComputedStyle(el).getPropertyValue(name).trim();
}

/* ------------------------------------------------------------------ *
 * THE COMPOSITING MODEL, in one place.
 *
 * `.glass-right` paints two SEMI-TRANSPARENT gradient stops. Four test
 * files measured ink against that panel and they did not agree on what
 * "the panel" is: two composited the stops onto the app frame and honoured
 * the alpha, one dropped the fourth channel and treated the stops as
 * opaque, and the fourth carried its own un-rounded `flatten`. The opaque
 * reading is roughly 0.3 to 0.55 MORE generous, which is the direction that
 * lets a real regression through - and it is the reading that disagreed
 * with three of index.css's own four rationale comments, which already
 * quote composited figures (`:869-871` 8.39/7.26, `:1138-1146` 5.08/4.49,
 * `:921-922` 11.8 to 13.0). Aligning on compositing aligns the stylesheet
 * with its own majority rather than imposing a new convention on it.
 *
 * Alpha is honoured, the frame is PARSED from `--color-es-background`, and
 * every helper below is shared so the four files cannot drift apart again.
 * ------------------------------------------------------------------ */

/**
 * Refuses a value that still carries a `var()`.
 *
 * Both readers below pull the first three numbers out of whatever they are
 * handed. Handed `var(--es-veil-12, rgba(255, 255, 255, 0.12))` they answered
 * white with an alpha of 255 - the fourth comma-separated part of the whole
 * string - and a contrast ratio computed from that came out at 6639:1 against
 * a physical ceiling of 21, with every assertion in the file green because
 * 6640 is finite and is not 1. A measurement taken from an unresolved value is
 * not a weaker measurement, it is a different number entirely, so it stops
 * here. Resolve with `resolveColour()` first.
 */
function refuseUnresolved(raw: string, who: string): void {
  if (raw.includes("var(")) {
    throw new Error(
      `${who} was handed an unresolved value: "${raw}". ` +
        "Resolve it against the sheet first - a var() is not a colour.",
    );
  }
}

/** The first three channels of `#rgb`, `#rrggbb`, `rgb(...)`, `rgba(...)`, or
 *  a bare `r, g, b, a` list as captured out of a regex group. */
export function parseRgbColour(raw: string): Rgb {
  refuseUnresolved(raw, "parseRgbColour");
  const text = raw.trim();
  const hex = parseHex(text);
  if (hex) return hex;
  // The colour function has to be one this reader actually speaks. Without
  // this check the three numbers were pulled out of ANY `f(...)`, so
  // `oklch(1 0 0)` - white - came back as `rgb(1, 0, 0)`, near black, and
  // travelled on into a contrast ratio as a confident wrong number. A
  // syntax this cannot read has to say so rather than guess: an unreadable
  // colour is a red test, a fabricated one is a green test that measured
  // nothing.
  const fn = /^([a-zA-Z][a-zA-Z-]*)\s*\(/.exec(text);
  if (fn && !/^rgba?$/i.test(fn[1])) {
    throw new Error(
      `parseRgbColour reads hex and rgb()/rgba() only, and "${text}" is ${fn[1]}(); ` +
        "convert it in the sheet or teach this reader that syntax",
    );
  }
  const inner = /\(([^)]*)\)/.exec(text);
  const m = /(\d+)[,\s]+(\d+)[,\s]+(\d+)/.exec(inner ? inner[1] : text);
  if (!m) throw new Error(`parseRgbColour could not read "${text}"`);
  return { r: Number(m[1]), g: Number(m[2]), b: Number(m[3]) };
}

/**
 * The fourth channel, defaulting to opaque.
 *
 * The inside of the parentheses is taken FIRST. Splitting a whole `rgba(...)`
 * string on commas leaves the closing paren stuck to the alpha, and
 * `Number(" 0.12) ")` is NaN - which then travels silently through every
 * composite and every ratio, because NaN compares false everywhere and
 * `not.toBe(1)` accepts it. That is how an earlier version of one of these
 * files reported a passing harness gate sitting on top of four NaNs.
 */
export function alphaOf(raw: string): number {
  refuseUnresolved(raw, "alphaOf");
  const inner = /\(([^)]*)\)/.exec(raw);
  const parts = (inner ? inner[1] : raw).split(",").map((x) => x.trim());
  if (parts.length <= 3) return 1;
  const a = Number(parts[3]);
  if (!Number.isFinite(a)) throw new Error(`unreadable alpha in "${raw}"`);
  return a;
}

/** src over dst. Rounded, because the channels really are eight bits. */
export function over(src: Rgb, dst: Rgb, alpha: number): Rgb {
  const mix = (a: number, b: number) => Math.round(a * alpha + b * (1 - alpha));
  return { r: mix(src.r, dst.r), g: mix(src.g, dst.g), b: mix(src.b, dst.b) };
}

/** A colour written WITH its alpha, laid over a backdrop. A bare `rgba()` is
 *  not a colour anybody can measure: the paint lands AT that alpha. */
export function composite(colour: string, backdrop: Rgb): Rgb {
  return over(parseRgbColour(colour), backdrop, alphaOf(colour));
}

/**
 * The same, for a layer in `mix-blend-mode: screen`. Screen never darkens:
 * `255 - (255-a)(255-b)/255` per channel, and the layer's own alpha then
 * mixes that result back towards the backdrop. The sidebar paints its
 * right-edge sheen this way, and that is the layer which lifts the ground
 * exactly where the scrollbar sits, so a reader treating it as ordinary
 * paint reads that corner darker than it ships.
 */
export function screenOver(colour: string, backdrop: Rgb): Rgb {
  const src = parseRgbColour(colour);
  const blend = (a: number, b: number) => 255 - ((255 - a) * (255 - b)) / 255;
  const lit: Rgb = {
    r: blend(src.r, backdrop.r),
    g: blend(src.g, backdrop.g),
    b: blend(src.b, backdrop.b),
  };
  return over(lit, backdrop, alphaOf(colour));
}

/** A `--token: value;` as declared anywhere in the compiled text. */
function declaration(css: string, name: string): string {
  const m = new RegExp(name + ":\\s*([^;]+);").exec(css);
  if (!m) throw new Error(`the stylesheet does not declare ${name}`);
  return m[1].trim();
}

/** The opaque frame the panel is painted on. Load-bearing: both stops are
 *  translucent, so the frame reaches the eye through them. */
export function frameColour(css: string): Rgb {
  const raw = declaration(css, "--color-es-background");
  const hex = parseHex(raw);
  if (!hex) throw new Error(`the frame background is no longer a plain hex: ${raw}`);
  return hex;
}

/**
 * A colour as the browser would resolve it: a literal comes back as itself, a
 * `var(--token)` is looked up in the sheet and resolved again, so a chain of
 * tokens ends at paint.
 *
 * Needed because the gradient stops below are now tokens. Reading the literal
 * out of the rule used to work only while the value was written at the point
 * of use; a helper that insists on that spelling would have made tokenising
 * the panel impossible, which is the wrong way round - the helper follows the
 * sheet, the sheet does not hold still for the helper.
 */
export function resolveColour(css: string, value: string): string {
  let current = value.trim();
  for (let hop = 0; hop < 8; hop += 1) {
    const m = /^var\(\s*(--[-\w]+)\s*(?:,([\s\S]*))?\)$/.exec(current);
    if (!m) return current;
    const name = m[1];
    const fallback = m[2];
    try {
      current = declaration(css, name).trim();
    } catch (err) {
      if (fallback === undefined) throw err;
      current = fallback.trim();
    }
  }
  throw new Error(`${value} does not resolve to a colour in eight hops`);
}

/** The two gradient stops of `.glass-right`, resolved but NOT composited. */
function glassRightStopValues(css: string): [string, string] {
  const idx = css.indexOf(".glass-right {");
  if (idx < 0) throw new Error("the .glass-right rule moved or was renamed");
  const block = css.slice(idx, idx + 400);
  const m = /background:\s*linear-gradient\(([^;]*?)\)\s*;/.exec(block);
  if (!m) throw new Error("the panel gradient did not parse - was it reformatted?");
  // Split on the commas that separate the gradient's own arguments, so a
  // `var(--x, rgba(1, 2, 3, 1))` fallback cannot be torn in half.
  const parts: string[] = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < m[1].length; i += 1) {
    const ch = m[1][i];
    if (ch === "(") depth += 1;
    else if (ch === ")") depth -= 1;
    else if (ch === "," && depth === 0) {
      parts.push(m[1].slice(start, i));
      start = i + 1;
    }
  }
  parts.push(m[1].slice(start));
  const stops = parts.slice(1).map((p) => resolveColour(css, p));
  if (stops.length !== 2) {
    throw new Error(`the panel gradient has ${stops.length} stops, not two`);
  }
  return [stops[0], stops[1]];
}

/** The two gradient stops AS WRITTEN, alpha discarded. Not a surface anybody
 *  sees; kept only so the compositing step has something to be compared
 *  against (POSITIVE CONTROL C) and so the discriminating ink below can be
 *  derived from the gap between the two readings. */
export function rawGlassRightStops(css: string): [Rgb, Rgb] {
  const [top, bottom] = glassRightStopValues(css);
  return [parseRgbColour(top), parseRgbColour(bottom)];
}

/** The panel as it actually paints: both stops composited onto the frame. */
export function glassRightStops(css: string): [Rgb, Rgb] {
  const [top, bottom] = glassRightStopValues(css);
  const f = frameColour(css);
  return [
    over(parseRgbColour(top), f, alphaOf(top)),
    over(parseRgbColour(bottom), f, alphaOf(bottom)),
  ];
}

/**
 * A grey that the two models DISAGREE about: it clears AA against the raw
 * stop and fails AA against the composited one. That disagreement is the
 * whole difference between the models, so an assertion on this ink goes red
 * the moment compositing stops happening - whether because the `over()` call
 * was removed, or because the frame token was lightened until the two stops
 * converged.
 *
 * Derived, never typed. Both stops are parsed out of the stylesheet and the
 * threshold is imported, so a hex written here would be exactly the kind of
 * copied constant that keeps a test green after the thing it measures moves.
 * The MIDDLE of the qualifying band is taken rather than an edge, so the
 * assertion carries margin on both sides instead of sitting on the line.
 *
 * When the band is EMPTY the two models have collapsed into one - which is
 * the failure this exists to catch - so the fallback is the lightest grey
 * that still clears AA on the raw stop: it then reads at or above AA against
 * the "composited" stop as well, and the caller's assertion fails loudly
 * instead of the helper throwing something unreadable.
 */
export function discriminatingInk(rawStop: Rgb, compositedStop: Rgb): Rgb {
  const grey = (v: number): Rgb => ({ r: v, g: v, b: v });
  const band: number[] = [];
  let lightestOnRaw: number | null = null;
  for (let v = 0; v <= 255; v += 1) {
    if (contrastRatio(grey(v), rawStop) < AA_NORMAL) continue;
    lightestOnRaw = v;
    if (contrastRatio(grey(v), compositedStop) < AA_NORMAL) band.push(v);
  }
  if (lightestOnRaw === null) {
    throw new Error(
      "no grey clears AA against the raw stop - is the panel still near-white?",
    );
  }
  if (band.length === 0) return grey(lightestOnRaw);
  return grey(band[Math.floor(band.length / 2)]);
}

/* ------------------------------------------------------------------
 * Gradient geometry: what a layer paints AT A PLACE, not what it contains.
 *
 * `paintedStops` answers "which colours are in this background", which is
 * the right question for "what colour is this box" and the wrong one for
 * "what is under the scrollbar". A bar is a narrow band down one edge of
 * its surface. A radial highlight centred at 12% of the width never reaches
 * that band however light its own peak is, and a bottom-anchored shadow
 * that fades out at 46% never reaches the top of it. A reader that offers
 * every stop of every layer at once stacks paint the engine cannot put in
 * one place, and the number it reports is two errors cancelling rather than
 * a measurement.
 *
 * So these read a layer the way the engine draws it: the gradient parameter
 * at a point, the colour interpolated between the stops bracketing it (in
 * premultiplied alpha, which is what the spec says), and over a REGION the
 * exact extremes of that parameter - at the region's corners for a linear
 * gradient, at the nearest and farthest points of the region from the
 * centre for a radial one. Both are monotonic in the parameter, so the
 * endpoints plus the stop positions between them are the whole answer and
 * nothing is sampled or guessed.
 *
 * A syntax this cannot read throws, for the reason `parseRgbColour` throws:
 * a guessed geometry is a green test that measured nothing.
 * ------------------------------------------------------------------ */

/** A box in CSS pixels. Aspect is load-bearing: a corner gradient's angle
 *  and a non-axis-aligned one's projection both depend on it. */
export interface Box {
  w: number;
  h: number;
}

/** A sub-rectangle of a box, in fractions of it, y down. */
export interface Region {
  x0: number;
  x1: number;
  y0: number;
  y1: number;
}

/** One stop: a colour and where it sits, as a fraction of the gradient line
 *  (linear) or of the radius (radial). */
export interface GradientStop {
  colour: string;
  pos: number;
}

type Corner = "to top right" | "to top left" | "to bottom right" | "to bottom left";

/** Where a layer's paint goes, as the sheet writes it. */
export type LayerShape =
  | { kind: "solid" }
  | { kind: "linear"; angleDeg: number; corner?: undefined }
  | { kind: "linear"; corner: Corner; angleDeg?: undefined }
  | { kind: "radial"; cx: number; cy: number; rx: number; ry: number };

export interface ParsedLayer {
  shape: LayerShape;
  stops: GradientStop[];
}

const TRANSPARENT = "rgba(0, 0, 0, 0)";

/** A colour with its alpha, `transparent` included - which `alphaOf` reads
 *  as opaque, because it has no parentheses to look inside. */
function paintOf(colour: string): { rgb: Rgb; a: number } {
  const text = colour.trim();
  if (/^transparent$/i.test(text)) return { rgb: { r: 0, g: 0, b: 0 }, a: 0 };
  return { rgb: parseRgbColour(text), a: alphaOf(text) };
}

/** The alpha a stop paints at, `transparent` included. */
export function paintAlpha(colour: string): number {
  return paintOf(colour).a;
}

/** Two stops mixed in PREMULTIPLIED alpha, which is what CSS interpolates
 *  in: mixing a translucent blue towards `transparent` un-premultiplied
 *  would drag the hue to black as the alpha fell. Channels come back whole,
 *  because the frame buffer really is eight bits and every other reader
 *  here parses integers. */
function mixStops(from: string, to: string, f: number): string {
  const a = paintOf(from);
  const b = paintOf(to);
  const alpha = a.a + (b.a - a.a) * f;
  if (alpha <= 0) return TRANSPARENT;
  const channel = (x: number, y: number) => {
    const pa = x * a.a;
    const pb = y * b.a;
    return Math.round((pa + (pb - pa) * f) / alpha);
  };
  return (
    "rgba(" +
    `${channel(a.rgb.r, b.rgb.r)}, ` +
    `${channel(a.rgb.g, b.rgb.g)}, ` +
    `${channel(a.rgb.b, b.rgb.b)}, ` +
    `${Number(alpha.toFixed(4))})`
  );
}

/** A stop's colour and its trailing position, if it was written with one.
 *  A two-position stop is a shape this does not speak. */
function splitStop(part: string): { colour: string; positions: number[] } {
  let rest = part.trim();
  const positions: number[] = [];
  for (;;) {
    const m = /\s(-?[\d.]+)%$/.exec(rest);
    if (!m) break;
    positions.unshift(Number(m[1]) / 100);
    rest = rest.slice(0, m.index);
  }
  return { colour: rest.trim(), positions };
}

/**
 * Stops with every implicit position filled in the way the spec fills them:
 * a missing first is 0, a missing last is 1, a run of missing ones is spread
 * evenly between the known positions bracketing it, and a position is never
 * allowed below the one before it.
 */
export function gradientStops(parts: string[]): GradientStop[] {
  if (parts.length < 2) {
    throw new Error(`a gradient needs at least two stops, got ${parts.length}`);
  }
  const colours: string[] = [];
  const known: Array<number | null> = [];
  for (const part of parts) {
    const { colour, positions } = splitStop(part);
    if (positions.length > 1) {
      throw new Error(
        `a stop written with a position RANGE ("${part.trim()}") is a shape this reader does not speak`,
      );
    }
    if (colour === "") throw new Error(`a stop with no colour: "${part}"`);
    colours.push(colour);
    known.push(positions.length === 1 ? positions[0] : null);
  }
  if (known[0] === null) known[0] = 0;
  if (known[known.length - 1] === null) known[known.length - 1] = 1;
  for (let i = 0; i < known.length; i += 1) {
    if (known[i] !== null) continue;
    let j = i;
    while (known[j] === null) j += 1;
    const before = known[i - 1] as number;
    const after = known[j] as number;
    const step = (after - before) / (j - (i - 1));
    for (let k = i; k < j; k += 1) known[k] = before + step * (k - (i - 1));
    i = j - 1;
  }
  const out: GradientStop[] = [];
  let floor = Number.NEGATIVE_INFINITY;
  for (let i = 0; i < colours.length; i += 1) {
    const pos = Math.max(known[i] as number, floor);
    floor = pos;
    out.push({ colour: colours[i], pos });
  }
  return out;
}

/** One background layer, read into a geometry plus its stops. */
export function parseLayer(layer: string): ParsedLayer {
  const text = layer.trim();
  const fn = /^([a-zA-Z-]+)\s*\(([\s\S]*)\)$/.exec(text);
  if (!fn || /^rgba?$/i.test(fn[1])) {
    // A bare colour fills its box, so every point of it is the same paint.
    return { shape: { kind: "solid" }, stops: [{ colour: text, pos: 0 }] };
  }
  const name = fn[1].toLowerCase();
  const parts = splitTopLevel(fn[2], ",").map((p) => p.trim());
  if (name === "linear-gradient") {
    const head = parts[0].toLowerCase();
    const deg = /^(-?[\d.]+)deg$/.exec(head);
    if (deg) {
      return {
        shape: { kind: "linear", angleDeg: Number(deg[1]) },
        stops: gradientStops(parts.slice(1)),
      };
    }
    const SIDES: Record<string, number> = {
      "to top": 0,
      "to right": 90,
      "to bottom": 180,
      "to left": 270,
    };
    if (head in SIDES) {
      return {
        shape: { kind: "linear", angleDeg: SIDES[head] },
        stops: gradientStops(parts.slice(1)),
      };
    }
    const corner = /^to (top|bottom) (left|right)$/.exec(head);
    if (corner) {
      return {
        shape: { kind: "linear", corner: `to ${corner[1]} ${corner[2]}` as Corner },
        stops: gradientStops(parts.slice(1)),
      };
    }
    // A missing direction means `to bottom`, but guessing which of the two
    // an unreadable first argument is would be the mistake this file exists
    // to refuse: it is written out or it throws.
    throw new Error(
      `linear-gradient direction "${parts[0]}" is a shape this reader does not speak`,
    );
  }
  if (name === "radial-gradient") {
    const head = parts[0].trim();
    const ellipse =
      /^ellipse\s+([\d.]+)%\s+([\d.]+)%\s+at\s+(-?[\d.]+)%\s+(-?[\d.]+)%$/.exec(head);
    if (!ellipse) {
      throw new Error(
        `radial-gradient shape "${head}" is one this reader does not speak; ` +
          "write it with an explicit ellipse size and centre, or teach this reader the form",
      );
    }
    return {
      shape: {
        kind: "radial",
        rx: Number(ellipse[1]) / 100,
        ry: Number(ellipse[2]) / 100,
        cx: Number(ellipse[3]) / 100,
        cy: Number(ellipse[4]) / 100,
      },
      stops: gradientStops(parts.slice(1)),
    };
  }
  throw new Error(`"${name}()" is not a background layer this reader can place`);
}

/** The angle a corner keyword means on a box of this aspect. CSS Images 3
 *  states the magic-corner rule as: the gradient line must be angled such
 *  that it points into the same quadrant as the specified corner, and is
 *  perpendicular to a line intersecting the two neighbouring corners of the
 *  gradient box. For `to top right` those neighbours are the top-left and
 *  the bottom-right; the line joining them runs (w, h) down the box, so the
 *  gradient line runs along (h, -w) and its angle off `to top` is
 *  atan2(h, w). On a square that is 45 degrees whichever way the ratio is
 *  written, which is why the aspect has to be read on a box that is not
 *  one. */
function cornerAngle(corner: Corner, box: Box): number {
  const base = (Math.atan2(box.h, box.w) * 180) / Math.PI;
  if (corner === "to top right") return base;
  if (corner === "to bottom right") return 180 - base;
  if (corner === "to bottom left") return 180 + base;
  return 360 - base;
}

/** The gradient parameter at one point of a box: 0 at the first stop's end
 *  of the line, 1 at the last's, and for a radial one the distance from the
 *  centre in radius units. */
export function layerParamAt(shape: LayerShape, box: Box, x: number, y: number): number {
  if (shape.kind === "solid") return 0;
  if (shape.kind === "radial") {
    return Math.hypot((x - shape.cx) / shape.rx, (y - shape.cy) / shape.ry);
  }
  const angle = shape.corner ? cornerAngle(shape.corner, box) : (shape.angleDeg as number);
  const rad = (angle * Math.PI) / 180;
  const sin = Math.sin(rad);
  const cos = Math.cos(rad);
  const len = Math.abs(box.w * sin) + Math.abs(box.h * cos);
  const px = x * box.w - box.w / 2;
  const py = y * box.h - box.h / 2;
  return 0.5 + (px * sin - py * cos) / len;
}

/** The exact range of the parameter over a region. Both geometries are
 *  monotonic, so this is a closed form and not a sample: a linear gradient
 *  is affine in the point, so its extremes sit at the region's corners; a
 *  radial one is a distance, so its extremes sit at the nearest and the
 *  farthest point of the region from the centre. */
export function layerParamRange(
  shape: LayerShape,
  box: Box,
  region: Region,
): [number, number] {
  if (shape.kind === "solid") return [0, 0];
  if (shape.kind === "radial") {
    const u0 = (region.x0 - shape.cx) / shape.rx;
    const u1 = (region.x1 - shape.cx) / shape.rx;
    const v0 = (region.y0 - shape.cy) / shape.ry;
    const v1 = (region.y1 - shape.cy) / shape.ry;
    const near = (lo: number, hi: number) => (lo > 0 ? lo : hi < 0 ? -hi : 0);
    const far = (lo: number, hi: number) => Math.max(Math.abs(lo), Math.abs(hi));
    return [
      Math.hypot(near(u0, u1), near(v0, v1)),
      Math.hypot(far(u0, u1), far(v0, v1)),
    ];
  }
  const values = [
    layerParamAt(shape, box, region.x0, region.y0),
    layerParamAt(shape, box, region.x1, region.y0),
    layerParamAt(shape, box, region.x0, region.y1),
    layerParamAt(shape, box, region.x1, region.y1),
  ];
  return [Math.min(...values), Math.max(...values)];
}

/** The colour a gradient paints at one parameter value, clamped to the end
 *  stops the way the engine clamps. */
export function colourAtParam(stops: GradientStop[], t: number): string {
  if (t <= stops[0].pos) return stops[0].colour;
  const last = stops[stops.length - 1];
  if (t >= last.pos) return last.colour;
  for (let i = 1; i < stops.length; i += 1) {
    if (t > stops[i].pos) continue;
    const a = stops[i - 1];
    const b = stops[i];
    const span = b.pos - a.pos;
    return mixStops(a.colour, b.colour, span === 0 ? 1 : (t - a.pos) / span);
  }
  return last.colour;
}

/**
 * What one background layer paints over one region: the distinct colours it
 * takes there, and whether it leaves any of that region unpainted.
 *
 * Absence is measured, not assumed. A layer whose alpha reaches zero
 * somewhere in the region really is absent somewhere along the bar, so what
 * is under it shows through there. A layer whose alpha never reaches zero
 * covers the whole region, and offering it as absent would measure the thumb
 * against a ground that is never on screen.
 */
export function layerPaintOver(
  layer: string,
  box: Box,
  region: Region,
): { stops: string[]; canBeAbsent: boolean } {
  const { shape, stops } = parseLayer(layer);
  const [lo, hi] = layerParamRange(shape, box, region);
  const samples = new Set<number>([lo, hi]);
  for (const stop of stops) {
    if (stop.pos > lo && stop.pos < hi) samples.add(stop.pos);
  }
  const out: string[] = [];
  let canBeAbsent = false;
  for (const t of [...samples].sort((a, b) => a - b)) {
    const colour = colourAtParam(stops, t);
    if (paintAlpha(colour) === 0) {
      canBeAbsent = true;
      continue;
    }
    if (!out.includes(colour)) out.push(colour);
  }
  return { stops: out, canBeAbsent };
}

/* ------------------------------------------------------------------
 * Words over a stack of painted elements, the way the engine draws them.
 *
 * KURAL 2.4b's ground is the panel's two gradient stops over the frame, and
 * its step 3 measures the text colour with its opacity applied. Between the
 * panel and a line of words there can be more than nothing: a card, a
 * tinted notice, a button's fill, and any of them, or the line itself, can
 * be translucent. The engine paints each element's background, then its
 * content, into a group, and lays the whole group over what is below at
 * that element's opacity. That is premultiplied arithmetic, and it is done
 * here in full, so an `opacity-70` span, a 50% disabled button and a card
 * at 34% white all reach the ratio the way they reach the screen.
 * ------------------------------------------------------------------ */

/** A colour's channels and its alpha: hex, `rgb()`, `rgba()`, `transparent`,
 *  or a colour mixed with `transparent`, which is what Tailwind's opacity
 *  modifier compiles to (`color-mix(in oklab, <colour> 85%, transparent)`).
 *  Mixed with transparent the colour keeps its channels whatever the space,
 *  and its alpha is scaled by the percentage. Any other mix throws. */
export function paintChannels(value: string): { rgb: Rgb; a: number } {
  const text = value.trim();
  if (/^transparent$/i.test(text)) return { rgb: { r: 0, g: 0, b: 0 }, a: 0 };
  const mix = /^color-mix\((.*)\)$/i.exec(text);
  if (!mix) return { rgb: parseRgbColour(text), a: alphaOf(text) };
  const parts = splitTopLevel(mix[1], ",").map((p) => p.trim());
  if (parts.length !== 3 || !/^in\s+[a-z-]+$/i.test(parts[0])) {
    throw new Error(`"${text}" is a colour-mix this reader cannot read`);
  }
  const side = (part: string): { colour: string; pct: number | null } => {
    const m = /^(.*?)(?:\s+(\d*\.?\d+)%)?$/.exec(part)!;
    return { colour: m[1].trim(), pct: m[2] === undefined ? null : Number(m[2]) };
  };
  const [one, two] = [side(parts[1]), side(parts[2])];
  const clear = (c: string) => /^transparent$/i.test(c);
  if (clear(one.colour) === clear(two.colour)) {
    throw new Error(`"${text}" does not mix one colour with transparent, which is the one mix read here`);
  }
  const [paint, other] = clear(two.colour) ? [one, two] : [two, one];
  const share = paint.pct ?? (other.pct === null ? 50 : 100 - other.pct);
  const base = paintChannels(paint.colour);
  return { rgb: base.rgb, a: base.a * (share / 100) };
}

type Premultiplied = { r: number; g: number; b: number; a: number };

const premultiply = ({ rgb, a }: { rgb: Rgb; a: number }): Premultiplied =>
  ({ r: rgb.r * a, g: rgb.g * a, b: rgb.b * a, a });
const overPremultiplied = (src: Premultiplied, dst: Premultiplied): Premultiplied => ({
  r: src.r + dst.r * (1 - src.a),
  g: src.g + dst.g * (1 - src.a),
  b: src.b + dst.b * (1 - src.a),
  a: src.a + dst.a * (1 - src.a),
});
const faded = (p: Premultiplied, opacity: number): Premultiplied =>
  ({ r: p.r * opacity, g: p.g * opacity, b: p.b * opacity, a: p.a * opacity });

/** One element of the stack: its own opacity and the one paint it lays
 *  under the words, null when it paints nothing. Outermost first. */
export interface StackLayer {
  opacity: number;
  paint: string | null;
}

/** The pixel under a line of words in `ink`, with the words and without,
 *  over `steps` over an opaque `base`: each element's paint and then its
 *  content in one group, the group laid down at the element's opacity, the
 *  words painted last inside the innermost. Rounded to eight bits, which is
 *  what the frame buffer holds. */
export function wordsOverStack(
  ink: string,
  steps: readonly StackLayer[],
  base: Rgb,
): { words: Rgb; ground: Rgb } {
  let words = premultiply(paintChannels(ink));
  let ground: Premultiplied = { r: 0, g: 0, b: 0, a: 0 };
  for (let i = steps.length - 1; i >= 0; i -= 1) {
    const { opacity, paint } = steps[i];
    const under = paint === null ? { r: 0, g: 0, b: 0, a: 0 } : premultiply(paintChannels(paint));
    words = faded(overPremultiplied(words, under), opacity);
    ground = faded(overPremultiplied(ground, under), opacity);
  }
  const floor = premultiply({ rgb: base, a: 1 });
  const flat = (p: Premultiplied): Rgb => {
    const out = overPremultiplied(p, floor);
    return { r: Math.round(out.r), g: Math.round(out.g), b: Math.round(out.b) };
  };
  return { words: flat(words), ground: flat(ground) };
}

/** Every combination is tried; past this many the stack is not one a line
 *  of words sits in, and the reading stops rather than sampling. */
const MAX_GROUND_COMBINATIONS = 4096;

/**
 * The lowest ratio a line in `ink` reaches over a stack whose elements can
 * each lay down any one of several paints (a gradient's stops), over each
 * of `bases`. Returns the combination that produced it, so a failure can say
 * what it was measured on.
 */
export function worstRatioOverStack(
  ink: string,
  steps: readonly { opacity: number; paints: readonly string[] }[],
  bases: readonly Rgb[],
): { ratio: number; words: Rgb; ground: Rgb } {
  const choices = steps.map((s) => (s.paints.length > 0 ? [...s.paints] : [null]));
  const combinations = choices.reduce((n, c) => n * c.length, bases.length);
  if (combinations > MAX_GROUND_COMBINATIONS) {
    throw new Error(`${combinations} grounds under one line: a stack this deep is not read`);
  }
  const worst = { ratio: Infinity, words: { r: 0, g: 0, b: 0 }, ground: { r: 0, g: 0, b: 0 } };
  const at = choices.map(() => 0);
  for (let n = 0; n < combinations / bases.length; n += 1) {
    const picked = choices.map((c, i) => ({ opacity: steps[i].opacity, paint: c[at[i]] }));
    for (const base of bases) {
      const { words, ground } = wordsOverStack(ink, picked, base);
      const ratio = contrastRatio(words, ground);
      if (ratio < worst.ratio) Object.assign(worst, { ratio, words, ground });
    }
    for (let i = at.length - 1; i >= 0; i -= 1) {
      at[i] += 1;
      if (at[i] < choices[i].length) break;
      at[i] = 0;
    }
  }
  if (!Number.isFinite(worst.ratio)) throw new Error("no ground was given to read the line on");
  return worst;
}
