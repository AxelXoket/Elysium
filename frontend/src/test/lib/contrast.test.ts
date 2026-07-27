/**
 * V11 - the arithmetic behind a free colour picker.
 *
 * The message-contrast presets each ship a measured ratio and are ordered
 * soft < default < high, all at or above AA. A picker without this module
 * would quietly discard that: the first pastel anybody likes lands near 2:1.
 * These tests are what makes the number shown next to the wheel trustworthy.
 */
import { describe, it, expect } from "vitest";

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

describe("contrast ratio", () => {
  it("matches the known extremes", () => {
    expect(contrastRatio(BLACK, WHITE)).toBeCloseTo(21, 1);
    expect(contrastRatio(WHITE, WHITE)).toBeCloseTo(1, 5);
  });

  it("does not care which colour is given first", () => {
    const a = parseHex("#3D4A59")!;
    expect(contrastRatio(a, WHITE)).toBeCloseTo(contrastRatio(WHITE, a), 10);
  });

  it("reproduces the SOFT preset's documented ratio", () => {
    // #3D4A59 on #EFF3F8 is the soft assistant pair, recorded as ~8.1:1 when
    // the presets were built. Reproducing it here is what proves this module
    // measures the same thing the theme was measured with.
    const ratio = contrastRatio(parseHex("#3D4A59")!, parseHex("#EFF3F8")!);
    expect(ratio).toBeGreaterThan(7.5);
    expect(ratio).toBeLessThan(8.7);
  });

  it("reproduces the HIGH preset's documented ratio", () => {
    const ratio = contrastRatio(parseHex("#0B1219")!, WHITE);
    expect(ratio).toBeGreaterThan(18);
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
  it("repairs a low-contrast choice", () => {
    const pastel = parseHex("#B9D4F0")!;
    const fixed = nudgeToRatio(pastel, WHITE);
    expect(contrastRatio(fixed, WHITE)).toBeGreaterThanOrEqual(AA_NORMAL);
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
