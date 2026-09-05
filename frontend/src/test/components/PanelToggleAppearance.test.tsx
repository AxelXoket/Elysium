/**
 * PanelToggleAppearance.test.tsx - the collapse handle is an arrow, not a box.
 *
 * Four requirements: no visible area around the arrow, an
 * invisible press area, the arrow visible at all times without hover, and
 * faint - low contrast, not assertive.
 *
 * Those four pull against each other, so each is pinned here separately. The
 * measurements go through `getComputedStyle` on the real rendered button with
 * the real stylesheet injected, because the thing under test is what the
 * CASCADE produces - a later rule or a higher-specificity override would beat
 * any regex over the source and this would not notice.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen } from "@testing-library/react";
import { readFileSync } from "fs";
import path from "path";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { PanelToggleButton } from "@/components/layout/PanelToggleButton";
import {
  AA_NON_TEXT,
  AA_NORMAL,
  contrastRatio,
  parseHex,
} from "@/lib/appearance/contrast";
import { composite } from "@/test/helpers/glassSurfaceCss";

const CSS = readFileSync(path.resolve(__dirname, "../../index.css"), "utf-8");

/** The composited canvas behind the handle at the two ends of the living
 *  fog's range, measured off the shader ramp and the milk veil above it.
 *
 *  NOTE, because it is easy to misread this file as measuring the right
 *  panel: it does not. `.glass-right`'s gradient is a different surface, and
 *  the three files that measure THAT one share their stops through
 *  helpers/glassSurfaceCss.ts. What is shared here is only the blend itself,
 *  so all four files composite the same way. */
const DARKEST_CANVAS = "#D4DBE2";
const LIGHTEST_CANVAS = "#F5F9FD";

/** The ink as it actually lands: alpha composited onto the backdrop. A bare
 *  rgba() is not a colour anyone can measure - the glyph is drawn AT that
 *  alpha over the canvas, so the ratio has to be taken on the composite.
 *
 *  `composite()` is the shared one. This file used to carry a private,
 *  UNROUNDED copy; the difference never exceeded 0.035 of a ratio point here,
 *  but a fourth private copy of the blend is how the four files that measure
 *  alpha in this app drifted into disagreeing about it in the first place. */
function ratioOn(colour: string, backdropHex: string): number {
  const backdrop = parseHex(backdropHex);
  if (!backdrop) throw new Error(`bad backdrop "${backdropHex}"`);
  return contrastRatio(composite(colour, backdrop), backdrop);
}

describe("panel toggle is a bare arrow", () => {
  let sheet: HTMLStyleElement;

  beforeEach(() => {
    sheet = document.createElement("style");
    sheet.textContent = CSS;
    document.head.appendChild(sheet);
  });

  afterEach(() => {
    sheet.remove();
    vi.restoreAllMocks();
  });

  function handle(collapsed: boolean): HTMLElement {
    renderWithQueryClient(
      <PanelToggleButton side="left" collapsed={collapsed} onToggle={vi.fn()} />,
    );
    return screen.getByRole("button");
  }

  it("the stylesheet was actually read (guards the guard)", () => {
    // GROUND CONTROL, and the only one of the four contrast files that was
    // missing it. Every measurement below is taken with this text injected as
    // a stylesheet; if the read ever returned an empty string, jsdom would
    // hand back browser defaults and the ratios would be computed on a colour
    // no rule ever set. The `opacity` sweep at the bottom would go green on
    // zero blocks for the same reason.
    expect(CSS.length).toBeGreaterThan(1000);
    expect(CSS).toContain(".panel-toggle");
  });

  it("the harness produced finite ratios, not NaN", () => {
    // `parseRgba(" 0.55) ")` is the shape of mistake that yields NaN, and NaN
    // compares false against a floor AND against a ceiling - so a file that
    // brackets a value from both sides is exactly the file that never notices.
    // `not.toBe(1)` on its own accepts NaN, which is why finiteness is checked
    // first and separately.
    const rest = getComputedStyle(handle(false)).color;
    for (const canvas of [DARKEST_CANVAS, LIGHTEST_CANVAS]) {
      const r = ratioOn(rest, canvas);
      expect(Number.isFinite(r), `the ratio came out ${r}`).toBe(true);
      expect(r).not.toBe(1);
    }
  });

  it("draws no container of its own", () => {
    // The complaint this closes: the arrow had been put in a box. Both halves
    // of a box are checked - a border with no fill still reads as a frame.
    const style = getComputedStyle(handle(false));
    expect(style.backgroundColor).toBe("rgba(0, 0, 0, 0)");
    expect(style.borderTopWidth).toBe("0px");
    expect(style.borderTopStyle === "none" || style.borderTopWidth === "0px").toBe(
      true,
    );
  });

  it("stays visible with no hover", () => {
    // It used to sit at opacity 0 until a :has() chain lit it up. A control
    // that is invisible until pointed at is a control nobody knows exists.
    const style = getComputedStyle(handle(false));
    expect(style.opacity).toBe("1");
    expect(style.visibility).not.toBe("hidden");
    expect(style.display).not.toBe("none");
  });

  it("is faint at rest but still clears the contrast floor", () => {
    // Both bounds, because either alone is satisfiable by the wrong colour.
    // The lower bound is WCAG 1.4.11 on the DARKEST canvas the fog produces;
    // the upper bound is what keeps it quiet rather than assertive - the same
    // ink at full weight measures over 5:1 and reads as chrome.
    const rest = getComputedStyle(handle(false)).color;
    const worst = ratioOn(rest, DARKEST_CANVAS);
    const best = ratioOn(rest, LIGHTEST_CANVAS);

    expect(worst, `rest ink is below the ${AA_NON_TEXT}:1 floor`).toBeGreaterThanOrEqual(
      AA_NON_TEXT,
    );
    expect(best, "rest ink reads as foreground, not as a quiet affordance")
      .toBeLessThan(AA_NORMAL);
  });

  it("gives the closed handle real weight", () => {
    // The one state that must NOT be faint: with the panel at zero width this
    // glyph is the only route back to it.
    const open = getComputedStyle(handle(false)).color;
    const openRatio = ratioOn(open, DARKEST_CANVAS);
    document.body.innerHTML = "";

    const closed = getComputedStyle(handle(true)).color;
    const closedRatio = ratioOn(closed, DARKEST_CANVAS);

    expect(closedRatio, "the only way back is as faint as an idle affordance").toBeGreaterThan(
      openRatio,
    );
    expect(closedRatio).toBeGreaterThanOrEqual(AA_NORMAL);
  });

  it("keeps a press area larger than the glyph", () => {
    // The box is gone; the hit area is not. A 2px stroke is not a target.
    const style = getComputedStyle(handle(false));
    expect(parseFloat(style.width)).toBeGreaterThanOrEqual(24);
    expect(parseFloat(style.height)).toBeGreaterThanOrEqual(24);
  });

  it("carries no rule that would dim it again", () => {
    // GROUND CONTROL for "stays visible". jsdom does not evaluate media
    // queries, so the one thing getComputedStyle cannot see is a `@media
    // (hover: none)` or reduced-* block re-introducing an opacity below 1 -
    // which is exactly what the old coarse-pointer rule did, and it would have
    // multiplied the new faint ink down to roughly 1.8:1.
    const stripped = CSS.replace(/\/\*[\s\S]*?\*\//g, "");
    const offenders = stripped
      .split("}")
      .filter(
        (block) =>
          /\.panel-toggle[^{]*\{/.test(block) &&
          /opacity:\s*(0|0?\.\d+)\s*(!important)?\s*;/.test(block),
      );
    expect(
      offenders,
      "a rule still sets an opacity below 1 on the handle",
    ).toEqual([]);
  });
});
