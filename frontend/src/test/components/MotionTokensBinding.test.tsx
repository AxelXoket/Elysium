/**
 * MotionTokensBinding.test.tsx - the eight easing call sites read the curve
 * from lib/theme/tokens.ts, and the proof runs against the OTHER side.
 *
 * Seven components used to spell `[0.4, 0, 0.2, 1]` by hand (FadeIn,
 * SlideIn, AnimatedList, VariantCarousel, DropZoneOverlay, LockOverlay,
 * VaultGate) and three of them also spelled `0.18`. They read
 * `easeValues.smooth` and `durationSeconds.fast` now, and this file measures
 * the BINDING rather than the value: the expected curve is parsed out of the
 * compiled sheet's `--ease-smooth`, never imported from tokens.ts. So a
 * change to tokens.ts makes every bound call site diverge from the sheet
 * (red), while a call site that still carried the literal would keep
 * matching the sheet (green); that asymmetry is what tells a bound site from
 * an unbound one, and importing the expected value from tokens.ts would
 * have made both look the same.
 *
 * The eighth is CrossFade, which reads the OTHER pair of tokens
 * (`--duration-exit`, `--ease-exit`) because a swap leaves before it
 * arrives. It is the only shared primitive both notebook panels go
 * through, and until this file reached it the 120 ms KURAL 5.5 names could
 * be replaced by any literal with the whole suite green. Its reduced-motion
 * arm is measured here too, for the same reason: the compiled sheet cannot
 * see a motion/react transition, so the root `MotionConfig` proves nothing
 * about a per-component ternary, and `reducedMotion="user"` does not
 * neutralise an opacity tween.
 *
 * The motion elements are swapped for a recorder that renders the plain
 * element and keeps the `transition` prop it was handed, the same device
 * ReducedMotionRoot.test.tsx uses for MotionConfig. motion itself is a
 * dependency here, not the unit under test.
 */
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { mockFetch } from "@/test/mocks/api";
import { loadGlassRightCss } from "@/test/helpers/glassSurfaceCss";
import { parseBezier, parseSheet, resolveVars, rootTokens, timeMs } from "@/test/helpers/compiledSheet";
import { FadeIn } from "@/components/motion/FadeIn";
import { SlideIn } from "@/components/motion/SlideIn";
import { AnimatedList, AnimatedListItem } from "@/components/motion/AnimatedList";
import { VariantCarousel } from "@/components/motion/VariantCarousel";
import { DropZoneOverlay } from "@/components/chat/DropZoneOverlay";
import { LockOverlay } from "@/components/vault/LockOverlay";
import { VaultGate } from "@/components/vault/VaultGate";
import { CrossFade } from "@/components/motion/CrossFade";

type Recorded = { tag: string; transition: unknown };

/** Hoisted: `vi.mock`'s factory is lifted above the imports. */
const recorder = vi.hoisted(() => ({ seen: [] as Array<{ tag: string; transition: unknown }> }));

vi.mock("motion/react", async (importOriginal) => {
  const React = await import("react");
  const actual = await importOriginal<typeof import("motion/react")>();
  // Props motion consumes and a plain element must not receive.
  const MOTION_ONLY = new Set([
    "initial", "animate", "exit", "variants", "transition", "layout", "layoutId",
    "whileHover", "whileTap", "whileFocus", "whileInView", "onAnimationStart",
    "onAnimationComplete", "onUpdate", "custom", "inherit",
  ]);
  const cache = new Map<string, unknown>();
  const make = (tag: string) =>
    React.forwardRef(function Recorder(
      props: Record<string, unknown> & { children?: ReactNode },
      ref: React.Ref<unknown>,
    ) {
      recorder.seen.push({ tag, transition: props.transition });
      const plain: Record<string, unknown> = { ref };
      for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) plain[k] = v;
      return React.createElement(tag, plain, props.children);
    });
  const motion = new Proxy({} as Record<string, unknown>, {
    get: (_target, tag: string) => {
      if (!cache.has(tag)) cache.set(tag, make(tag));
      return cache.get(tag);
    },
  });
  return { ...actual, motion, m: motion };
});

let smooth: readonly number[];
let fastSeconds: number;
let exitCurve: readonly number[];
let exitSeconds: number;

beforeAll(async () => {
  const root = parseSheet(await loadGlassRightCss());
  const table = rootTokens(root);
  const ease = table.get("--ease-smooth");
  const fast = table.get("--duration-fast");
  const easeExit = table.get("--ease-exit");
  const exit = table.get("--duration-exit");
  if (ease === undefined || fast === undefined) {
    throw new Error("the compiled sheet lost --ease-smooth or --duration-fast");
  }
  if (easeExit === undefined || exit === undefined) {
    throw new Error("the compiled sheet lost --ease-exit or --duration-exit");
  }
  smooth = parseBezier(resolveVars(ease, table));
  fastSeconds = (timeMs(resolveVars(fast, table)) ?? NaN) / 1000;
  exitCurve = parseBezier(resolveVars(easeExit, table));
  exitSeconds = (timeMs(resolveVars(exit, table)) ?? NaN) / 1000;
}, 60_000);

beforeEach(() => {
  recorder.seen.length = 0;
});

/** Every bezier a recorded transition carries, whether `ease` is one curve
 *  or a per-keyframe list of curves and keywords. */
function curvesOf(entry: Recorded): number[][] {
  const t = entry.transition as { ease?: unknown } | undefined;
  const ease = t?.ease;
  if (Array.isArray(ease) && ease.every((x) => typeof x === "number")) return [ease as number[]];
  if (Array.isArray(ease)) return ease.filter((x): x is number[] => Array.isArray(x));
  return [];
}

function durationOf(entry: Recorded): number | undefined {
  return (entry.transition as { duration?: number } | undefined)?.duration;
}

function expectCurve(entries: Recorded[], what: string, want: readonly number[]) {
  // GROUND: something was recorded, so a missing curve is a missing binding
  // rather than a component that never rendered a motion element.
  expect(entries.length, `${what} rendered no motion element`).toBeGreaterThan(0);
  const curves = entries.flatMap(curvesOf);
  expect(curves.length, `${what} handed motion no easing curve`).toBeGreaterThan(0);
  for (const curve of curves) {
    expect(curve, `${what} eases with a curve the sheet does not declare`).toEqual([...want]);
  }
}

function expectCurveFromSheet(entries: Recorded[], what: string) {
  expectCurve(entries, what, smooth);
}

/** Render a swap under a stated motion preference and give back what the
 *  pane was handed. The preference is the OS one, read through
 *  `window.matchMedia` exactly as `useReducedMotion` reads it. */
function crossFadeUnder(reduced: boolean): Recorded[] {
  const original = window.matchMedia;
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: reduced && query.includes("prefers-reduced-motion"),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
  try {
    render(<CrossFade pane="a">x</CrossFade>);
  } finally {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: original,
    });
  }
  return recorder.seen.filter((e) => durationOf(e) !== undefined);
}

describe("the house curve comes from the sheet at every call site", () => {
  it("FadeIn", () => {
    render(<FadeIn>x</FadeIn>);
    expectCurveFromSheet(recorder.seen, "FadeIn");
  });

  it("SlideIn", () => {
    render(<SlideIn>x</SlideIn>);
    expectCurveFromSheet(recorder.seen, "SlideIn");
  });

  it("AnimatedList item, and its duration is --duration-fast", () => {
    render(
      <AnimatedList>
        <AnimatedListItem>x</AnimatedListItem>
      </AnimatedList>,
    );
    const items = recorder.seen.filter((e) => durationOf(e) !== undefined);
    expectCurveFromSheet(items, "AnimatedListItem");
    expect(items.map(durationOf)).toEqual([fastSeconds]);
  });

  it("VariantCarousel, and its duration is --duration-fast", () => {
    render(
      <VariantCarousel paneKey="a" direction={1} animateEnter>
        x
      </VariantCarousel>,
    );
    expectCurveFromSheet(recorder.seen, "VariantCarousel");
    expect(recorder.seen.map(durationOf)).toEqual([fastSeconds]);
  });

  it("DropZoneOverlay, and its duration is --duration-fast", () => {
    render(<DropZoneOverlay />);
    expectCurveFromSheet(recorder.seen, "DropZoneOverlay");
    expect(recorder.seen.map(durationOf)).toEqual([fastSeconds]);
  });

  it("LockOverlay", () => {
    render(<LockOverlay onCommit={() => {}} onDone={() => {}} />);
    expectCurveFromSheet(recorder.seen, "LockOverlay");
  });

  it("VaultGate", async () => {
    mockFetch({});
    renderWithQueryClient(
      <VaultGate>
        <span>the app</span>
      </VaultGate>,
    );
    await screen.findByText("the app");
    expectCurveFromSheet(recorder.seen, "VaultGate");
  });

  it("CrossFade, on the exit pair rather than the smooth one", () => {
    // The eighth call site, and the one shared by two panels. A swap
    // leaves before it arrives, so it reads `--duration-exit` and
    // `--ease-exit`; the seven above read the smooth pair. Both halves are
    // asserted against the SHEET, so replacing either token reference in
    // the component with the literal it resolves to today leaves the
    // component matching the sheet by luck and this file red the moment
    // the sheet moves - which is the asymmetry this file exists for.
    const panes = crossFadeUnder(false);

    expectCurve(panes, "CrossFade", exitCurve);
    expect(panes.map(durationOf)).toEqual([exitSeconds]);
  });
});

describe("the swap collapses to nothing when the reader asked for it", () => {
  it("takes no time at all under prefers-reduced-motion", () => {
    // The per-component half, and nothing else can see it. The compiled
    // sheet has no view of a motion/react transition, and the root
    // `MotionConfig reducedMotion="user"` strips transform and layout
    // while leaving an opacity tween exactly as it was - so without this
    // row a reader who asked the OS to stop animation still gets a 120 ms
    // fade on every undo confirmation in two panels, with a green suite.
    const panes = crossFadeUnder(true);

    expect(panes.length, "the swap rendered no pane to measure").toBe(1);
    expect(panes.map(durationOf), "the swap still animates").toEqual([0]);
  });

  it("GROUND: the same swap at the default preference takes the exit time", () => {
    // Without this the row above would also pass on a component that had
    // stopped animating for everybody, which is a different defect wearing
    // the same green.
    const panes = crossFadeUnder(false);

    expect(panes.map(durationOf)).toEqual([exitSeconds]);
    expect(exitSeconds, "the sheet's exit duration is zero").toBeGreaterThan(0);
  });
});
