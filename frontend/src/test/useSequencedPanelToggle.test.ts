/**
 * useSequencedPanelToggle.test.ts - the panel and the column take turns.
 *
 * What is actually being pinned here is an ORDER, not a look: while a panel is
 * moving the column must hold an explicit width (so no bubble re-wraps), and
 * the two motions must never run at the same time. The order flips by
 * direction, because a column may never be wider than the canvas holding it -
 * on collapse the room appears first, on expand it has to be given up first.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import {
  useSequencedPanelToggle,
  PANEL_MS,
  COLUMN_MS,
} from "@/lib/layout/useSequencedPanelToggle";

const OPEN_CANVAS = 836;
const GROWN_CANVAS = 1156;
const PANEL_W = 320;

describe("panel and column take turns", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  /** `available` is read fresh on every call, so a test can widen the canvas
   *  between the two halves the way a real collapse does. */
  function setup(available: { value: number | null }) {
    const toggle = vi.fn();
    const view = renderHook(() =>
      useSequencedPanelToggle(
        () => available.value,
        () => PANEL_W,
      ),
    );
    return { toggle, view };
  }

  it("holds the column still while a panel closes, then widens it", () => {
    const available = { value: OPEN_CANVAS };
    const { toggle, view } = setup(available);

    act(() => view.result.current.run(toggle, false));
    // The panel is already going, and the column is pinned where it was.
    expect(toggle).toHaveBeenCalledTimes(1);
    expect(view.result.current.columnWidth).toBe(OPEN_CANVAS);

    // Mid-panel: still pinned. This is the assertion the whole feature exists
    // for - no width change reaches the bubbles while the panel is in motion.
    act(() => {
      available.value = GROWN_CANVAS;
      vi.advanceTimersByTime(PANEL_MS - 1);
    });
    expect(view.result.current.columnWidth).toBe(OPEN_CANVAS);

    act(() => void vi.advanceTimersByTime(1));
    expect(view.result.current.columnWidth).toBe(GROWN_CANVAS);

    act(() => void vi.advanceTimersByTime(COLUMN_MS));
    expect(
      view.result.current.columnWidth,
      "the pin outlived the sequence; the column is no longer fluid",
    ).toBeNull();
    expect(view.result.current.animating).toBe(false);
  });

  it("opens the panel on the press, with no wait", () => {
    // The press must do something immediately. An earlier version held the
    // panel back until the column had made room, and a third of a second of
    // nothing reads as a broken button.
    const available = { value: GROWN_CANVAS };
    const { toggle, view } = setup(available);

    act(() => view.result.current.run(toggle, true));
    expect(
      toggle,
      "the panel is waiting on the column again; the press feels dead",
    ).toHaveBeenCalledTimes(1);

    act(() => void vi.advanceTimersByTime(0));
    expect(view.result.current.columnWidth).toBe(GROWN_CANVAS - PANEL_W);

    act(() => void vi.advanceTimersByTime(PANEL_MS));
    expect(view.result.current.columnWidth).toBeNull();
    expect(view.result.current.animating).toBe(false);
  });

  it("gives the column less time than the panel when opening", () => {
    // The safety property behind starting them together: the column has to be
    // surrendering width at least as fast as the panel takes it, or it
    // overflows the shrinking canvas. Equal durations would put them exactly
    // level and leave nothing for rounding; the column must be strictly
    // quicker.
    expect(COLUMN_MS).toBeLessThan(PANEL_MS);
  });

  it("starts from the current width so the browser has something to animate", () => {
    // A width that appears already at its target does not transition, it
    // snaps. The opening path therefore publishes the CURRENT width for one
    // frame before the target.
    const available = { value: GROWN_CANVAS };
    const { toggle, view } = setup(available);
    act(() => view.result.current.run(toggle, true));
    expect(view.result.current.columnWidth).toBe(GROWN_CANVAS);
  });

  it("abandons a sequence when the reader changes their mind", () => {
    // The damage a leftover timer does is not that it runs, it is WHEN: the
    // first sequence would clear the pin partway through the second one, and
    // the column would snap back to fluid mid-motion. So the times here are
    // absolute and the assertion sits in the window where the two disagree.
    const available = { value: OPEN_CANVAS };
    const { toggle, view } = setup(available);
    const total = PANEL_MS + COLUMN_MS;
    const interrupt = Math.round(PANEL_MS / 2);

    act(() => view.result.current.run(toggle, false));
    act(() => void vi.advanceTimersByTime(interrupt));

    const second = vi.fn();
    act(() => view.result.current.run(second, false));
    expect(second).toHaveBeenCalledTimes(1);

    // Past the point the FIRST sequence would have finished, still inside the
    // second. A stale timer clears the pin here; a cancelled one cannot.
    act(() => void vi.advanceTimersByTime(total - interrupt + 1));
    expect(
      view.result.current.columnWidth,
      "the abandoned sequence cleared the pin out from under the live one",
    ).not.toBeNull();
    expect(view.result.current.animating).toBe(true);

    // Past the second sequence's own end.
    act(() => void vi.advanceTimersByTime(interrupt));
    expect(view.result.current.columnWidth).toBeNull();
    expect(view.result.current.animating).toBe(false);
  });

  it("does not sequence what it cannot measure", () => {
    // POSITIVE CONTROL: with no measurement the hook must fall back to the
    // plain toggle rather than pinning the column to an invented number.
    const available = { value: null };
    const { toggle, view } = setup(available);

    act(() => view.result.current.run(toggle, false));
    expect(toggle).toHaveBeenCalledTimes(1);
    expect(view.result.current.columnWidth).toBeNull();
    expect(view.result.current.animating).toBe(false);
  });

  it("never pins the column to a negative width", () => {
    // A window narrow enough that the incoming panel would take everything.
    const available = { value: PANEL_W - 40 };
    const { toggle, view } = setup(available);
    act(() => view.result.current.run(toggle, true));
    act(() => void vi.advanceTimersByTime(0));
    expect(view.result.current.columnWidth).toBeGreaterThan(0);
  });

  it("reports that it is animating for the whole sequence", () => {
    const available = { value: OPEN_CANVAS };
    const { toggle, view } = setup(available);

    expect(view.result.current.animating).toBe(false);
    act(() => view.result.current.run(toggle, false));
    expect(view.result.current.animating).toBe(true);
    act(() => void vi.advanceTimersByTime(PANEL_MS + COLUMN_MS - 1));
    expect(view.result.current.animating).toBe(true);
    act(() => void vi.advanceTimersByTime(1));
    expect(view.result.current.animating).toBe(false);
  });
});
