/**
 * AnimatedListItemMemo.test.tsx - what a list item costs when nothing about
 * it changed.
 *
 * `AnimatedList` and `AnimatedListItem` sit on the streaming path: the parent
 * re-renders on every flush, and until the memo landed each flush re-ran all
 * 200 item bodies for lists whose rows had not moved. The file already
 * hoisted its variants and its transition for that reason; the component
 * bodies themselves were the part still running.
 *
 * THE FROZEN BUDGET (written against the pre-memo tree, not fitted to the
 * fix), at N = 200:
 *
 *   B1  A parent state change that leaves every item's props referentially
 *       unchanged re-runs AT MOST K = 0 of the 200 item bodies.
 *   B2  That same run must re-render the parent at least once, so B1 is
 *       measured against a parent that actually updated.
 *   B3  Under the same change the container body re-runs at most 0 times,
 *       when its own props are referentially unchanged.
 *   B4  When one item's props DO change, that item's body re-runs and the
 *       DOM shows the new value. A comparator that blocks a correct render
 *       fails HERE, not at B1 - the two are different failures.
 *   B5  A parent that rebuilds the child element every render re-runs
 *       200 of 200, memo or not.
 *
 * Pre-fix measurement: B1 = 200/200 and B3 = 1/1, both violated.
 *
 * WHY THE PARENT HERE IS SYNTHETIC. Read first, then written down: all four
 * call sites (MessageList, ModelPanel, ChatList, CharacterList) build a fresh
 * child element inside the same `.map()` that builds the item, and a fresh
 * element is never shallow-equal to the last one. Measured through one of
 * those parents, this memo cannot be observed at all - the number would be
 * 200 before the fix and 200 after it, which measures the parent and not the
 * component this file is about. B5 is that shape, kept as a case so the
 * limitation is a number somebody can re-run rather than a warning in prose.
 *
 * The probe counts the REAL `useReducedMotion`, which both components call
 * once per body run. Nothing here asserts on the text of a source file.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { useMemo, useState, type ReactNode } from "react";
import { AnimatedList, AnimatedListItem } from "@/components/motion/AnimatedList";

/** Hoisted: `vi.mock`'s factory is lifted above the imports. */
const probe = vi.hoisted(() => ({ bodies: 0 }));

vi.mock("@/components/motion/ReducedMotion", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/components/motion/ReducedMotion")>();
  return {
    ...actual,
    useReducedMotion: () => {
      probe.bodies += 1;
      return actual.useReducedMotion();
    },
  };
});

/** B1's list length. */
const BUDGET_N = 200;
/** B1's ceiling: an item whose props did not move must not run again. */
const BUDGET_K = 0;

/**
 * A parent that re-renders on demand and hands every item the SAME child
 * element object it handed it last time.
 *
 * The item ELEMENTS are rebuilt on every render on purpose. Memoising those
 * too would let React bail out on element identity alone, one level above
 * `memo`, and the budget would read green with the memo deleted - measured,
 * and it is the trap this harness exists to avoid.
 */
function StableParent({ onReady }: { onReady: (bump: () => void) => void }) {
  const [tick, setTick] = useState(0);
  const kids = useMemo(
    () =>
      Array.from({ length: BUDGET_N }, (_, i) => (
        <span key={i}>Row {i + 1}</span>
      )),
    [],
  );
  onReady(() => setTick((t) => t + 1));
  return (
    <div data-testid="parent" data-tick={tick}>
      {kids.map((kid, i) => (
        <AnimatedListItem key={i}>{kid}</AnimatedListItem>
      ))}
    </div>
  );
}

/** B5: the shipped shape. Same parent, but the child is built inline. */
function RebuildingParent({ onReady }: { onReady: (bump: () => void) => void }) {
  const [tick, setTick] = useState(0);
  onReady(() => setTick((t) => t + 1));
  return (
    <div data-testid="parent" data-tick={tick}>
      {Array.from({ length: BUDGET_N }, (_, i) => (
        <AnimatedListItem key={i}>
          <span>Row {i + 1}</span>
        </AnimatedListItem>
      ))}
    </div>
  );
}

/** B3: the container, with a children array that does not move. */
function StableContainerParent({
  onReady,
}: {
  onReady: (bump: () => void) => void;
}) {
  const [tick, setTick] = useState(0);
  const kids = useMemo<ReactNode>(
    () =>
      Array.from({ length: BUDGET_N }, (_, i) => (
        <span key={i}>Row {i + 1}</span>
      )),
    [],
  );
  onReady(() => setTick((t) => t + 1));
  return (
    <div data-testid="parent" data-tick={tick}>
      <AnimatedList>{kids}</AnimatedList>
    </div>
  );
}

/**
 * Mount `Parent`, then apply one state change that touches nothing an item
 * reads, and report how many component bodies that change cost.
 */
function costOfAnIrrelevantParentUpdate(
  Parent: (props: { onReady: (bump: () => void) => void }) => ReactNode,
): { bodies: number; tickBefore: string | null; tickAfter: string | null } {
  let bump: () => void = () => {};
  render(<Parent onReady={(fn) => (bump = fn)} />);
  const tickBefore = screen.getByTestId("parent").getAttribute("data-tick");

  probe.bodies = 0;
  act(() => bump());

  return {
    bodies: probe.bodies,
    tickBefore,
    tickAfter: screen.getByTestId("parent").getAttribute("data-tick"),
  };
}

describe("a list item does not re-run when nothing about it changed", () => {
  beforeEach(() => {
    probe.bodies = 0;
  });

  afterEach(() => {
    probe.bodies = 0;
  });

  it("POSITIVE CONTROL: the probe sees the bodies when they do run", () => {
    // Without this, a budget of "zero bodies" is also met by a counter wired
    // to nothing at all. First paint has no memo to hide behind, so all 200
    // must be visible here.
    render(
      <div>
        {Array.from({ length: BUDGET_N }, (_, i) => (
          <AnimatedListItem key={i}>
            <span>Row {i + 1}</span>
          </AnimatedListItem>
        ))}
      </div>,
    );
    expect(probe.bodies).toBeGreaterThanOrEqual(BUDGET_N);
  });

  it("B1/B2: 200 items with unchanged props cost nothing when the parent updates", () => {
    const { bodies, tickBefore, tickAfter } = costOfAnIrrelevantParentUpdate(
      StableParent,
    );

    // B2 first: a parent that did not actually update would score a free
    // zero on B1 and prove nothing.
    expect(tickAfter, "the parent never re-rendered").not.toBe(tickBefore);
    expect(bodies, "item bodies re-ran for a change none of them read")
      .toBeLessThanOrEqual(BUDGET_K);
  });

  it("B3: the container itself is not re-run either", () => {
    const { bodies, tickBefore, tickAfter } = costOfAnIrrelevantParentUpdate(
      StableContainerParent,
    );

    expect(tickAfter, "the parent never re-rendered").not.toBe(tickBefore);
    expect(bodies, "the container body re-ran for a change it does not read")
      .toBeLessThanOrEqual(BUDGET_K);
  });

  it("B5: a parent that rebuilds the child element defeats the memo entirely", () => {
    // The dependency set, stated as an experiment. `children` is the prop
    // that decides this, and a fresh element makes the shallow comparison
    // miss every time. This is why the memo is inert at today's four call
    // sites, and it is a measured number rather than a caveat in a comment.
    const { bodies, tickBefore, tickAfter } = costOfAnIrrelevantParentUpdate(
      RebuildingParent,
    );

    expect(tickAfter, "the parent never re-rendered").not.toBe(tickBefore);
    expect(bodies, "a fresh child element should not be shallow-equal")
      .toBe(BUDGET_N);
  });
});

describe("the memo still lets the correct render through", () => {
  beforeEach(() => {
    probe.bodies = 0;
  });

  it("B4: an item whose props change re-runs and shows the new value", () => {
    // The other half of B1, and a DIFFERENT failure: B1 goes red when the
    // memo is missing, this goes red when the comparison is too aggressive -
    // a hand-written comparator that ignores a prop, for instance. The
    // component takes the default shallow comparison precisely so this holds.
    function Swapping({ onReady }: { onReady: (bump: () => void) => void }) {
      const [n, setN] = useState(1);
      onReady(() => setN((v) => v + 1));
      return (
        <AnimatedListItem className={`row-${n}`}>
          <span>Body {n}</span>
        </AnimatedListItem>
      );
    }

    let bump: () => void = () => {};
    const { container } = render(
      <Swapping onReady={(fn) => (bump = fn)} />,
    );

    // GROUND CONTROL: the first value is on screen, so the change below is a
    // real change and not an assertion against an empty render.
    expect(screen.getByText("Body 1")).toBeInTheDocument();
    expect(container.querySelector(".row-1")).not.toBeNull();

    probe.bodies = 0;
    act(() => bump());

    expect(probe.bodies, "a changed item was blocked from re-rendering")
      .toBeGreaterThan(0);
    expect(screen.getByText("Body 2")).toBeInTheDocument();
    expect(screen.queryByText("Body 1")).toBeNull();
    // The className prop moved too, so this is not one prop carrying two
    // assertions.
    expect(container.querySelector(".row-2")).not.toBeNull();
    expect(container.querySelector(".row-1")).toBeNull();
  });

  it("B4: reduced motion still reaches an item that is not re-rendering", () => {
    // A memo that froze the item would also freeze its accessibility state.
    // The hook is a subscription inside the body, so a preference change has
    // to re-run that body even though no prop moved - if this ever stops
    // being true, the memo has taken something it must not take.
    const listeners = new Set<() => void>();
    let reduced = false;
    const original = window.matchMedia;
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: (query: string) => ({
        get matches() {
          return reduced;
        },
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: (_: string, cb: () => void) => listeners.add(cb),
        removeEventListener: (_: string, cb: () => void) =>
          listeners.delete(cb),
        dispatchEvent: () => false,
      }),
    });

    try {
      const kid = <span>Row 1</span>;
      function Host() {
        return <AnimatedListItem>{kid}</AnimatedListItem>;
      }
      render(<Host />);

      probe.bodies = 0;
      act(() => {
        reduced = true;
        listeners.forEach((cb) => cb());
      });

      expect(
        probe.bodies,
        "a reduced-motion change never reached the item body",
      ).toBeGreaterThan(0);
    } finally {
      Object.defineProperty(window, "matchMedia", {
        writable: true,
        configurable: true,
        value: original,
      });
    }
  });
});
