/**
 * PanelToggleAppearance.test.tsx - the collapse handle is an arrow, not a box.
 *
 * The owner's brief, verbatim in effect: no visible area around the arrow, an
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
import { contrastRatio, parseHex, type Rgb } from "@/lib/appearance/contrast";

const CSS = readFileSync(path.resolve(__dirname, "../../index.css"), "utf-8");

/** The composited canvas behind the handle at the two ends of the living
 *  fog's range, measured off the shader ramp and the milk veil above it. */
const DARKEST_CANVAS = "#D4DBE2";
const LIGHTEST_CANVAS = "#F5F9FD";

/** WCAG SC 1.4.11: a non-text UI component needs 3:1 against its backdrop. */
const UI_COMPONENT_FLOOR = 3;

function parseRgba(value: string): [number, number, number, number] {
  const m = value.match(/rgba?\(([^)]+)\)/);
  if (!m) throw new Error(`not an rgb(a) colour: "${value}"`);
  const parts = m[1].split(",").map((p) => Number(p.trim()));
  return [parts[0], parts[1], parts[2], parts[3] ?? 1];
}

/** The ink as it actually lands: alpha composited onto the backdrop. A bare
 *  rgba() is not a colour anyone can measure - the glyph is drawn AT that
 *  alpha over the canvas, so the ratio has to be taken on the composite. */
function flatten(colour: string, backdrop: Rgb): Rgb {
  const [r, g, b, a] = parseRgba(colour);
  const mix = (ink: number, back: number) => ink * a + back * (1 - a);
  return {
    r: mix(r, backdrop.r),
    g: mix(g, backdrop.g),
    b: mix(b, backdrop.b),
  };
}

function ratioOn(colour: string, backdropHex: string): number {
  const backdrop = parseHex(backdropHex);
  if (!backdrop) throw new Error(`bad backdrop "${backdropHex}"`);
  return contrastRatio(flatten(colour, backdrop), backdrop);
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

    expect(worst, `rest ink is below the ${UI_COMPONENT_FLOOR}:1 floor`).toBeGreaterThanOrEqual(
      UI_COMPONENT_FLOOR,
    );
    expect(best, "rest ink reads as foreground, not as a quiet affordance").toBeLessThan(4.5);
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
    expect(closedRatio).toBeGreaterThanOrEqual(4.5);
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
