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
} from "@/test/helpers/glassSurfaceCss";

import {
  AA_NORMAL,
  contrastRatio,
  luminance,
  parseHex,
} from "@/lib/appearance/contrast";
import type { Rgb } from "@/lib/appearance/contrast";

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
 * `parseColor` stays local because contrast.ts only parses hex, and the
 * stylesheet writes some of these colours as `rgb(...)`. It delegates the
 * hex half rather than reimplementing it.
 */
function parseColor(raw: string): Rgb {
  const hex = parseHex(raw.trim());
  if (hex) return hex;
  const rgb = /rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)/.exec(raw);
  if (rgb) return { r: Number(rgb[1]), g: Number(rgb[2]), b: Number(rgb[3]) };
  throw new Error(`parseColor could not read "${raw}" - format changed?`);
}

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
   *  nor a custom property) - so this is read from source text, the nearest
   *  honest measurement available, matching how the defect was originally
   *  measured (near-opaque paint at 0.94-0.97 alpha, treated as the surface
   *  colour itself). */
  function glassRightBackgroundStops(): [Rgb, Rgb] {
    const idx = compiledCss.indexOf(".glass-right {");
    expect(idx, "the .glass-right rule moved or was renamed").toBeGreaterThan(-1);
    const block = compiledCss.slice(idx, idx + 400);
    const m = /background:\s*linear-gradient\([^,]+,\s*rgba\(([^)]+)\),\s*rgba\(([^)]+)\)/.exec(
      block,
    );
    expect(m, "glass-right's background gradient did not parse - format changed?").not.toBeNull();
    return [parseColor(`rgba(${m![1]})`), parseColor(`rgba(${m![2]})`)];
  }

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

  it("the stylesheet actually compiled (guards the guard)", () => {
    expect(compiledCss.length).toBeGreaterThan(1000);
  });

  it("GROUND: :root's danger colour fails AA text contrast on .glass-right", () => {
    const danger = parseColor(rootDangerToken());
    const [top, bottom] = glassRightBackgroundStops();
    const cTop = contrastRatio(danger, top);
    const cBottom = contrastRatio(danger, bottom);
    // Measured: 3.67:1 against the lighter (top) stop, 3.37:1 against the
    // darker (bottom) stop - both short of the 4.5:1 AA floor. This is the
    // defect as originally reported (3.4-3.7:1), reproduced here from the
    // real source values rather than restated as a hand-picked number.
    expect(cTop, "top-stop contrast").toBeLessThan(AA_TEXT_THRESHOLD);
    expect(cBottom, "bottom-stop contrast").toBeLessThan(AA_TEXT_THRESHOLD);
    expect(cTop).toBeGreaterThan(3.0);
    expect(cBottom).toBeGreaterThan(3.0);
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

  it("FIX: .glass-right redefines the token to clear AA text contrast on this surface", () => {
    const glassRight = wrapInGlassRight();
    const resolved = surfaceToken(glassRight, "--color-es-danger");
    expect(resolved, "the override did not resolve through the real cascade").not.toBe("");

    const darkened = parseColor(resolved);
    const [top, bottom] = glassRightBackgroundStops();
    const cTop = contrastRatio(darkened, top);
    const cBottom = contrastRatio(darkened, bottom);

    // Measured: 5.41:1 against the top stop, 4.97:1 against the bottom -
    // comfortably over the 4.5:1 floor on both ends of the gradient, without
    // dropping so far that it reads as body text rather than danger.
    expect(cTop, "top-stop contrast after the fix").toBeGreaterThanOrEqual(AA_TEXT_THRESHOLD);
    expect(cBottom, "bottom-stop contrast after the fix").toBeGreaterThanOrEqual(AA_TEXT_THRESHOLD);
    // Not overshot into near-black: still recognisably a mid-tone rose.
    expect(luminance(darkened)).toBeGreaterThan(0.05);

    glassRight.remove();
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

  it("the persona-error background tint and border stay literal, not tied to the token", () => {
    // .persona-error / .persona-local-error paint their background tint and
    // border from hardcoded rgba(195, 106, 114, ...) literals, not
    // var(--color-es-danger) - so darkening the token cannot turn that tint
    // into a solid block. This pins that down: if a later edit switches
    // these to the variable, this fails and forces a re-check of the tint
    // against the darkened value instead of silently drifting.
    const idx = compiledCss.indexOf(".persona-error,");
    expect(idx, "the persona-error rule moved").toBeGreaterThan(-1);
    const block = compiledCss.slice(idx, compiledCss.indexOf("}", idx));
    expect(block).toMatch(/background:\s*rgba\(195,\s*106,\s*114,\s*0\.08\)/);
    expect(block).toMatch(/border-color:\s*rgba\(195,\s*106,\s*114,\s*0\.16\)/);
    expect(block).not.toMatch(/background:\s*var\(--color-es-danger\)/);
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
    const blocks = [
      ...compiledCss.matchAll(
        /\.persona-danger-action\s*\{([^}]*)\}/g),
    ];
    expect(blocks.length, "the .persona-danger-action rule moved or was renamed")
      .toBeGreaterThan(0);
    let hex: string | null = null;
    for (const b of blocks) {
      const found = /color:\s*(#[0-9a-fA-F]{6})/.exec(b[1]);
      if (found) hex = found[1];
    }
    expect(hex, "persona-danger-action no longer paints a literal colour").not.toBeNull();

    const ink = parseColor(hex!);
    const [top, bottom] = glassRightBackgroundStops();
    // The class paints a white wash over the panel, so the ink sits on that
    // rather than on the panel directly. The ALPHA IS READ, not retyped: a
    // hardcoded 0.12 would go on printing a comfortable ratio for a composite
    // that no longer exists the moment somebody changes the stylesheet.
    const washed = blocks.map((b) => b[1]).join(" ").match(/background:\s*rgba\(\s*255\s*,\s*255\s*,\s*255\s*,\s*([0-9.]+)\s*\)/);
    expect(washed, "persona-danger-action no longer paints a white wash").not.toBeNull();
    const alpha = Number(washed![1]);
    expect(alpha).toBeGreaterThan(0);
    expect(alpha).toBeLessThan(1);
    const tint = (bg: Rgb): Rgb => ({
      r: 255 * alpha + bg.r * (1 - alpha),
      g: 255 * alpha + bg.g * (1 - alpha),
      b: 255 * alpha + bg.b * (1 - alpha),
    });
    expect(contrastRatio(ink, tint(top)), "top-stop")
      .toBeGreaterThanOrEqual(AA_TEXT_THRESHOLD);
    expect(contrastRatio(ink, tint(bottom)), "bottom-stop")
      .toBeGreaterThanOrEqual(AA_TEXT_THRESHOLD);
    // GROUND, and the discriminating half: the rose this replaced must FAIL
    // the same measurement, or the assertion above proves nothing about the
    // value actually chosen.
    expect(contrastRatio(parseColor("#C36A72"), tint(top)))
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
