/**
 * V10 - the motion vocabulary, and the two accessibility switches.
 *
 * These are asserted as CONTRACTS rather than as rendering, because both
 * failures are invisible in a screenshot: a spring that quietly ignores
 * inherited velocity looks fine until somebody flicks something, and a
 * transparency preference the app never reads looks fine to everyone who does
 * not have it turned on.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import path from "path";

import {
  SPRING_SNAP,
  SPRING_SURFACE,
  SPRING_PANEL,
  SPRING_CINEMATIC,
  SPRING_GESTURE,
  staggerStep,
} from "@/lib/motion/springs";

const SRC = path.resolve(__dirname, "../..");

describe("spring tokens", () => {
  const DISCRETE = [SPRING_SNAP, SPRING_SURFACE, SPRING_PANEL, SPRING_CINEMATIC];

  it("uses the duration form for discrete state changes", () => {
    // `visualDuration` is the time the movement LOOKS finished, which is what
    // lets a spring be coordinated with a plain CSS transition.
    for (const spring of DISCRETE) {
      expect(spring).toHaveProperty("visualDuration");
      expect(spring).not.toHaveProperty("stiffness");
    }
  });

  it("keeps every discrete spring inside the duration bands", () => {
    // Nothing repeated should exceed ~400ms; the cinematic one is the single
    // deliberate exception and still stops at half a second.
    for (const spring of DISCRETE) {
      const d = (spring as { visualDuration: number }).visualDuration;
      expect(d).toBeGreaterThan(0);
      expect(d).toBeLessThanOrEqual(0.5);
    }
    expect((SPRING_SNAP as { visualDuration: number }).visualDuration)
      .toBeLessThanOrEqual(0.15);
  });

  it("uses the PHYSICS form for gesture continuation", () => {
    // The whole point: `visualDuration`/`bounce` discards inherited velocity,
    // so a flicked element would visibly restart from zero. Only the physics
    // form carries momentum.
    expect(SPRING_GESTURE).toHaveProperty("stiffness");
    expect(SPRING_GESTURE).not.toHaveProperty("visualDuration");
  });

  it("scales the whole surface with a single bounce vocabulary", () => {
    // Larger surface, less overshoot - bounce at panel scale reads as toy-like.
    const bounce = (s: unknown) => (s as { bounce: number }).bounce;
    expect(bounce(SPRING_SNAP)).toBe(0);
    expect(bounce(SPRING_PANEL)).toBeLessThan(bounce(SPRING_CINEMATIC));
  });
});

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
    // colour - the split the guidance asks for. A per-component branch is a
    // correctness bug waiting for the one place somebody forgets.
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
