/**
 * V11 - the arithmetic behind a free colour picker.
 *
 * The message-contrast presets each ship a measured ratio and are ordered
 * soft < default < high, all at or above AA. A picker without this module
 * would quietly discard that: the first pastel anybody likes lands near 2:1.
 * These tests are what makes the number shown next to the wheel trustworthy.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import path from "path";

import {
  AA_NORMAL,
  contrastRatio,
  hslToRgb,
  luminance,
  nudgeToRatio,
  parseHex,
  toHex,
  verdict,
  wheelToHs,
} from "@/lib/appearance/contrast";

const WHITE = { r: 255, g: 255, b: 255 };
const BLACK = { r: 0, g: 0, b: 0 };

/** The preset blocks as index.css actually declares them. */
const CSS_PATH = path.resolve(__dirname, "..", "..", "index.css");

/**
 * Ratios recorded in index.css's own comment when the presets were built:
 * "soft 6.5/8.1, high 15.2/18.8 :1 - all >= AA, ordered soft < default < high".
 * Default deliberately declares nothing (it is the MessageBubble fallback),
 * so only the two blocks that DO declare colours are measurable here.
 */
const DOCUMENTED_RATIOS = {
  "msg-contrast-soft": { user: 6.5, asst: 8.1 },
  "msg-contrast-high": { user: 15.2, asst: 18.8 },
} as const;

function presetColours(preset: string): Record<string, string> {
  const css = readFileSync(CSS_PATH, "utf-8");
  // Plain indexOf rather than a built RegExp: the escapes needed to write
  // `\.` inside a template literal are exactly the kind of thing that
  // silently degrades into a pattern matching nothing, which is the failure
  // this whole stage is about.
  const opener = `.${preset} {`;
  const start = css.indexOf(opener);
  expect(start, `${preset} is gone from index.css`).toBeGreaterThan(-1);
  const body = css.slice(start + opener.length, css.indexOf("}", start));

  const out: Record<string, string> = {};
  for (const m of body.matchAll(/(--[\w-]+):\s*(#[0-9A-Fa-f]{6})/g)) {
    out[m[1]] = m[2];
  }
  expect(
    Object.keys(out).sort(),
    `${preset} no longer declares all four colours`,
  ).toEqual(["--msg-asst-bg", "--msg-asst-fg", "--msg-user-bg", "--msg-user-fg"]);
  return out;
}

function pairRatio(colours: Record<string, string>, who: "user" | "asst"): number {
  const fg = parseHex(colours[`--msg-${who}-fg`]);
  const bg = parseHex(colours[`--msg-${who}-bg`]);
  expect(fg, `unparseable ${who} foreground`).not.toBeNull();
  expect(bg, `unparseable ${who} background`).not.toBeNull();
  return contrastRatio(fg!, bg!);
}

describe("contrast ratio", () => {
  it("matches the known extremes", () => {
    expect(contrastRatio(BLACK, WHITE)).toBeCloseTo(21, 1);
    expect(contrastRatio(WHITE, WHITE)).toBeCloseTo(1, 5);
  });

  it("does not care which colour is given first", () => {
    const a = parseHex("#3D4A59")!;
    expect(contrastRatio(a, WHITE)).toBeCloseTo(contrastRatio(WHITE, a), 10);
  });

  // The two tests that used to stand here hard-coded the preset hexes and
  // never opened index.css. That made the whole preset table unguarded:
  // shifting a colour, or SWAPPING the soft and high blocks outright, left
  // every test in this stage green. Measured in KADEME 19a, not guessed.
  //
  // These read the declarations index.css actually ships and measure them.
  it.each(Object.entries(DOCUMENTED_RATIOS))(
    "%s measures what index.css declares, not what a test remembered",
    (preset, want) => {
      const colours = presetColours(preset);
      const user = pairRatio(colours, "user");
      const asst = pairRatio(colours, "asst");

      expect(user, `${preset} user pair drifted`).toBeCloseTo(want.user, 0);
      expect(asst, `${preset} assistant pair drifted`).toBeCloseTo(want.asst, 0);
      // The floor the presets were built to. AA_NORMAL is imported rather
      // than spelled, because here the CONTRACT is "at least AA", and the
      // number itself is pinned by the verdict test below.
      expect(user).toBeGreaterThanOrEqual(AA_NORMAL);
      expect(asst).toBeGreaterThanOrEqual(AA_NORMAL);
    },
  );

  it("keeps soft below high, which is the whole point of having two", () => {
    // index.css states the ordering as a promise ("ordered soft < default <
    // high"). Nothing checked it, so the two blocks could be exchanged and
    // the setting would quietly do the opposite of its label.
    const soft = presetColours("msg-contrast-soft");
    const high = presetColours("msg-contrast-high");
    expect(pairRatio(soft, "user")).toBeLessThan(pairRatio(high, "user"));
    expect(pairRatio(soft, "asst")).toBeLessThan(pairRatio(high, "asst"));
  });
});

describe("verdict", () => {
  it("labels the bands the way the presets were judged", () => {
    expect(verdict(contrastRatio(BLACK, WHITE))).toBe("aaa");
    expect(verdict(4.6)).toBe("aa");
    expect(verdict(3.9)).toBe("low");
  });

  it("calls a pretty pastel what it is", () => {
    // The exact case the warning exists for.
    const ratio = contrastRatio(parseHex("#B9D4F0")!, WHITE);
    expect(verdict(ratio)).toBe("low");
  });
});

describe("hex parsing", () => {
  it("accepts long, short, and hash-less forms", () => {
    expect(parseHex("#FFFFFF")).toEqual(WHITE);
    expect(parseHex("fff")).toEqual(WHITE);
    expect(parseHex("  #000  ")).toEqual(BLACK);
  });

  it("refuses anything else instead of guessing", () => {
    for (const bad of ["", "#12", "#GGGGGG", "rgb(0,0,0)", "#1234567"]) {
      expect(parseHex(bad)).toBeNull();
    }
  });

  it("round-trips through toHex", () => {
    expect(parseHex(toHex({ r: 12, g: 200, b: 255 }))).toEqual({
      r: 12, g: 200, b: 255,
    });
  });

  it("clamps out-of-range channels rather than emitting broken hex", () => {
    expect(toHex({ r: -20, g: 999, b: 128 })).toBe("#00ff80");
  });
});

describe("nudgeToRatio", () => {
  it("repairs a low-contrast choice without overshooting it", () => {
    const pastel = parseHex("#B9D4F0")!;
    const fixed = nudgeToRatio(pastel, WHITE);
    expect(contrastRatio(fixed, WHITE)).toBeGreaterThanOrEqual(AA_NORMAL);
    // The floor alone was one-sided until KADEME 19a: jumping straight to
    // black also clears AA, and that is not a repair, it is a replacement.
    // The whole promise is "the result still resembles the choice", so the
    // ceiling is what makes this test about `nudge` rather than about `set`.
    expect(
      contrastRatio(fixed, WHITE),
      "the repair walked past the target instead of stopping at it",
    ).toBeLessThan(AA_NORMAL + 1);
  });

  it("leaves a colour that already passes exactly as it was", () => {
    const ink = parseHex("#0B1219")!;
    expect(nudgeToRatio(ink, WHITE)).toEqual(ink);
  });

  it("moves towards white on a dark background", () => {
    const fixed = nudgeToRatio(parseHex("#2A3340")!, parseHex("#16222F")!);
    expect(luminance(fixed)).toBeGreaterThan(luminance(parseHex("#2A3340")!));
  });
});

describe("colour wheel geometry", () => {
  it("puts saturation at the rim and grey in the middle", () => {
    expect(wheelToHs(0, 0, 100).s).toBe(0);
    expect(wheelToHs(100, 0, 100).s).toBe(1);
  });

  it("rides the edge instead of stopping when dragged past the rim", () => {
    expect(wheelToHs(400, 0, 100).s).toBe(1);
  });

  it("wraps hue rather than returning a negative angle", () => {
    expect(wheelToHs(0, -100, 100).h).toBeCloseTo(270, 0);
  });

  it("converts the primaries correctly", () => {
    expect(toHex(hslToRgb(0, 1, 0.5))).toBe("#ff0000");
    expect(toHex(hslToRgb(120, 1, 0.5))).toBe("#00ff00");
    expect(toHex(hslToRgb(240, 1, 0.5))).toBe("#0000ff");
  });
});
