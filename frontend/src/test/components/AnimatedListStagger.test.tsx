/**
 * AnimatedListStagger.test.tsx - the stagger budget, measured on the value
 * `AnimatedList` actually hands to motion.
 *
 * This replaces a describe block in `test/lib/motionTokens.test.ts` that was
 * named after this component and never imported it. It recomputed the
 * component's own clamp inside the assertion and wrote the `0.04` default out
 * by hand, so deleting the clamp from the component, or changing the default
 * prop, left both cases green. The component was deleted outright during a
 * measurement pass and the block stayed 7/7 green.
 *
 * What is asserted here is the `staggerChildren` that reaches `motion` on the
 * `visible` variant. `motion.div` is swapped for a plain div that records the
 * variants it was handed; everything above it - the default prop, the clamp,
 * the reduced-motion branch - is the real component.
 *
 * The children are plain spans rather than AnimatedListItem: `Children.count`
 * is all the container reads, and a nested motion element would put a second
 * variants object in the recorder for no gain.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { AnimatedList } from "@/components/motion/AnimatedList";
import { staggerStep } from "@/lib/motion/stagger";

/** Hoisted: `vi.mock`'s factory is lifted above the imports. */
const recorder = vi.hoisted(() => ({ variants: [] as unknown[] }));

vi.mock("motion/react", async (importOriginal) => {
  const React = await import("react");
  const actual = await importOriginal<typeof import("motion/react")>();
  return {
    ...actual,
    motion: {
      div: ({
        variants,
        children,
        className,
      }: {
        variants?: unknown;
        children?: ReactNode;
        className?: string;
      }) => {
        recorder.variants.push(variants);
        return React.createElement("div", { className }, children);
      },
    },
  };
});

/** The per-item ceiling short lists get, written out because pinning it is
 *  the whole job: importing the component's own default would make this
 *  assertion agree with any value the default happened to hold. */
const PER_ITEM_CAP = 0.04;
/** The total the sequence may occupy, in seconds. */
const TOTAL_BUDGET = 0.4;

/** Render the real container with `count` children and return the
 *  `staggerChildren` that reached motion. */
function staggerChildrenFor(count: number): number {
  recorder.variants.length = 0;
  render(
    <AnimatedList>
      {Array.from({ length: count }, (_, i) => (
        <span key={i}>Row {i + 1}</span>
      ))}
    </AnimatedList>,
  );

  const variants = recorder.variants[0] as
    | { visible?: { transition?: { staggerChildren?: unknown } } }
    | undefined;
  const step = variants?.visible?.transition?.staggerChildren;
  // POSITIVE CONTROL, inline because every case below depends on it: a
  // recorder that caught nothing would make `undefined <= 0.4` the assertion,
  // and this whole file would report on an empty render.
  expect(step, "no staggerChildren reached motion").toBeTypeOf("number");
  return step as number;
}

describe("AnimatedList honours the stagger budget", () => {
  beforeEach(() => {
    recorder.variants.length = 0;
  });

  it("POSITIVE CONTROL: the recorder sees the container's variants", () => {
    render(
      <AnimatedList>
        <span>only row</span>
      </AnimatedList>,
    );
    expect(recorder.variants.length, "the motion mock was never rendered")
      .toBeGreaterThan(0);
    expect(recorder.variants[0], "the container passed no variants at all")
      .toBeDefined();
  });

  it("caps the step at 0.04s for a short list", () => {
    // Short lists were never the problem, so nothing about them changes: the
    // per-item ceiling wins and the total budget is nowhere near binding.
    expect(staggerChildrenFor(4)).toBeCloseTo(PER_ITEM_CAP, 10);
  });

  it("fits a sixteen-row list inside the budget by shrinking the step", () => {
    // Sixteen rows at a flat 40ms is 0.64s before the last one moves -
    // roughly double the point where a sequence stops reading as considered
    // and starts reading as waiting.
    const step = staggerChildrenFor(16);

    // The container must be asking `staggerStep`, not carrying its own idea
    // of the budget. Imported, never retyped.
    expect(step, "the container stopped consulting the shared budget")
      .toBeCloseTo(staggerStep(16), 10);
    expect(step * 16).toBeLessThanOrEqual(TOTAL_BUDGET);
    expect(step).toBeLessThan(PER_ITEM_CAP);
  });
});

describe("the stagger budget still answers to reduced motion", () => {
  const original = window.matchMedia;

  afterEach(() => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: original,
    });
    recorder.variants.length = 0;
  });

  it("staggers nothing at all when the reader asked for reduced motion", () => {
    // The clamp is not the only branch on this line, and a mock that only
    // ever sees the default preference would let the reduced-motion arm rot
    // unobserved. GROUND CONTROL is the case above: the same call returns a
    // non-zero step when the preference is off.
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: (query: string) => ({
        matches: query.includes("prefers-reduced-motion"),
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }),
    });

    expect(staggerChildrenFor(16)).toBe(0);
  });
});
