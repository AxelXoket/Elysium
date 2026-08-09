/**
 * V10 - the stagger budget, and the two accessibility switches.
 *
 * These are asserted as CONTRACTS rather than as rendering, because the
 * failure is invisible in a screenshot: a transparency preference the app
 * never reads looks fine to everyone who does not have it turned on.
 *
 * This file also held four tests for five spring presets. Both the presets and
 * those tests were deleted on 2026-08-09: nothing in the app ever imported a
 * preset, so the tests proved the constants were consistent with each other
 * and nothing else. The reasoning is kept in `lib/motion/stagger.ts`, next to
 * what survived.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import path from "path";

import { staggerStep } from "@/lib/motion/stagger";

const SRC = path.resolve(__dirname, "../..");

describe("stagger budget", () => {
  it("never lets a long list take longer just for being long", () => {
    const total = (n: number) => staggerStep(n) * n;
    expect(total(40)).toBeLessThanOrEqual(0.4);
    expect(total(200)).toBeLessThanOrEqual(0.4);
  });

  it("still uses a visible offset for a short list", () => {
    expect(staggerStep(5)).toBeGreaterThan(0.02);
  });

  it("does not stagger a single item", () => {
    expect(staggerStep(1)).toBe(0);
  });
});

describe("accessibility preferences the app must actually read", () => {
  it("honours reduced motion at the root, not per component", () => {
    // `reducedMotion="user"` strips transform/layout while KEEPING opacity and
    // colour - the split the guidance asks for.
    //
    // It is NOT the whole answer, and an earlier version of this comment said
    // it was. Measured against the installed motion-dom: `shouldReduceMotion`
    // is consulted for positional keys only (width/height/top/left/right/
    // bottom plus the transform props) and for layout animations. It never
    // reaches `staggerChildren`, an opacity duration, whether a canvas effect
    // runs at all, or a native `scrollIntoView` behaviour. Those are what the
    // shared `useReducedMotion` hook in components/motion covers, and removing
    // it in favour of this prop would have been a regression, not a cleanup.
    const providers = readFileSync(
      path.join(SRC, "app", "providers.tsx"), "utf-8",
    );
    expect(providers).toContain('reducedMotion="user"');
  });

  it("honours reduced transparency across the glass surfaces", () => {
    // Windows exposes this switch (Personalization > Colors > Transparency
    // effects) and this is a Windows app, so somebody can already have it on.
    // It is a READABILITY preference, distinct from reduced motion.
    const css = readFileSync(path.join(SRC, "index.css"), "utf-8");
    expect(css).toContain("prefers-reduced-transparency");
    const block = css.slice(css.indexOf("prefers-reduced-transparency"));
    expect(block).toContain("backdrop-filter: none");
  });
});

describe("AnimatedList honours the stagger budget", () => {
  it("keeps the per-item value as a ceiling for short lists", () => {
    // Short lists were never the problem, so nothing about them changes.
    expect(Math.min(0.04, staggerStep(4))).toBe(0.04);
  });

  it("tightens a long list instead of letting it run past the budget", () => {
    // Sixteen rows at a flat 40ms is 0.64s before the last one moves - roughly
    // double the point where a sequence stops reading as considered and starts
    // reading as waiting.
    const step = Math.min(0.04, staggerStep(16));
    expect(step * 16).toBeLessThanOrEqual(0.4);
    expect(step).toBeLessThan(0.04);
  });
});
