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
  MAX_LAG_MS,
  SMOOTH_CPS,
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
      ({ target }) => useSmoothStreamText(target, { lineage: 1, disabled: true }),
      { initialProps: { target: "" } },
    );
    rerender({ target: "full text at once" });
    expect(result.current).toBe("full text at once");
  });

  it("paces monotonically and eventually catches up", () => {
    const { result, rerender } = renderHook(
      ({ target }) => useSmoothStreamText(target, { lineage: 1 }),
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
      ({ target }) => useSmoothStreamText(target, { lineage: 1 }),
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
      ({ target }) => useSmoothStreamText(target, { lineage: 1 }),
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

  it("H13: switching INTO a streaming chat snaps instead of retyping", () => {
    // The lineage key is what makes this a switch. Chat A is idle, chat B is
    // already mid-stream: the text on B's wire was produced while the user was
    // somewhere else, so retyping it would replay something they never saw.
    const { result, rerender } = renderHook(
      ({ target, lineage }) => useSmoothStreamText(target, { lineage }),
      { initialProps: { target: "", lineage: 1 } },
    );
    const bigStreamedText = "chat B already streamed this much text. ".repeat(10);
    rerender({ target: bigStreamedText, lineage: 2 });
    // No retype-from-zero: the full text is shown without pumping frames.
    expect(result.current).toBe(bigStreamedText);
  });

  it("a fast model's first delta is TYPED, however big it is", () => {
    // The bug this replaced: the snap above used to be inferred from SIZE
    // alone (a jump over 200 characters). At the start of a stream shownLen
    // and prevTarget.length are both 0, so that test always read as "caught
    // up" - and a fast model whose first delta carried more than 200
    // characters had its entire reply painted in one frame. Measured: the
    // cliff sat exactly at 201. Same chat, so nothing may appear at once.
    const { result, rerender } = renderHook(
      ({ target }) => useSmoothStreamText(target, { lineage: 7 }),
      { initialProps: { target: "" } },
    );
    const wholeReply = "x".repeat(800);
    rerender({ target: wholeReply });

    expect(
      result.current.length,
      "the whole reply was painted before a single frame ran",
    ).toBeLessThan(wholeReply.length);

    // And it does arrive, by typing, within the trailing bound.
    let t = 0;
    let frames = 0;
    while (result.current.length < wholeReply.length && frames < 400) {
      t += 16;
      pump(t);
      frames += 1;
    }
    expect(result.current).toBe(wholeReply);
    expect(frames, "typing it took no frames at all").toBeGreaterThan(1);
  });

  it("a lineage change to SMALL text still shows exactly that text", () => {
    // Switching to an idle chat: nothing to retype, nothing to withhold.
    const { result, rerender } = renderHook(
      ({ target, lineage }) => useSmoothStreamText(target, { lineage }),
      { initialProps: { target: "streaming in chat A", lineage: 1 } },
    );
    rerender({ target: "", lineage: 2 });
    expect(result.current).toBe("");
  });

  /**
   * Drive one backlog to completion at 60 fps and report what it cost.
   * Frames are pumped one callback at a time, the way the browser does it.
   */
  function drainProfile(total: number) {
    const { result, rerender } = renderHook(
      ({ target }) => useSmoothStreamText(target, { lineage: 1 }),
      { initialProps: { target: "" } },
    );
    rerender({ target: "a".repeat(total) });
    let t = 0;
    let frames = 0;
    let prev = result.current.length;
    let biggestFrame = 0;
    while (result.current.length < total && frames < 5000) {
      t += 16;
      pump(t);
      frames += 1;
      biggestFrame = Math.max(biggestFrame, result.current.length - prev);
      prev = result.current.length;
    }
    return { ms: frames * 16, biggestFrame, done: result.current.length === total };
  }

  it("honours MAX_LAG_MS even on an absurd backlog", () => {
    // The bound used to be dead code: `rate += lag / CATCHUP_TAU_MS` already
    // exceeded `lag / MAX_LAG_MS`, so the Math.max could never win. Worse, the
    // expression itself is not a deadline - used as a rate against a shrinking
    // lag it decays exponentially and never arrives. Measured with that form:
    // 12000 characters took 1712 ms and 24000 took 1920 ms against a promise
    // of 1500. 24000 is chosen deliberately: the old law is ~28% over there,
    // far outside the one-frame tolerance below.
    const p = drainProfile(24000);
    expect(p.done, "the backlog never finished draining").toBe(true);
    expect(
      p.ms,
      "the display trailed the buffer for longer than MAX_LAG_MS promises",
    ).toBeLessThanOrEqual(MAX_LAG_MS + 32);
  });

  it("keeps ordinary replies under the smoothness ceiling", () => {
    // SMOOTH_CPS is what stops a burst being painted as a block. Measured
    // before it existed: 2000 characters revealed 109 in a single frame and
    // 5000 revealed 269, which reads as a paragraph appearing, not as typing.
    const perFrame = Math.round(SMOOTH_CPS / 60);
    const p = drainProfile(2000);
    expect(p.done).toBe(true);
    expect(
      p.biggestFrame,
      `a single frame revealed ${p.biggestFrame} characters, over the ${perFrame} ceiling`,
    ).toBeLessThanOrEqual(perFrame + 1);
  });

  it("lets the deadline override the ceiling when it has to", () => {
    // The positive control for the test above, and the deliberate trade-off
    // written down: the two constraints genuinely conflict once the backlog is
    // large enough, and the bounded delay is the one that wins. Without this,
    // "never exceeds the ceiling" would also be satisfied by a hook that was
    // simply too slow to keep its own promise.
    const perFrame = Math.round(SMOOTH_CPS / 60);
    const p = drainProfile(24000);
    expect(
      p.biggestFrame,
      "the ceiling was never exceeded, so MAX_LAG_MS cannot have been met",
    ).toBeGreaterThan(perFrame);
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
