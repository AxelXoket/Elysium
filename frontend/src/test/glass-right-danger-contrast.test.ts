/**
 * glass-right-danger-contrast.test.ts - the light-panel danger colour, measured.
 *
 * --color-es-danger is declared once, at :root, as #C36A72. That value was
 * only ever checked against the app's dark surfaces. On .glass-right - the
 * one LIGHT surface in the app - text painted with it (persona-local-error,
 * settings-error, and every inline danger style inside RightPanel) measured
 * 3.37-3.67:1 against the panel's own background, short of the 4.5:1 WCAG AA
 * floor for text at this size.
 *
 * The fix redefines the token INSIDE `.glass-right`, the same mechanism that
 * selector already uses for --foreground, --muted-foreground and the rest
 * (index.css:838). Every dark surface still reads the :root declaration and
 * is untouched; only descendants of .glass-right see the darker value.
 *
 * Both numbers below come from the real cascade, not a hand-copied hex:
 * the compiled CSS is index.css run through the project's own Vite pipeline
 * (see helpers/glassSurfaceCss.ts), and the redefined token is read back off
 * a live `.glass-right` element with getComputedStyle().getPropertyValue,
 * the one mechanism that survives jsdom for a custom property (see that
 * module's comment for what does not: :root-only tokens and Tailwind's own
 * generated utility rules).
 */

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  loadGlassRightCss,
  injectCss,
  wrapInGlassRight,
  surfaceToken,
  alphaOf,
  composite,
  discriminatingInk,
  glassRightStops,
  parseRgbColour,
  rawGlassRightStops,
  resolveColour,
} from "@/test/helpers/glassSurfaceCss";
import { parseSheet } from "@/test/helpers/compiledSheet";
import { panelMistGrounds } from "@/test/helpers/fogGround";

import {
  AA_NON_TEXT,
  AA_NORMAL,
  AAA_NORMAL,
  contrastRatio,
  luminance,
  parseHex,
  type Rgb,
} from "@/lib/appearance/contrast";

/**
 * The maths is IMPORTED, not restated.
 *
 * This file used to carry its own `relativeLuminance`, its own
 * `contrastRatio` and its own `const AA_TEXT_THRESHOLD = 4.5`, all copied
 * from lib/appearance/contrast.ts. That made it a test of its private copy:
 * ship a wrong coefficient or a lowered threshold in the module the APP
 * actually paints and reads with, and this file would have gone on printing
 * comfortable numbers. It proved the stylesheet, never the comparator.
 * InkPicker.test.ts already imports these; so does this now.
 *
 * The COLOUR reading is imported too, as of 5 September 2026. This file used
 * to keep a private `parseColor` that dropped the fourth channel, and a
 * private `tint()` that composited without rounding, and it was the one file
 * of the four measuring this panel that treated the gradient stops as OPAQUE.
 * That read the surface roughly 0.3 to 0.55 more generous than it paints -
 * the direction that lets a real regression through - and it disagreed with
 * three of index.css's own four rationale comments, which already quote
 * composited figures. helpers/glassSurfaceCss.ts now owns the model for all
 * four files; see its comment for why the alpha is honoured.
 */
const AA_TEXT_THRESHOLD = AA_NORMAL;

describe("glass-right danger token - contrast", () => {
  let compiledCss: string;
  let styleEl: HTMLStyleElement;

  beforeAll(async () => {
    compiledCss = await loadGlassRightCss();
    styleEl = injectCss(compiledCss);
  }, 20000);

  afterAll(() => styleEl.remove());

  /** .glass-right's own painted background - the surface the danger text
   *  actually sits on. Pulled from the compiled CSS rather than hand-copied,
   *  so a future edit to the gradient re-measures instead of silently
   *  drifting from what this test asserts. background is a shorthand
   *  gradient, which is not something the custom-property technique in
   *  glassSurfaceCss.ts can resolve through jsdom (that technique reads
   *  inherited custom properties, and `background` is neither inherited
   *  nor a custom property) - so the stops are read from the compiled text.
   *
   *  Both stops are translucent and their ALPHA IS HONOURED, over the frame
   *  colour parsed out of --color-es-background. Dropping the fourth channel,
   *  which is what this file did until 5 September 2026, measures a surface
   *  that is never painted. */
  const stops = (): [Rgb, Rgb] => glassRightStops(compiledCss);

  /** The mist's veil over the darkest pixel its fog can paint: what the
   *  words sit on with the mist on, the default. Darker than either stop. */
  const mistGrounds = (): Rgb[] => panelMistGrounds(parseSheet(compiledCss), "right");

  /** The value :root declares for --color-es-danger, read from source text
   *  rather than the DOM: jsdom does not resolve :root-only custom
   *  properties through getComputedStyle().getPropertyValue (confirmed in
   *  helpers/glassSurfaceCss.ts's module comment), so this is the nearest
   *  honest measurement of "what every non-.glass-right surface still
   *  uses" - the positive control that the dark surfaces are untouched. */
  function rootDangerToken(): string {
    // Tailwind v4 compiles the @theme inline block's tokens onto
    // ":root, :host" in the shipped CSS, not a bare ":root" - confirmed by
    // reading the compiled output, not assumed from the source file.
    const idx = compiledCss.indexOf(":root, :host {");
    expect(idx, "the :root, :host rule moved").toBeGreaterThan(-1);
    const block = compiledCss.slice(idx, compiledCss.indexOf("}", idx));
    const m = /--color-es-danger:\s*([^;]+);/.exec(block);
    expect(m, "-color-es-danger is no longer declared at :root").not.toBeNull();
    return m![1].trim();
  }

  /** A value as the DARK shell resolves it: var() chased through the sheet's
   *  global declarations. */
  const globalResolve = (value: string) => resolveColour(compiledCss, value);

  /** The same value as THIS PANEL resolves it: .glass-right's own
   *  redeclarations first, the global ones for everything it inherits. */
  function panelResolve(value: string): string {
    const idx = compiledCss.indexOf(".glass-right {");
    expect(idx, "the .glass-right rule moved or was renamed").toBeGreaterThan(-1);
    const block = compiledCss.slice(idx, compiledCss.indexOf("}", idx));
    let current = value.trim();
    for (let hop = 0; hop < 8; hop += 1) {
      const m = /^var\(\s*(--[-\w]+)\s*\)$/.exec(current);
      if (!m) return current;
      const local = new RegExp(`${m[1]}:\\s*([^;]+);`).exec(block);
      current = local ? local[1].trim() : resolveColour(compiledCss, current);
    }
    throw new Error(`${value} does not resolve in eight hops`);
  }

  it("the stylesheet actually compiled (guards the guard)", () => {
    expect(compiledCss.length).toBeGreaterThan(1000);
  });

  it("the harness resolved real colours, not empty strings or NaN", () => {
    // Every measurement in this file is a search over a string followed by a
    // division. Without this, a renamed class or a reformatted gradient turns
    // the whole file into comparisons between two identical defaults - and a
    // mis-parsed alpha turns them into comparisons involving NaN, which
    // compares false against every threshold below without ever failing.
    // `not.toBe(1)` on its own ACCEPTS NaN, so the finiteness half is the
    // load-bearing one and both halves are needed.
    const danger = parseRgbColour(rootDangerToken());
    for (const stop of stops()) {
      const r = contrastRatio(danger, stop);
      expect(Number.isFinite(r), `the ratio came out ${r}`).toBe(true);
      expect(r).not.toBe(1);
    }
  });

  it("the panel is measured as a COMPOSITE, not as opaque paint", () => {
    // MUTATION CATCHER. The gradient stops are translucent; the surface is
    // what they make over --color-es-background. This ink is DERIVED, never
    // typed: it is the middle of the band of greys the two readings disagree
    // about - clearing AA against the stops as written, failing AA against
    // the stops as painted. Both stops and the frame are parsed out of the
    // compiled sheet and the threshold is imported, so nothing here is a hex
    // that can quietly stop matching the stylesheet.
    //
    // It goes red two different ways, which is the point. Take the `over()`
    // out of the shared helper and the two readings collapse into one, so no
    // grey disagrees and the fallback ink lands at or above AA. Lighten
    // --color-es-background towards white and the stops converge on the same
    // place, with the same result. Either way the ink stops being
    // discriminating and this fails.
    const [, rawBottom] = rawGlassRightStops(compiledCss);
    const [, bottom] = stops();
    const ink = discriminatingInk(rawBottom, bottom);

    expect(
      contrastRatio(ink, rawBottom),
      "the discriminating ink no longer clears AA on the stops AS WRITTEN",
    ).toBeGreaterThanOrEqual(AA_TEXT_THRESHOLD);
    expect(
      contrastRatio(ink, bottom),
      "the same ink cleared AA on the composited stop too - " +
        "the two readings have collapsed into one, so compositing is not happening",
    ).toBeLessThan(AA_TEXT_THRESHOLD);
  });

  it("POSITIVE CONTROL: the compositing step actually darkened the panel", () => {
    // The frame is near-black, so laying a translucent stop over it can only
    // move the result DOWN in luminance. Strict inequality, both stops: if
    // compositing is ever turned into a no-op the two are equal, not merely
    // close, and equality fails this.
    const raw = rawGlassRightStops(compiledCss);
    const painted = stops();
    for (const i of [0, 1] as const) {
      expect(
        luminance(painted[i]),
        `stop ${i} is no darker painted than it is written`,
      ).toBeLessThan(luminance(raw[i]));
    }
  });

  it("POSITIVE CONTROL: the meter can still see a low-contrast ink", () => {
    // Near-white ink on a near-white panel. If the measurement ever goes
    // blind - a NaN, an empty parse, a constant - this is the assertion that
    // notices, because it is the only one here that expects a FAILURE and so
    // the only one a broken comparator cannot accidentally satisfy. It reads
    // about 1.05:1, which does not clear the floor for a graphic, let alone
    // for letters.
    const [, bottom] = stops();
    expect(contrastRatio(parseHex("#F0F2F4")!, bottom)).toBeLessThan(AA_NON_TEXT);
  });

  it("POSITIVE CONTROL: the comparator itself, and the order of its thresholds", () => {
    // Black on white is 21:1 by definition. Everything above divides two
    // luminances; if that arithmetic is wrong every number in this file is
    // wrong in the same direction and no ratio assertion would show it.
    expect(contrastRatio(parseHex("#000")!, parseHex("#fff")!)).toBeCloseTo(21, 3);
    // And the thresholds are ordered, which is what makes them thresholds:
    // a graphic needs less than a letter, and AAA asks for more than AA.
    expect(AA_NON_TEXT).toBeLessThan(AA_NORMAL);
    expect(AA_NORMAL).toBeLessThan(AAA_NORMAL);
  });

  it("GROUND: :root's danger colour fails AA text contrast on .glass-right", () => {
    const danger = parseRgbColour(rootDangerToken());
    const [top, bottom] = stops();
    const cTop = contrastRatio(danger, top);
    const cBottom = contrastRatio(danger, bottom);
    // On the composited panel: 3.51:1 at the lighter (top) stop, 3.17:1 at
    // the darker (bottom) one - both short of the 4.5:1 AA floor. This is
    // the defect as originally reported, reproduced from the real source
    // values rather than restated as a hand-picked number.
    expect(cTop, "top-stop contrast").toBeLessThan(AA_TEXT_THRESHOLD);
    expect(cBottom, "bottom-stop contrast").toBeLessThan(AA_TEXT_THRESHOLD);
  });

  it("GROUND: and the override is what closes the gap, by a whole ratio point", () => {
    // This replaced a pair of bare `> 3.0` floors, and the reason is worth
    // writing down. Those floors existed to say "the defect was a near miss,
    // not a blank screen" - a real thing to pin - but 3.0 was a number
    // nobody derived: under the opaque reading the bottom stop measured
    // 3.37, under the composited one it measures 2.996 at the old gradient
    // alpha and 3.17 at the current one. A threshold that flips sign when
    // the MEASUREMENT MODEL changes, without the colour moving at all, is
    // measuring the harness rather than the panel. Nudging it down to 2.9 to
    // keep it green would have been worse still: that is loosening a gate to
    // fit the number underneath it.
    //
    // The claim underneath was never really "3.0". It was that the scoped
    // override does real work - that #AF4650 is meaningfully darker on this
    // surface than the :root rose it shadows, at BOTH ends of the gradient.
    // A whole ratio point of separation says that in a way no measurement
    // model can flip: it holds at the opaque reading (1.74/1.60), at the
    // composited one (1.67/1.50), and on the shipped fog-lit surface too.
    const root = parseRgbColour(rootDangerToken());
    const glassRight = wrapInGlassRight();
    const fixed = parseRgbColour(surfaceToken(glassRight, "--color-es-danger"));
    glassRight.remove();

    for (const [name, stop] of [
      ["top", stops()[0]],
      ["bottom", stops()[1]],
    ] as const) {
      const gain = contrastRatio(fixed, stop) - contrastRatio(root, stop);
      expect(gain, `${name}-stop gain from the override`).toBeGreaterThan(1.0);
    }
  });

  it("POSITIVE CONTROL: :root still declares the original rose, and only .glass-right overrides it", () => {
    // The dark dialogs, the sidebar, every surface except .glass-right reads
    // this declaration. If the fix had touched it (instead of adding a
    // scoped override) every dark surface would have darkened too.
    expect(rootDangerToken().toLowerCase()).toBe("#c36a72");

    // Exactly one more declaration should exist anywhere in the compiled
    // sheet: the .glass-right override. Any other count means either the
    // override went missing, or it leaked onto a second selector -
    // including, silently, a dark one.
    const occurrences = compiledCss.match(/--color-es-danger:\s*[^;]+;/g) ?? [];
    expect(occurrences, "expected exactly the :root declaration plus one override").toHaveLength(2);
  });

  it("FIX: .glass-right redefines the token to clear AA text contrast on the panel's gradient, "
     + "with the mist off", () => {
    const glassRight = wrapInGlassRight();
    const resolved = surfaceToken(glassRight, "--color-es-danger");
    expect(resolved, "the override did not resolve through the real cascade").not.toBe("");

    const darkened = parseRgbColour(resolved);
    const [top, bottom] = stops();
    const cTop = contrastRatio(darkened, top);
    const cBottom = contrastRatio(darkened, bottom);

    // On the composited panel: 5.18:1 against the top stop, 4.67:1 against
    // the bottom - over the 4.5:1 floor at both ends of the gradient, without
    // dropping so far that it reads as body text rather than danger.
    expect(cTop, "top-stop contrast after the fix").toBeGreaterThanOrEqual(AA_TEXT_THRESHOLD);
    expect(cBottom, "bottom-stop contrast after the fix").toBeGreaterThanOrEqual(AA_TEXT_THRESHOLD);
    // Not overshot into near-black: still recognisably a mid-tone rose.
    expect(luminance(darkened)).toBeGreaterThan(0.05);

    glassRight.remove();
  });

  it("with the mist on the token falls under AA, and the words the panel paints in danger take "
     + "the island's danger ink, which clears it", () => {
    // The token's own reading is the one above, on the gradient. With the
    // mist on the words sit on its veil over the fog, and there the token
    // is under the floor; so no words on this panel are painted in it
    // straight on the mist (the Security tab's error feedback is, on a light
    // wash of its own), and the island's danger ink carries the rest.
    const glassRight = wrapInGlassRight();
    const token = parseRgbColour(surfaceToken(glassRight, "--color-es-danger"));
    glassRight.remove();
    const island = parseRgbColour(panelResolve("var(--es-island-danger-ink)"));
    const grounds = mistGrounds();
    // GROUND: both stops of the veil are read, and both are darker than the
    // gradient's darker stop.
    expect(grounds.length, "the veil's stops were not read").toBe(2);
    for (const ground of grounds) {
      expect(luminance(ground), "the mist is not darker than the gradient")
        .toBeLessThan(luminance(stops()[1]));
    }
    expect(Math.min(...grounds.map((g) => contrastRatio(token, g))),
      "the token clears AA on the mist too; the island's ink is no longer needed")
      .toBeLessThan(AA_TEXT_THRESHOLD);
    // On the panel, and on the refusal tint the error lines lay under
    // their words (.persona-error's own background), over every ground.
    const idx = compiledCss.indexOf(".persona-error,");
    expect(idx, "the persona-error rule moved").toBeGreaterThan(-1);
    const block = compiledCss.slice(idx, compiledCss.indexOf("}", idx));
    const tintDecl = /background:\s*([^;]+)/.exec(block);
    expect(tintDecl, "persona-error declares no background").not.toBeNull();
    const tint = panelResolve(tintDecl![1].trim());
    for (const ground of [...stops(), ...grounds]) {
      const at = `rgb(${ground.r}, ${ground.g}, ${ground.b})`;
      expect(contrastRatio(island, ground), `the island's ink on ${at}`)
        .toBeGreaterThanOrEqual(AA_TEXT_THRESHOLD);
      expect(contrastRatio(island, composite(tint, ground)), `the island's ink on the tint over ${at}`)
        .toBeGreaterThanOrEqual(AA_TEXT_THRESHOLD);
    }
  });

  it("every descendant of .glass-right inherits the SAME darkened token (the whole family moves together)", () => {
    // The status dot (ApiKeySection, ProxySection) and the destructive icons
    // (OrphanedCopyNotice, RotationBackupNotice, PlaintextBackupNotice) all
    // read var(--color-es-danger) with no class of their own that could
    // re-override it - so any element under .glass-right must resolve to
    // the identical value the text does. A second, different override
    // somewhere in that subtree would mean the family split apart.
    const glassRight = wrapInGlassRight();
    const child = document.createElement("span");
    glassRight.appendChild(child);

    const onSurface = surfaceToken(glassRight, "--color-es-danger");
    const onChild = surfaceToken(child, "--color-es-danger");
    expect(onChild).toBe(onSurface);
    expect(onChild.toLowerCase()).not.toBe("#c36a72");

    glassRight.remove();
  });

  it("the persona-error background tint and border do not follow the panel's danger override", () => {
    // .persona-error / .persona-local-error paint their tint and border from
    // a colour of their own, not from var(--color-es-danger) - so darkening
    // that token for this panel cannot turn the tint into a solid block.
    // What that "of their own" is spelled as stopped mattering when the tint
    // moved onto a token: the claim is about the CASCADE, so the cascade is
    // what is measured. Both values are resolved twice, once globally and
    // once as .glass-right redeclares things, and the two readings must be
    // the same reading. The danger token itself is the POSITIVE CONTROL for
    // that method: it really does differ between the two scopes, so a
    // resolver that ignored panel scope would fail this line first.
    const idx = compiledCss.indexOf(".persona-error,");
    expect(idx, "the persona-error rule moved").toBeGreaterThan(-1);
    const block = compiledCss.slice(idx, compiledCss.indexOf("}", idx));
    const declared = (prop: string) => {
      const m = new RegExp(`${prop}:\\s*([^;]+)`).exec(block);
      expect(m, `persona-error declares no ${prop}`).not.toBeNull();
      return m![1].trim();
    };
    expect(panelResolve(declared("background"))).toBe("rgba(195, 106, 114, 0.08)");
    expect(globalResolve(declared("background"))).toBe("rgba(195, 106, 114, 0.08)");
    expect(panelResolve(declared("border-color"))).toBe("rgba(195, 106, 114, 0.16)");
    expect(globalResolve(declared("border-color"))).toBe("rgba(195, 106, 114, 0.16)");
    expect(block).not.toMatch(/background:\s*var\(--color-es-danger\)/);
    // POSITIVE CONTROL for the two resolvers: the token the tint refuses to
    // use DOES read differently in the two scopes, so "same in both" above
    // is a finding and not an artefact of a resolver that never looks.
    expect(panelResolve("var(--color-es-danger)"))
      .not.toBe(globalResolve("var(--color-es-danger)"));
  });

  it("the persona danger ACTION colour clears AA on this surface too", () => {
    // The gap a watchdog found on 2026-08-20, after the PersonaPanel delete
    // confirm button was moved off shadcn's `--destructive` and onto
    // `.persona-danger-action`. That class hardcodes its own colour and reads
    // NO token, so nothing in this file - which only ever measured
    // `--color-es-danger` - was defending it. Measured: setting that colour
    // back to #C36A72, the exact rose whose 3.29:1 caused the move, left all
    // 1654 frontend tests green. The class is used at 12 call sites across
    // six components, so the regression could have come back anywhere.
    // The selector is declared TWICE: once for the shared border/background
    // (whose `color` is a var) and once for its own literal ink. Scan every
    // block of that selector for a hex rather than taking the first `color:`,
    // which is the var and would make this measure the wrong thing.
    // ONLY the rule whose selector is that class and nothing else. The
    // shared block `.persona-ghost-action, .persona-danger-action { ... }`
    // also contains the name, and taking the last match meant deleting the
    // single-class rule - the one this file was written to defend - left the
    // test green, quietly measuring the shared muted ink instead.
    const blocks = [
      ...compiledCss.matchAll(
        /(?:^|[}\n;])\s*\.persona-danger-action\s*\{([^}]*)\}/g),
    ];
    expect(blocks.length, "the .persona-danger-action rule moved or was renamed")
      .toBeGreaterThan(0);
    let own: string | null = null;
    for (const b of blocks) {
      const found = /(?:^|;)\s*color:\s*([^;!]+)/.exec(b[1]);
      if (found) own = found[1].trim();
    }
    expect(own, "persona-danger-action no longer paints an ink of its own")
      .not.toBeNull();

    const ink = parseRgbColour(panelResolve(own!));
    const [top, bottom] = stops();
    const grounds = [top, bottom, ...mistGrounds()];
    // The class paints a white wash over the panel, so the ink sits on that
    // rather than on the panel directly - and the panel underneath is itself
    // a composite, so this is two compositing steps, not one. The WASH IS
    // READ WHOLE, colour and alpha together, through the same `composite()`
    // the panel uses: a hardcoded 0.12 would go on printing a comfortable
    // ratio for a paint that no longer exists the moment somebody edits the
    // stylesheet, and a private copy of the blend is how this file drifted
    // away from the other three in the first place.
    // The wash is declared in the SHARED rule, so it is read from the wider
    // scope on purpose; only the ink above belongs to the single-class rule.
    const shared = [
      ...compiledCss.matchAll(/\.persona-danger-action(?![-:a-z])[^{]*\{([^}]*)\}/g),
    ];
    expect(shared.length, "nothing names .persona-danger-action any more")
      .toBeGreaterThan(0);
    const washDecl = shared.map((b) => b[1]).join(" ").match(/background:\s*([^;!]+)/);
    expect(washDecl, "persona-danger-action no longer paints a background")
      .not.toBeNull();
    const wash = panelResolve(washDecl![1].trim());
    expect(wash, "persona-danger-action no longer paints a white wash")
      .toMatch(/^rgba\(\s*255\s*,\s*255\s*,\s*255\s*,/);
    const alpha = alphaOf(wash);
    expect(alpha, "the wash stopped being a wash").toBeGreaterThan(0);
    expect(alpha, "the wash went opaque and the panel no longer shows through")
      .toBeLessThan(1);
    // Every ground: both stops, and the mist's veil over the fog at its
    // darkest, which is the lowest reading of the four.
    for (const ground of grounds) {
      expect(contrastRatio(ink, composite(wash, ground)),
        `on rgb(${ground.r}, ${ground.g}, ${ground.b})`).toBeGreaterThanOrEqual(AA_TEXT_THRESHOLD);
    }
    // GROUND, and the discriminating half: the rose this replaced must FAIL
    // the same measurement, or the assertion above proves nothing about the
    // value actually chosen.
    expect(contrastRatio(parseRgbColour("#C36A72"), composite(wash, top)))
      .toBeLessThan(AA_TEXT_THRESHOLD);
  });

  // A test that asserted the WHY comment still sits above the override used
  // to live here, and it was deleted rather than kept. The distinction, since
  // the tests above plainly do read the stylesheet: these read the COMPILED
  // CSS because the compiled CSS is the artefact under test and jsdom cannot
  // resolve it, and they then compute a contrast ratio from it. The deleted
  // one read source text to assert something about the source - it passed
  // while a comment was present and the value beneath it was dead, and it
  // failed when somebody reworded prose that was never the behaviour.
  // Reading the artefact to measure it is not the same as grepping it to
  // avoid measuring.
});
