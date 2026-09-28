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
 * COMPOSITING MODEL, and it is now the house model: the gradient stops are
 * SEMI-TRANSPARENT and their alpha is honoured, over the app frame
 * (`--color-es-background`). All four files that measure this panel share it
 * as of 5 September 2026, through `helpers/glassSurfaceCss.ts`.
 *
 * `glass-right-danger-contrast.test.ts` used to drop the fourth channel and
 * treat the stops as opaque, which reads roughly 0.3 to 0.55 MORE generous -
 * the direction that lets a real regression through. Under that reading the
 * ghost ink measured 4.9109:1 and passed AA, so a test copied from there
 * would have been born green and reported this defect as already fixed. That
 * is why the model lives in one module now instead of in four copies.
 *
 * Everything is parsed from the compiled stylesheet. jsdom applies no
 * Tailwind utility rules at all (helpers/glassSurfaceCss.ts records the
 * measurement), so getComputedStyle could not answer any of this.
 */
import { beforeAll, describe, expect, it } from "vitest";

import {
  AAA_NORMAL,
  AA_NON_TEXT,
  AA_NORMAL,
  contrastRatio,
  luminance,
  type Rgb,
} from "@/lib/appearance/contrast";
import {
  composite,
  glassRightStops,
  loadGlassRightCss,
  over,
  parseRgbColour,
} from "@/test/helpers/glassSurfaceCss";

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

/** The panel's two gradient stops, composited onto the frame.
 *
 * Shared with the other three files that measure this surface as of
 * 5 September 2026. The frame is load-bearing: both stops are translucent, so
 * --color-es-background reaches the eye through them, and with a lighter frame
 * the ink would clear AA on its own. */
const panelStops = (): [Rgb, Rgb] => glassRightStops(css);

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
function ghostDeclaration(
  prop: string,
  opts: { focus?: boolean; own?: boolean } = {},
): string | null {
  // `own` narrows to the rule whose selector is THAT CLASS AND NOTHING ELSE.
  // Without it the shared `.persona-select-action, .persona-ghost-action, ...`
  // block is also a match, and taking the last match meant deleting the
  // single-class ink rule - the one the headline claim is about - left this
  // file green on the shared muted colour, 0.05 above its own floor.
  const selector = opts.focus
    ? /\.persona-ghost-action:focus-visible[^{]*\{/g
    : opts.own
      ? /(?:^|[}\n;])\s*\.persona-ghost-action\s*\{/g
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

/** A value with every var() chased out of it, panel scope first: these
 *  declarations live inside .glass-right, and the same names carry the dark
 *  shell's values globally. Alpha survives the trip, which matters - the
 *  ghost button's fill is a translucent wash and compositing it needs the
 *  whole colour, not its first three channels. */
function resolveValue(raw: string): string {
  let current = raw.trim();
  for (let hop = 0; hop < 8; hop += 1) {
    const v = /^var\(\s*(--[\w-]+)\s*\)$/.exec(current);
    if (!v) return current;
    current = panelToken(v[1]).trim();
  }
  throw new Error(`${raw} does not resolve to a colour in eight hops`);
}

/** A colour that may be a hex, an rgb()/rgba() list, or a var() reference. */
function resolve(raw: string): Rgb {
  return parseRgbColour(resolveValue(raw));
}

/** The button's own fill, over each panel stop. Two compositing steps, not
 *  one: the fill is translucent over a panel that is itself translucent. */
function fillStops(): [Rgb, Rgb] {
  const raw = ghostDeclaration("background");
  expect(raw, "the ghost action declares no background").not.toBeNull();
  const [top, bottom] = panelStops();
  const fill = resolveValue(raw!);
  return [composite(fill, top), composite(fill, bottom)];
}

describe("the right panel's ghost buttons", () => {
  it("the stylesheet actually compiled (guards the guard)", () => {
    expect(css.length).toBeGreaterThan(1000);
  });

  it("the harness resolved real colours, not empty strings", () => {
    // Every assertion below is a search over a string. Without this, a
    // renamed class or a reformatted gradient turns the whole file into a
    // set of comparisons between two identical defaults, all of which pass.
    const ink = ghostDeclaration("color", { own: true });
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

    expect(contrastRatio(indicatorTop, top)).toBeGreaterThanOrEqual(AA_NON_TEXT);
    expect(contrastRatio(indicatorBottom, bottom))
      .toBeGreaterThanOrEqual(AA_NON_TEXT);
  });

  it("the ink clears AA at the BOTTOM stop, where the panel is darkest", () => {
    // Only the bottom. The top stop reads 5.08:1 and passes; even t=0.90
    // reads 4.54:1 and passes. The miss is confined to the last few percent
    // of the gradient, and the claim is written no wider than that.
    const ink = resolve(ghostDeclaration("color", { own: true })!);
    const [, fillBottom] = fillStops();

    expect(contrastRatio(ink, fillBottom))
      .toBeGreaterThanOrEqual(AA_NORMAL + MARGIN);
  });

  it("and does not become the primary ink while doing it", () => {
    // A ceiling and an ordering, both from index.css's own constraint that
    // "anything softer than this and the two tiers stop being two tiers".
    // Darkening the secondary ink until it matches the primary would clear
    // AA and quietly delete the distinction the panel is built on.
    const ink = resolve(ghostDeclaration("color", { own: true })!);
    const primary = resolve(panelToken("--color-es-text-light"));
    const [fillTop] = fillStops();

    expect(contrastRatio(ink, fillTop)).toBeLessThan(
      contrastRatio(primary, fillTop));
    expect(luminance(ink)).toBeGreaterThan(luminance(primary));
  });
});
