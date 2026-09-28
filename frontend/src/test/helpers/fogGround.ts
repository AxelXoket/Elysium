/**
 * fogGround.ts - what the living mist paints behind a side panel, as a
 * ground a contrast reading can be taken on.
 *
 * With the ambient mist on, which is the default, `PanelMist` puts an
 * opaque WebGL canvas inside the panel at z-index -1: above the panel's own
 * gradient and below its content. The panel's gradient then never reaches
 * the eye, and the ground every line is read on is the mist's veil over
 * whatever the fog paints at that spot. The fog moves, so a line has to
 * clear its floor where the fog is darkest (dark words) and the scrollbar
 * has to stand out where it is lightest (a dark bar on the sidebar).
 *
 * No reader can see the canvas: jsdom has no WebGL, and the gate takes the
 * canvas back out when the context fails. So the fog is evaluated from the
 * shader's own exported ramp and tuning constants, over every value its
 * three free inputs can take, and the veil is read out of the compiled
 * sheet. Retuning the fog or repainting the veil moves every reading that
 * stands on this.
 */
import type { Root } from "postcss";

import { luminance, type Rgb } from "@/lib/appearance/contrast";
import {
  FOG_RAMP_BASE,
  FOG_RAMP_STEPS,
  HUE_TRAVEL,
  LIFT,
  LIFT_HI,
  LIFT_LO,
  LIFT_REACH,
  PANEL_BANDS,
  RAMP_COMPRESS,
  WEATHER_AMOUNT,
} from "@/components/backdrop/fogShader";
import { declarationOnSelector } from "@/test/helpers/compiledSheet";
import { composite, parseLayer } from "@/test/helpers/glassSurfaceCss";

/** `smoothstep` as GLSL defines it, which is what the fog's ramp and its
 *  lift are both written in. */
export function smoothstep(lo: number, hi: number, t: number): number {
  const x = Math.min(Math.max((t - lo) / (hi - lo), 0), 1);
  return x * x * (3 - 2 * x);
}

/** The shader's `ramp()`, evaluated from the table the module exports.
 *  Not a copy of the numbers: the colours and the thresholds are its own,
 *  and css-contract.test.ts proves they are the ones the GLSL it generates
 *  really carries. */
export function fogRamp(t: number): Rgb {
  const clamped = Math.min(Math.max(t, 0), 1);
  let c = [...FOG_RAMP_BASE] as number[];
  for (const step of FOG_RAMP_STEPS) {
    const f = smoothstep(step.lo, step.hi, clamped);
    c = c.map((channel, i) => channel + (step.colour[i] - channel) * f);
  }
  return { r: c[0] * 255, g: c[1] * 255, b: c[2] * 255 };
}

/** The lightest or the darkest pixel one panel's fog can paint, swept from
 *  the shader's own constants over every value its three free inputs can
 *  take.
 *
 *  `d` is a diagonal tendency moved by the weather cell, `v` is the
 *  normalised fog density, and both the base colour and the billow lift
 *  are `ramp()` of those. All three inputs are `smoothstep`ed to [0, 1]
 *  before they get here, so a grid over the unit cube is the whole of
 *  their range and the extreme over it is the answer. */
export function fogExtreme(
  band: readonly [number, number],
  pick: "lightest" | "darkest",
  steps = 60,
): Rgb {
  const [lo, hi] = band;
  const span = hi - lo;
  let best: Rgb | null = null;
  const better = (c: Rgb, than: Rgb): boolean =>
    pick === "lightest" ? luminance(c) > luminance(than) : luminance(c) < luminance(than);
  for (let i = 0; i <= steps; i += 1) {
    const diag = i / steps;
    for (let j = 0; j <= steps; j += 1) {
      const w3 = j / steps;
      const d = lo + span * (0.5 + (diag - 0.5) * RAMP_COMPRESS + (w3 - 0.5) * WEATHER_AMOUNT);
      for (let k = 0; k <= steps; k += 1) {
        const v = k / steps;
        const base = fogRamp(d + (v - 0.5) * HUE_TRAVEL * span);
        const lifted = fogRamp(Math.min(d + LIFT_REACH * span, hi));
        const weight = smoothstep(LIFT_LO, LIFT_HI, v) * LIFT;
        const mixed: Rgb = {
          r: base.r + (lifted.r - base.r) * weight,
          g: base.g + (lifted.g - base.g) * weight,
          b: base.b + (lifted.b - base.b) * weight,
        };
        if (!best || better(mixed, best)) best = mixed;
      }
    }
  }
  if (!best) throw new Error("the fog sweep produced nothing");
  return { r: Math.round(best.r), g: Math.round(best.g), b: Math.round(best.b) };
}

/** The lightest pixel one panel's fog can paint. */
export const fogCeiling = (band: readonly [number, number], steps = 60): Rgb =>
  fogExtreme(band, "lightest", steps);

const FLOORS = new Map<string, Rgb>();

/** The darkest pixel one panel's fog can paint. Swept once per band and
 *  grid: every line of a sweep stands on the same number. */
export function fogFloor(band: readonly [number, number], steps = 60): Rgb {
  const key = `${band[0]}:${band[1]}:${steps}`;
  const known = FLOORS.get(key);
  if (known) return known;
  const floor = fogExtreme(band, "darkest", steps);
  FLOORS.set(key, floor);
  return floor;
}

/** The veil `PanelMist` lays over one panel's fog, as the sheet declares it. */
export const panelMistVeil = (side: "left" | "right"): string =>
  `.panel-mist-${side} .panel-mist-veil`;

/**
 * The darkest grounds the mist can put under one panel's words: each stop
 * of that panel's veil, read from the compiled sheet, laid over the darkest
 * pixel its fog can paint. The veil is a two-stop gradient, and a line can
 * sit anywhere down it, so both stops are grounds; between them the
 * composite only moves from one to the other.
 *
 * Throws when the sheet no longer declares the veil where this reads it, or
 * paints it as something other than one gradient of colour stops: a mist
 * this cannot model is a red reading, not a ground quietly left out.
 */
export function panelMistGrounds(root: Root, side: "left" | "right"): Rgb[] {
  const selector = panelMistVeil(side);
  const lift = declarationOnSelector(root, ".panel-mist", /^z-index$/);
  if (lift !== "-1") {
    throw new Error(
      `.panel-mist is no longer at z-index -1 (got ${lift}), so the veil is not the ground ` +
        "under the panel's words this models",
    );
  }
  const veil = declarationOnSelector(root, selector, /^background$/);
  if (veil === null) throw new Error(`${selector} declares no background; the veil moved`);
  const layer = parseLayer(veil);
  if (layer.shape.kind !== "linear") {
    throw new Error(`${selector} is no longer one linear gradient: ${veil}`);
  }
  const floor = fogFloor(PANEL_BANDS[side]);
  return layer.stops.map((stop) => composite(stop.colour, floor));
}
