/**
 * glass-right-ghost-contrast.test.ts - the right panel's quiet buttons.
 *
 * Twenty `variant="ghost"` buttons live in the right panel and every one of
 * them carries `.persona-ghost-action` or `.persona-danger-action`. Those
 * classes declare border, background and colour with `!important` from an
 * UNLAYERED rule, so the cva `ghost` variant never reaches the screen here -
 * `index.css` beats `@layer utilities` whatever the specificity says. The
 * surface under test is therefore the CSS, not the component.
 *
 * COMPOSITING MODEL, fixed here and not negotiable: the gradient stops are
 * SEMI-TRANSPARENT and their alpha is honoured, over the app frame
 * (`--color-es-background`). This is `right-panel-readability.test.ts`'s
 * model. `glass-right-danger-contrast.test.ts` drops the fourth channel and
 * treats the stops as opaque, which reads roughly 0.7 MORE generous - the
 * direction that lets a real regression through. Under that model the ghost
 * ink measures 4.9109:1 and passes AA, so a test copied from there would
 * have been born green and reported this defect as already fixed.
 *
 * Everything is parsed from the compiled stylesheet. jsdom applies no
 * Tailwind utility rules at all (helpers/glassSurfaceCss.ts records the
 * measurement), so getComputedStyle could not answer any of this.
 */
import { beforeAll, describe, expect, it } from "vitest";

import {
  AAA_NORMAL,
  AA_NORMAL,
  contrastRatio,
  luminance,
  parseHex,
  type Rgb,
} from "@/lib/appearance/contrast";
import { loadGlassRightCss } from "@/test/helpers/glassSurfaceCss";

/** WCAG's non-text floor. A focus indicator is a graphic, not a letter. */
const NON_TEXT = 3;

/** How far past AA a fix has to land.
 *
 * The defect is a 0.006 miss. Closing it by 0.006 would put the value back
 * under the bar on the next gradient retouch, so the fix has to buy room
 * rather than touch the line. */
const MARGIN = 0.15;

let css = "";

beforeAll(async () => {
  css = await loadGlassRightCss();
}, 60_000);

function parseRgba(raw: string): Rgb {
  const m = /(\d+)[,\s]+(\d+)[,\s]+(\d+)/.exec(raw);
  if (!m) throw new Error(`unparseable colour: ${raw}`);
  return { r: Number(m[1]), g: Number(m[2]), b: Number(m[3]) };
}

/** The fourth channel of an rgb()/rgba() colour, defaulting to opaque.
 *
 * The inside of the parentheses is taken FIRST. Splitting the whole
 * `rgba(...)` string on commas leaves the closing paren stuck to the alpha,
 * and `Number(" 0.12) ")` is NaN - which then travels silently through every
 * composite and every ratio, because NaN compares false everywhere and
 * `not.toBe(1)` accepts it. That is how the first version of this file
 * reported a passing harness gate sitting on top of four NaNs. */
function alphaOf(raw: string): number {
  const inner = /\(([^)]*)\)/.exec(raw);
  const parts = (inner ? inner[1] : raw).split(",").map((x) => x.trim());
  return parts.length > 3 ? Number(parts[3]) : 1;
}

/** src over dst. Same rounding as right-panel-readability.test.ts. */
function over(src: Rgb, dst: Rgb, alpha: number): Rgb {
  const mix = (a: number, b: number) => Math.round(a * alpha + b * (1 - alpha));
  return { r: mix(src.r, dst.r), g: mix(src.g, dst.g), b: mix(src.b, dst.b) };
}

/** A `--token: value;` as declared anywhere in the compiled text. */
function token(name: string): string {
  const m = new RegExp(name + ":\\s*([^;]+);").exec(css);
  expect(m, `the stylesheet does not declare ${name}`).not.toBeNull();
  return m![1].trim();
}

/** A token as `.glass-right` redeclares it.
 *
 * The panel overrides several of these and the global value is the DARK
 * shell's. `--color-es-text-light` is `#EAEEF3` at `@theme inline` and
 * `#3A4A5C` inside `.glass-right`; measuring the first against this panel is
 * the already-closed defect `right-panel-readability.test.ts` was written
 * for - it read about 1.08:1, and that figure is where this record's own
 * headline number came from by mistake. */
function panelToken(name: string): string {
  const idx = css.indexOf(".glass-right {");
  expect(idx, "the .glass-right rule moved or was renamed").toBeGreaterThan(-1);
  const block = css.slice(idx, css.indexOf("}", idx));
  const m = new RegExp(name + ":\\s*([^;]+);").exec(block);
  // Falling back to the global declaration is the CASCADE, not a shortcut:
  // the panel overrides some tokens and inherits the rest. `--ring` is one
  // it does not redeclare, so the focus colour really is the global one.
  return m ? m[1].trim() : token(name);
}

/** The frame the panel is painted on. Load-bearing: both gradient stops are
 *  translucent, so the frame reaches the eye through them. With a lighter
 *  frame the ink would clear AA on its own. */
function frame(): Rgb {
  const hex = parseHex(token("--color-es-background"));
  expect(hex, "the frame background is no longer a plain hex").not.toBeNull();
  return hex!;
}

/** The panel's two gradient stops, composited onto the frame. */
function panelStops(): [Rgb, Rgb] {
  const idx = css.indexOf(".glass-right {");
  expect(idx, "the .glass-right rule moved or was renamed").toBeGreaterThan(-1);
  const block = css.slice(idx, idx + 400);
  const m =
    /background:\s*linear-gradient\([^,]+,\s*rgba\(([^)]+)\),\s*rgba\(([^)]+)\)/
      .exec(block);
  expect(m, "the panel gradient did not parse - was it reformatted?")
    .not.toBeNull();
  const f = frame();
  return [
    over(parseRgba(m![1]), f, alphaOf(m![1])),
    over(parseRgba(m![2]), f, alphaOf(m![2])),
  ];
}

/**
 * A declaration from the rule that governs `.persona-ghost-action`.
 *
 * NOT the danger file's `/\.persona-danger-action\s*\{([^}]*)\}/`. That regex
 * cannot be reused: `.persona-ghost-action` appears six times in the compiled
 * sheet and never once alone before its brace - it is always a member of a
 * comma-separated list. A copied regex matches zero blocks and every
 * assertion built on it passes over an empty string.
 *
 * The LAST declaration wins, which is the cascade: the shared block sets the
 * muted token, and a later single-class rule may override it.
 */
function ghostDeclaration(prop: string, opts: { focus?: boolean } = {}):
    string | null {
  const selector = opts.focus
    ? /\.persona-ghost-action:focus-visible[^{]*\{/g
    : /\.persona-ghost-action(?![-:a-z])[^{]*\{/g;
  let found: string | null = null;
  for (const m of css.matchAll(selector)) {
    // Skip the focus rule when asked for the resting one, and vice versa.
    const head = m[0];
    if (!opts.focus && head.includes(":focus-visible")) continue;
    if (!opts.focus && (head.includes(":hover") || head.includes(":active"))) {
      continue;
    }
    const open = m.index! + head.length - 1;
    const close = css.indexOf("}", open);
    const body = css.slice(open + 1, close);
    const d = new RegExp(`(?:^|;|\\s)${prop}:\\s*([^;!]+)`).exec(body);
    if (d) found = d[1].trim();
  }
  return found;
}

/** A colour that may be a hex, an rgb()/rgba() list, or a var() reference. */
function resolve(raw: string): Rgb {
  const v = /var\(\s*(--[\w-]+)/.exec(raw);
  // Panel scope first: these declarations live inside .glass-right, and
  // the same names carry the dark shell values globally.
  if (v) return resolve(panelToken(v[1]));
  if (raw.includes("(")) return parseRgba(raw);
  const hex = parseHex(raw);
  expect(hex, `unresolvable colour: ${raw}`).not.toBeNull();
  return hex!;
}

/** The button's own fill, over each panel stop. */
function fillStops(): [Rgb, Rgb] {
  const raw = ghostDeclaration("background");
  expect(raw, "the ghost action declares no background").not.toBeNull();
  const [top, bottom] = panelStops();
  return [
    over(parseRgba(raw!), top, alphaOf(raw!)),
    over(parseRgba(raw!), bottom, alphaOf(raw!)),
  ];
}

describe("the right panel's ghost buttons", () => {
  it("the stylesheet actually compiled (guards the guard)", () => {
    expect(css.length).toBeGreaterThan(1000);
  });

  it("the harness resolved real colours, not empty strings", () => {
    // Every assertion below is a search over a string. Without this, a
    // renamed class or a reformatted gradient turns the whole file into a
    // set of comparisons between two identical defaults, all of which pass.
    const ink = ghostDeclaration("color");
    expect(ink, "no colour declaration reached .persona-ghost-action")
      .not.toBeNull();
    const [fillTop, fillBottom] = fillStops();
    // FINITE and not 1.  alone accepts NaN, and NaN is exactly
    // what a mis-parsed colour produces - it then compares false against
    // every threshold below without ever failing this gate.
    for (const surface of [fillTop, fillBottom]) {
      const r = contrastRatio(resolve(ink!), surface);
      expect(Number.isFinite(r), 'the ratio came out ' + r).toBe(true);
      expect(r).not.toBe(1);
    }
  });

  it("POSITIVE CONTROL: the panel's primary ink clears the same bar", () => {
    // Against the BUTTON FILL, not the panel - the two differ, and quoting
    // the panel figures here would make this control pass for a surface the
    // buttons do not sit on.
    const primary = resolve(panelToken("--color-es-text-light"));
    const [fillTop, fillBottom] = fillStops();

    // AAA, imported. The bottom bar was written as a bare `7`, which is
    // `AAA_NORMAL` retyped from the module this file already imports.
    expect(contrastRatio(primary, fillTop)).toBeGreaterThan(AAA_NORMAL + 1);
    expect(contrastRatio(primary, fillBottom)).toBeGreaterThan(AAA_NORMAL);
  });

  it("the focus indicator clears the 3:1 non-text floor", () => {
    // THE HEAVY ITEM. `button.tsx` asks for `focus-visible:border-ring`, and
    // the shared block's `border: ... !important` eats it - an unlayered
    // rule beats a layered utility. What survives is `ring-ring/50`, which
    // composites to 1.97:1 / 1.88:1 on this panel. SC 1.4.11 covers
    // components AND STATES, keyboard focus is a state, and nothing else on
    // screen marks it, so there is no exemption to fall back on.
    const [top, bottom] = panelStops();
    const ring = resolve(token("--ring"));

    // The border the focus state actually paints, if a rule restores one.
    const focusBorder = ghostDeclaration("border-color", { focus: true });
    const indicatorTop = focusBorder
      ? resolve(focusBorder)
      : over(ring, top, 0.5);
    const indicatorBottom = focusBorder
      ? resolve(focusBorder)
      : over(ring, bottom, 0.5);

    expect(contrastRatio(indicatorTop, top)).toBeGreaterThanOrEqual(NON_TEXT);
    expect(contrastRatio(indicatorBottom, bottom))
      .toBeGreaterThanOrEqual(NON_TEXT);
  });

  it("the ink clears AA at the BOTTOM stop, where the panel is darkest", () => {
    // Only the bottom. The top stop reads 5.08:1 and passes; even t=0.90
    // reads 4.54:1 and passes. The miss is confined to the last few percent
    // of the gradient, and the claim is written no wider than that.
    const ink = resolve(ghostDeclaration("color")!);
    const [, fillBottom] = fillStops();

    expect(contrastRatio(ink, fillBottom))
      .toBeGreaterThanOrEqual(AA_NORMAL + MARGIN);
  });

  it("and does not become the primary ink while doing it", () => {
    // A ceiling and an ordering, both from index.css's own constraint that
    // "anything softer than this and the two tiers stop being two tiers".
    // Darkening the secondary ink until it matches the primary would clear
    // AA and quietly delete the distinction the panel is built on.
    const ink = resolve(ghostDeclaration("color")!);
    const primary = resolve(panelToken("--color-es-text-light"));
    const [fillTop] = fillStops();

    expect(contrastRatio(ink, fillTop)).toBeLessThan(
      contrastRatio(primary, fillTop));
    expect(luminance(ink)).toBeGreaterThan(luminance(primary));
  });
});
