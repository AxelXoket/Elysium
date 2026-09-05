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
 *
 * Two more assertions left on 2026-09-03, and this time they MOVED rather
 * than went, because both of them were reading source text:
 *
 *   - the reduced-motion one looked for `reducedMotion="user"` in the text of
 *     `app/providers.tsx`, where that string also appears inside the comment
 *     explaining the prop. Measured: deleting the prop and leaving the comment
 *     kept it green. It is `test/components/ReducedMotionRoot.test.tsx` now,
 *     reading the prop off the component that receives it.
 *   - the two `AnimatedList` cases recomputed the component's own clamp inside
 *     the assertion and never imported the component. Measured: the file was
 *     deleted outright and both stayed green. They are
 *     `test/components/AnimatedListStagger.test.tsx` now, asserting on the
 *     value that reaches motion.
 *
 * What is left here is what genuinely is a contract rather than a render.
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

  it("keeps a short list step above 0.02s, and at the 0.05s cap", () => {
    // One-sided until KADEME 19a. `staggerStep` is `min(cap, total/count)`
    // and at five items the CAP is what wins, so the lower bound alone let
    // the cap regress freely: at cap 1.0 this returns 0.07 and stays green.
    // The upper side is the cap itself, so pin it here where it binds.
    expect(staggerStep(5)).toBeGreaterThan(0.02);
    expect(staggerStep(5), "the per-item cap moved").toBeCloseTo(0.05, 5);
  });

  it("does not stagger a single item", () => {
    expect(staggerStep(1)).toBe(0);
  });
});

describe("accessibility preferences the app must actually read", () => {
  it("honours reduced transparency across the glass surfaces", () => {
    // Windows exposes this switch (Personalization > Colors > Transparency
    // effects) and this is a Windows app, so somebody can already have it on.
    // It is a READABILITY preference, distinct from reduced motion.
    //
    // KADEME 19a had this test down for deletion as a duplicate of the
    // css-contract block. Measured before deleting, and the plan was wrong:
    // css-contract checks WHICH selectors the block lists and that they are
    // all real, but `backdrop-filter: none` is asserted nowhere else in the
    // suite. Delete this and the block could list every blurred surface
    // correctly while turning off nothing at all. It stays.
    const css = readFileSync(path.join(SRC, "index.css"), "utf-8");
    expect(css).toContain("prefers-reduced-transparency");
    const block = css.slice(css.indexOf("prefers-reduced-transparency"));
    expect(block).toContain("backdrop-filter: none");
  });
});
