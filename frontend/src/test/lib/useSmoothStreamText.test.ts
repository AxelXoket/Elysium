/**
 * useSmoothStreamText (v1.1 A2 + I10 + H13).
 *
 * rAF is stubbed and pumped manually so pacing math is deterministic. The
 * hook's contract: monotonic prefix growth, bounded trailing (MAX_LAG),
 * instant reset on non-extension targets, grapheme-safe emission, snap on
 * context-switch jumps, verbatim passthrough when disabled.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import {
  useSmoothStreamText,
  snapToGraphemeBoundary,
  advanceToBoundary,
} from "@/lib/chat/useSmoothStreamText";

let rafCallbacks: FrameRequestCallback[] = [];

function pump(now: number) {
  const cbs = rafCallbacks;
  rafCallbacks = [];
  act(() => {
    for (const cb of cbs) cb(now);
  });
}

beforeEach(() => {
  rafCallbacks = [];
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    rafCallbacks.push(cb);
    return rafCallbacks.length;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useSmoothStreamText", () => {
  it("disabled: passes the target through verbatim", () => {
    const { result, rerender } = renderHook(
      ({ target }) => useSmoothStreamText(target, { disabled: true }),
      { initialProps: { target: "" } },
    );
    rerender({ target: "full text at once" });
    expect(result.current).toBe("full text at once");
  });

  it("paces monotonically and eventually catches up", () => {
    const { result, rerender } = renderHook(
      ({ target }) => useSmoothStreamText(target),
      { initialProps: { target: "" } },
    );
    rerender({ target: "Hello smooth streaming world." });

    const seen: string[] = [];
    let t = 1000;
    for (let i = 0; i < 60 && result.current.length < 29; i++) {
      pump(t);
      t += 16;
      seen.push(result.current);
    }
    // Monotonic prefixes, and full text reached.
    for (let i = 1; i < seen.length; i++) {
      expect(seen[i].startsWith(seen[i - 1])).toBe(true);
    }
    expect(result.current).toBe("Hello smooth streaming world.");
  });

  it("never trails the buffer once frames advance past MAX_LAG", () => {
    const { result, rerender } = renderHook(
      ({ target }) => useSmoothStreamText(target),
      { initialProps: { target: "" } },
    );
    const text = "x".repeat(150);
    rerender({ target: text });
    // Two seconds of frames - beyond MAX_LAG_MS(1500) everything must show.
    let t = 5000;
    for (let i = 0; i < 130; i++) {
      pump(t);
      t += 16;
    }
    expect(result.current).toBe(text);
  });

  it("resets instantly when the target is not an extension (regenerate)", () => {
    const { result, rerender } = renderHook(
      ({ target }) => useSmoothStreamText(target),
      { initialProps: { target: "old reply text" } },
    );
    expect(result.current).toBe("old reply text"); // initial state = target
    rerender({ target: "" });
    expect(result.current).toBe(""); // teardown snaps down immediately

    rerender({ target: "ne" });
    pump(100);
    pump(116);
    expect("ne".startsWith(result.current)).toBe(true); // paces the new text
  });

  it("H13: a huge single jump snaps instead of retyping (chat switch)", () => {
    const { result, rerender } = renderHook(
      ({ target }) => useSmoothStreamText(target),
      { initialProps: { target: "" } },
    );
    const bigStreamedText = "chat B already streamed this much text. ".repeat(10);
    rerender({ target: bigStreamedText });
    // No retype-from-zero: the full text is shown without pumping frames.
    expect(result.current).toBe(bigStreamedText);
  });

  it("I10: never splits grapheme clusters (ZWJ family, skin tone, flag, accent)", () => {
    const family = "\u{1F468}‍\u{1F469}‍\u{1F467}"; // 👨‍👩‍👧
    const wave = "\u{1F44B}\u{1F3FD}"; // 👋🏽
    const flag = "\u{1F1F9}\u{1F1F7}"; // 🇹🇷
    const accented = "é"; // é as combining pair
    const text = `hi ${family} ${wave} ${flag} caf${accented}`;

    // Every possible cut point lands on a boundary that keeps clusters whole.
    for (let cut = 0; cut <= text.length; cut++) {
      const snapped = snapToGraphemeBoundary(text, cut);
      const prefix = text.slice(0, snapped);
      // A safe prefix must never end INSIDE a cluster: re-segmenting the
      // prefix and comparing round-trips proves the boundary is real.
      const segmenter = new Intl.Segmenter(undefined, {
        granularity: "grapheme",
      });
      const rejoined = Array.from(
        segmenter.segment(prefix),
        (s) => s.segment,
      ).join("");
      expect(rejoined).toBe(prefix);
      // And specifically: no cut may end on a lone ZWJ or half a surrogate.
      expect(prefix.endsWith("‍")).toBe(false);
      const lastCode = prefix.charCodeAt(prefix.length - 1);
      expect(lastCode >= 0xd800 && lastCode <= 0xdbff).toBe(false);
    }
  });

  it("I10: plain ASCII boundaries are the identity (pacing unchanged)", () => {
    const text = "plain ascii text";
    for (let cut = 0; cut <= text.length; cut++) {
      expect(snapToGraphemeBoundary(text, cut)).toBe(cut);
    }
  });
});

describe("advanceToBoundary (A2 smoothness: windowed grapheme advance)", () => {
  const family = "\u{1F468}‍\u{1F469}‍\u{1F467}"; // 👨‍👩‍👧
  const wave = "\u{1F44B}\u{1F3FD}"; // 👋🏽
  const flag = "\u{1F1F9}\u{1F1F7}"; // 🇹🇷
  const text = `hi ${family} ${wave} ${flag} café more ascii tail`;
  const seg = new Intl.Segmenter(undefined, { granularity: "grapheme" });
  // Every real grapheme boundary of `text` (0..len, only cluster edges).
  const boundaries = (() => {
    const bs = [0];
    for (const s of seg.segment(text)) bs.push(s.index + s.segment.length);
    return bs;
  })();

  it("is byte-for-byte equivalent to a full snap, except where that would stall", () => {
    // The optimization's correctness claim: scanning only [from, target] from
    // a known boundary yields the SAME index as scanning from 0.
    //
    // The one documented divergence is the case that used to DEADLOCK: when
    // the next cluster is wider than the budget, snapping down returns `from`,
    // no progress is made, and the tick reschedules itself with identical
    // state forever. There it advances by exactly one cluster instead - never
    // splitting one, and never standing still.
    for (const from of boundaries) {
      for (let target = from; target <= text.length + 3; target++) {
        const snapped = snapToGraphemeBoundary(text, Math.min(target, text.length));
        const got = advanceToBoundary(text, from, target);
        if (snapped > from || target <= from || from >= text.length) {
          expect(got).toBe(snapped);
        } else {
          // Exactly the next boundary after `from`, and strictly forward.
          expect(got).toBeGreaterThan(from);
          expect(boundaries).toContain(got);
          const nextBoundary = boundaries.find((b) => b > from)!;
          expect(got).toBe(nextBoundary);
        }
      }
    }
  });

  it("never advances into the middle of a cluster at the tail", () => {
    for (const from of boundaries) {
      for (let emit = 1; emit <= 8; emit++) {
        const next = advanceToBoundary(text, from, from + emit);
        const prefix = text.slice(0, next);
        const rejoined = Array.from(
          seg.segment(prefix),
          (s) => s.segment,
        ).join("");
        expect(rejoined).toBe(prefix); // prefix ends on a real boundary
        expect(next).toBeGreaterThanOrEqual(from); // monotonic, never rewinds
      }
    }
  });

  it("clamps out-of-range targets and is a no-op when target <= from", () => {
    expect(advanceToBoundary(text, 5, 5)).toBe(5);
    expect(advanceToBoundary(text, 8, 4)).toBe(8); // target < from -> from
    expect(advanceToBoundary(text, 0, text.length + 99)).toBe(text.length);
  });
});

/**
 * Audit: the pacer froze permanently on a multi-code-unit grapheme cluster.
 *
 * advanceToBoundary returned `from` when the next cluster was longer than the
 * per-frame budget, so setShownLen was never called - and the tick rescheduled
 * itself with an unchanged shownLen, lag and rate, producing an identical emit
 * every frame. The displayed prefix froze at the emoji and a 60 fps no-op loop
 * ran for the rest of the stream. Mid-message it only recovered once the
 * backlog grew past ~150 chars, so on a SLOW provider - precisely when the
 * pacer is keeping up and lag is small - it never recovered at all.
 */
describe("advanceToBoundary - clusters wider than the budget", () => {
  const FAMILY = "\u{1F468}\u200D\u{1F469}\u200D\u{1F467}\u200D\u{1F466}";
  const text = `Here is my family: ${FAMILY}`;

  it("always advances, even when the next cluster does not fit", () => {
    // shownLen 19, budget 4 - the ZWJ family is 11 UTF-16 units.
    const next = advanceToBoundary(text, 19, 23);
    expect(next).toBeGreaterThan(19);
  });

  it("never splits the cluster it overshoots for", () => {
    const next = advanceToBoundary(text, 19, 23);
    expect(text.slice(19, next)).toBe(FAMILY);
  });

  it("converges instead of looping forever", () => {
    // Ten frames of the real arithmetic: every one must make progress.
    let shown = 19;
    for (let frame = 0; frame < 10 && shown < text.length; frame += 1) {
      const before = shown;
      shown = advanceToBoundary(text, shown, Math.min(shown + 4, text.length));
      expect(shown).toBeGreaterThan(before);
    }
    expect(shown).toBe(text.length);
  });

  it("a skin-toned emoji at the tail also advances", () => {
    const thumb = "\u{1F44D}\u{1F3FD}"; // 4 UTF-16 units
    const tail = `ok ${thumb}`;
    const next = advanceToBoundary(tail, 3, 6);
    expect(next).toBe(tail.length);
  });

  it("plain text still stops exactly at the budget", () => {
    expect(advanceToBoundary("abcdefgh", 2, 5)).toBe(5);
  });
});
