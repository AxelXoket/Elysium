/**
 * holdReaderPlace.test.ts - the reader keeps their place while the column
 * re-wraps.
 *
 * jsdom has no layout, so the rectangles are stubbed. That is not a weakness
 * here: what is under test is the CORRECTION - given that a row moved by N
 * pixels, does `scrollTop` move by N to cancel it - and the browser's job of
 * producing N is exactly the part that is not ours.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { holdReaderPlace } from "@/lib/layout/holdReaderPlace";

const VIEWPORT_TOP = 100;

/** A scroller with `count` rows whose position can be shifted by the test. */
function buildScroller(count = 6) {
  const scroller = document.createElement("div");
  document.body.appendChild(scroller);
  /** How far the whole document has moved since the rows were placed. */
  let shift = 0;
  /** Row i sits 200px apart, starting 40px below the viewport top. */
  const rowTop = (i: number) => VIEWPORT_TOP + 40 + i * 200 + shift;

  scroller.getBoundingClientRect = () =>
    ({ top: VIEWPORT_TOP, left: 0, width: 800, height: 600 }) as DOMRect;

  for (let i = 0; i < count; i += 1) {
    const row = document.createElement("div");
    row.className = "message-bubble-shell";
    row.getBoundingClientRect = () =>
      ({
        top: rowTop(i),
        bottom: rowTop(i) + 180,
        left: 0,
        width: 600,
        height: 180,
      }) as DOMRect;
    scroller.appendChild(row);
  }

  // jsdom leaves scrollTop at 0 and ignores writes on a non-scrolling box, so
  // it is backed by a plain field here.
  let top = 0;
  Object.defineProperty(scroller, "scrollTop", {
    get: () => top,
    set: (v: number) => {
      top = v;
    },
    configurable: true,
  });

  return {
    scroller,
    /** Simulate content above the viewport re-wrapping: everything moves. */
    shiftContent(px: number) {
      shift += px;
    },
  };
}

describe("holding the reader's place", () => {
  beforeEach(() =>
    vi.useFakeTimers({ toFake: ["requestAnimationFrame", "performance"] }),
  );
  afterEach(() => {
    vi.useRealTimers();
    document.body.innerHTML = "";
  });

  it("cancels a shift the reader did not ask for", () => {
    // THE SYMPTOM. Content above the viewport re-wraps, the document grows,
    // `scrollTop` is untouched, and the conversation appears to leap.
    const { scroller, shiftContent } = buildScroller();
    scroller.scrollTop = 1200;

    holdReaderPlace(scroller, 500);
    vi.advanceTimersByTime(16);

    shiftContent(240);
    vi.advanceTimersByTime(16);

    expect(
      scroller.scrollTop,
      "the view was left where it was while the content moved under it",
    ).toBe(1200 + 240);
  });

  it("does nothing when nothing moves", () => {
    // POSITIVE CONTROL: the loop must not drift on its own, or it would walk
    // the view every frame of a 740ms hold.
    const { scroller } = buildScroller();
    scroller.scrollTop = 1200;

    holdReaderPlace(scroller, 500);
    vi.advanceTimersByTime(320);

    expect(scroller.scrollTop).toBe(1200);
  });

  it("leaves the top of the chat alone", () => {
    // At offset 0 there is nothing above the reader to move, and correcting
    // would only fight the browser's own clamp.
    const { scroller, shiftContent } = buildScroller();
    scroller.scrollTop = 0;

    holdReaderPlace(scroller, 500);
    shiftContent(240);
    vi.advanceTimersByTime(64);

    expect(scroller.scrollTop).toBe(0);
  });

  it("gets out of the way the moment the reader scrolls", () => {
    // A correction loop that outlives the reader's intent is worse than the
    // jump it fixes: it would drag the view back under their wheel.
    const { scroller, shiftContent } = buildScroller();
    scroller.scrollTop = 1200;

    holdReaderPlace(scroller, 500);
    vi.advanceTimersByTime(16);

    scroller.dispatchEvent(new Event("wheel"));
    shiftContent(240);
    vi.advanceTimersByTime(64);

    expect(
      scroller.scrollTop,
      "the hold kept correcting after the reader took over",
    ).toBe(1200);
  });

  it("reports when it is finished", () => {
    // `nearBottom` is only recomputed from a scroll event, and a layout-driven
    // height change fires none - so the caller has to be told to re-measure,
    // or the next reply scrolls the reader a second time.
    const { scroller } = buildScroller();
    scroller.scrollTop = 1200;
    const settled = vi.fn();

    holdReaderPlace(scroller, 200, settled);
    vi.advanceTimersByTime(150);
    expect(settled).not.toHaveBeenCalled();

    vi.advanceTimersByTime(100);
    expect(settled).toHaveBeenCalledTimes(1);
  });

  it("reports immediately when there is nothing to hold", () => {
    // GROUND CONTROL for the callback: the early-outs must still tell the
    // caller, or `nearBottom` stays stale on exactly the paths that skip the
    // loop.
    const empty = document.createElement("div");
    empty.getBoundingClientRect = () => ({ top: 0 }) as DOMRect;
    Object.defineProperty(empty, "scrollTop", { value: 500, writable: true });
    document.body.appendChild(empty);

    const settled = vi.fn();
    holdReaderPlace(empty, 200, settled);
    expect(settled).toHaveBeenCalledTimes(1);
  });

  it("leaves no listeners behind", () => {
    // Not a behaviour difference, a LEAK - which is why nothing above catches
    // it. Every press attaches three handlers to the scroller, and a reader
    // who toggles a panel forty times would be carrying a hundred and twenty
    // of them. Balance is the only observable that moves.
    const { scroller } = buildScroller();
    scroller.scrollTop = 1200;
    const added = new Set<string>();
    const removed: string[] = [];
    const realAdd = scroller.addEventListener.bind(scroller);
    const realRemove = scroller.removeEventListener.bind(scroller);
    scroller.addEventListener = ((t: string, ...rest: unknown[]) => {
      added.add(t);
      return (realAdd as (...a: unknown[]) => void)(t, ...rest);
    }) as typeof scroller.addEventListener;
    scroller.removeEventListener = ((t: string, ...rest: unknown[]) => {
      removed.push(t);
      return (realRemove as (...a: unknown[]) => void)(t, ...rest);
    }) as typeof scroller.removeEventListener;

    holdReaderPlace(scroller, 100);
    expect(added.size, "the hold attached nothing to cancel on").toBeGreaterThan(0);

    vi.advanceTimersByTime(200);
    expect(
      [...added].every((t) => removed.includes(t)),
      `attached ${[...added].join(", ")} but only removed ${removed.join(", ") || "nothing"}`,
    ).toBe(true);
  });

  it("can be cancelled by its caller", () => {
    const { scroller, shiftContent } = buildScroller();
    scroller.scrollTop = 1200;

    const stop = holdReaderPlace(scroller, 500);
    vi.advanceTimersByTime(16);
    stop();

    shiftContent(240);
    vi.advanceTimersByTime(64);
    expect(scroller.scrollTop).toBe(1200);
  });
});
