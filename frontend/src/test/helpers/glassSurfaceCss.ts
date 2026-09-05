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

/** The first three channels of `#rgb`, `#rrggbb`, `rgb(...)`, `rgba(...)`, or
 *  a bare `r, g, b, a` list as captured out of a regex group. */
export function parseRgbColour(raw: string): Rgb {
  const hex = parseHex(raw.trim());
  if (hex) return hex;
  const inner = /\(([^)]*)\)/.exec(raw);
  const m = /(\d+)[,\s]+(\d+)[,\s]+(\d+)/.exec(inner ? inner[1] : raw);
  if (!m) throw new Error(`parseRgbColour could not read "${raw}"`);
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

/** The two gradient stops AS WRITTEN, alpha discarded. Not a surface anybody
 *  sees; kept only so the compositing step has something to be compared
 *  against (POSITIVE CONTROL C) and so the discriminating ink below can be
 *  derived from the gap between the two readings. */
export function rawGlassRightStops(css: string): [Rgb, Rgb] {
  const idx = css.indexOf(".glass-right {");
  if (idx < 0) throw new Error("the .glass-right rule moved or was renamed");
  const block = css.slice(idx, idx + 400);
  const m =
    /background:\s*linear-gradient\([^,]+,\s*rgba\(([^)]+)\),\s*rgba\(([^)]+)\)/
      .exec(block);
  if (!m) throw new Error("the panel gradient did not parse - was it reformatted?");
  return [parseRgbColour(m[1]), parseRgbColour(m[2])];
}

/** The panel as it actually paints: both stops composited onto the frame. */
export function glassRightStops(css: string): [Rgb, Rgb] {
  const idx = css.indexOf(".glass-right {");
  if (idx < 0) throw new Error("the .glass-right rule moved or was renamed");
  const block = css.slice(idx, idx + 400);
  const m =
    /background:\s*linear-gradient\([^,]+,\s*rgba\(([^)]+)\),\s*rgba\(([^)]+)\)/
      .exec(block);
  if (!m) throw new Error("the panel gradient did not parse - was it reformatted?");
  const f = frameColour(css);
  return [
    over(parseRgbColour(m[1]), f, alphaOf(m[1])),
    over(parseRgbColour(m[2]), f, alphaOf(m[2])),
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
