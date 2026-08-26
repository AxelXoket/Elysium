/**
 * useSmoothStreamText - presentation-layer typewriter pacing (v1.1 A2).
 *
 * Providers deliver tokens in bursts; painting each burst at once reads as
 * jumpy "wall of text" streaming. This hook paces the DISPLAYED prefix of
 * the accumulating stream text: an EMA of the real arrival rate sets the
 * base speed (a slow model types slowly), a proportional catch-up term
 * drains backlog, a smoothness ceiling (SMOOTH_CPS) refuses to paint faster
 * than the eye reads as typing, and a hard bound (MAX_LAG_MS) is allowed to
 * override that ceiling rather than let the display fall further behind than
 * it promises.
 *
 * Deliberately OUTSIDE useStreamingCompletion: the hook's terminal logic,
 * abort semantics and rAF batching all read the full `streamedText` - pacing
 * is a view concern and must never delay persistence or teardown.
 *
 * Structure note: lineage resets (regenerate/chat switch) use the
 * render-phase state-adjustment pattern; the effect only drives the rAF
 * loop, whose callbacks do the setState (lint: no sync setState in effects).
 *
 * Grapheme-safe (I10): the shown prefix only ever ends on a grapheme-cluster
 * boundary via Intl.Segmenter - ZWJ families, skin tones, flags and combining
 * accents are never split mid-cluster. Runtimes without Segmenter fall back
 * to not splitting surrogate pairs.
 */

import { useEffect, useRef, useState } from "react";

// Exported so tests assert against the REAL numbers instead of retyping them.
// A pacing test that carries its own copy of 1500 agrees with itself and with
// nothing else, and stays green through a change to the value it is named for.

/** Floor chars/sec - dead-air protection when the model is very slow. */
export const MIN_CPS = 90;

/**
 * Smoothness ceiling, chars/sec.
 *
 * 1440 is 24 characters per frame at 60 fps. Above roughly that, growth stops
 * reading as typing and starts reading as blocks appearing: measured before
 * this ceiling existed, a 5000-character reply revealed 269 characters in its
 * first frame, which is a paragraph, not a letter.
 *
 * This is a CEILING, not a cap on the final rate. MAX_LAG_MS is applied after
 * it and is allowed to win, because a bounded delay matters more than perfect
 * smoothness once the backlog is absurd. The two genuinely conflict and the
 * order below is the answer: smooth while it is affordable, honest about
 * lateness when it is not.
 */
export const SMOOTH_CPS = 1440;

/** Proportional backlog drain time constant. */
export const CATCHUP_TAU_MS = 300;

/**
 * Hard bound: the display never trails the buffer by more than this.
 *
 * This was dead code. `rate += lag / CATCHUP_TAU_MS` already makes rate at
 * least lag/300, which is always >= lag/1500, so the Math.max below could
 * never win - checked exhaustively over lag 0..200000, it did not win once.
 * The docblock promised a guarantee that nothing delivered: measured wall
 * time was 1504 ms at 6000 characters, 1712 ms at 12000 and 1920 ms at 24000,
 * growing without limit. The SMOOTH_CPS ceiling above is what brings the rate
 * back down far enough for this bound to become reachable, so the two
 * changes are one change.
 */
export const MAX_LAG_MS = 1500;

const EMA_ALPHA = 0.2;
const MAX_DT_MS = 100; // clamp tab-hidden gaps so lag drains fast, not in one dump
// Window margin past the advance target so the grapheme cluster straddling it
// is fully present in the sliced window (real clusters - ZWJ families, flags,
// skin tones, combining accents - are well under this). A window ending
// exactly at the target could truncate a cluster and mis-read it as complete.
const MAX_CLUSTER_CHARS = 64;

const segmenter: Intl.Segmenter | null =
  typeof Intl !== "undefined" && typeof Intl.Segmenter === "function"
    ? new Intl.Segmenter(undefined, { granularity: "grapheme" })
    : null;

/**
 * Largest index <= len that lands on a grapheme boundary of `text`.
 * Exported for tests.
 */
export function snapToGraphemeBoundary(text: string, len: number): number {
  if (len <= 0) return 0;
  if (len >= text.length) return text.length;
  if (segmenter != null) {
    let boundary = 0;
    for (const seg of segmenter.segment(text)) {
      const next = seg.index + seg.segment.length;
      if (next > len) break;
      boundary = next;
      if (next === len) break;
    }
    return boundary;
  }
  // Fallback: at least never split a surrogate pair.
  const code = text.charCodeAt(len - 1);
  if (code >= 0xd800 && code <= 0xdbff) return len - 1;
  return len;
}

/**
 * Advance from a KNOWN grapheme boundary `from` to the largest boundary
 * <= `target`, segmenting ONLY the local window [from, target] instead of the
 * whole string. Semantically identical to snapToGraphemeBoundary(text, target)
 * whenever `from` is a real boundary with `from <= target`, but O(target-from)
 * rather than O(target).
 *
 * This is the core of the A2 smoothness fix: the pacing loop used to re-segment
 * the ENTIRE accumulated text twice per frame (here + in the returned slice),
 * so per-frame cost grew with message length - long replies dropped frames,
 * and each dropped frame enlarged the next reveal, compounding the stutter.
 * Scanning only the freshly-revealed slice keeps per-frame cost flat.
 *
 * Exported for tests.
 */
export function advanceToBoundary(
  text: string,
  from: number,
  target: number,
): number {
  if (target <= from) return Math.max(from, 0);
  if (target >= text.length) return text.length;
  if (segmenter != null) {
    const window = text.slice(from, target + MAX_CLUSTER_CHARS);
    let boundary = from;
    // The end of the FIRST cluster, whether or not it fits the budget. When
    // the next cluster is longer than the per-frame budget, returning `from`
    // made no progress at all - and the tick then re-scheduled itself with an
    // unchanged shownLen, lag and rate, producing an identical emit every
    // frame. The displayed prefix froze on a ZWJ family emoji (11 UTF-16
    // units) or a skin-toned thumb (4) while a 60 fps no-op loop spun for the
    // rest of the stream. A cluster is indivisible, so overshooting the budget
    // by one cluster is the only correct move: it never splits a grapheme and
    // it always advances.
    let firstEnd = -1;
    for (const seg of segmenter.segment(window)) {
      const next = from + seg.index + seg.segment.length;
      if (firstEnd < 0) firstEnd = next;
      if (next > target) break;
      boundary = next;
      if (next === target) break;
    }
    if (boundary === from && firstEnd > from) return firstEnd;
    return boundary;
  }
  const code = text.charCodeAt(target - 1);
  if (code >= 0xd800 && code <= 0xdbff) return target - 1;
  return target;
}

export function useSmoothStreamText(
  target: string,
  opts: { lineage: string | number | null; disabled?: boolean },
): string {
  const disabled = opts.disabled ?? false;
  const lineage = opts.lineage;
  const [shownLen, setShownLen] = useState(target.length);
  const [prevTarget, setPrevTarget] = useState(target);
  const [prevLineage, setPrevLineage] = useState(lineage);
  const emaRateRef = useRef(0); // chars per ms
  // When the CURRENT backlog must be fully shown by. Null while caught up.
  const deadlineRef = useRef<number | null>(null);
  const lastTimeRef = useRef<number | null>(null);
  const seenLenRef = useRef(target.length);

  // Render-phase adjustment (the React-endorsed "derive from prop change"
  // pattern - no effects involved):
  if (lineage !== prevLineage) {
    // H13, told rather than guessed. Switching INTO a chat that is already
    // streaming must show what is on the wire, not retype hundreds of
    // characters the user already missed.
    //
    // This used to be inferred from SIZE: a jump bigger than 200 characters
    // was assumed to be a context switch. That reading is not available at
    // the start of a stream, where `shownLen` and `prevTarget.length` are
    // both 0 and therefore always "caught up" - so a fast model whose FIRST
    // delta carried more than 200 characters was mistaken for a chat switch
    // and its whole reply was painted in one frame, with no typing at all.
    // Measured: the cliff sat exactly at 201 characters. The lineage key is
    // the thing the size was standing in for, so it is passed in directly.
    setPrevLineage(lineage);
    setPrevTarget(target);
    setShownLen(target.length);
  } else if (target !== prevTarget) {
    setPrevTarget(target);
    if (!target.startsWith(prevTarget)) {
      // Same chat, not an extension: a regenerate restart or a teardown to
      // "". Retype it - within one conversation that IS new text arriving.
      setShownLen(0);
    }
    // Same chat, plain extension: no adjustment. However much arrived at
    // once, the pacing loop below types it - fast when there is a lot to
    // catch up on, slow when the model is slow.
  }

  // A new lineage means the arrival bookkeeping below belongs to a
  // conversation the user has left. Carried over, `seenLenRef` still holds the
  // OLD chat's length, so the new chat's first delta computes its arrival rate
  // over the whole message rather than the delta - measured at roughly 29x the
  // true rate, decaying over about 19 frames, and every delta inside that
  // window is emitted whole. Refs may not be written during render, so the
  // reset lives here, keyed on lineage and declared BEFORE the pacing effect
  // so it has already run when that one reads the refs.
  useEffect(() => {
    seenLenRef.current = target.length;
    lastTimeRef.current = null;
    emaRateRef.current = 0;
    deadlineRef.current = null;
    // `target` is deliberately not a dependency: this is a lineage reset, not
    // a per-delta one, and including it would wipe the EMA on every chunk.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lineage]);

  useEffect(() => {
    if (disabled) return;
    // Lineage bookkeeping for the EMA (ref writes are effect-safe).
    if (seenLenRef.current > target.length) {
      emaRateRef.current = 0;
      seenLenRef.current = 0;
    }
    if (shownLen >= target.length) return; // caught up - no loop needed

    let raf = 0;
    const tick = (now: number) => {
      const last = lastTimeRef.current ?? now;
      lastTimeRef.current = now;
      const dt = Math.min(Math.max(now - last, 0), MAX_DT_MS);

      if (target.length > seenLenRef.current && dt > 0) {
        const arrival = (target.length - seenLenRef.current) / Math.max(dt, 1);
        emaRateRef.current =
          EMA_ALPHA * arrival + (1 - EMA_ALPHA) * emaRateRef.current;
      }
      seenLenRef.current = target.length;

      const lag = target.length - shownLen;
      // Falling behind starts the clock; catching up stops it. Refreshing it
      // on every delta would let a long stream postpone its own deadline
      // forever, which is the failure the bound exists to prevent.
      if (lag > 0 && deadlineRef.current == null) {
        deadlineRef.current = now + MAX_LAG_MS;
      } else if (lag <= 0) {
        deadlineRef.current = null;
      }
      // Three terms, applied in this order on purpose.
      //   1. base speed: the model's own measured rate, with a floor
      //   2. catch-up:   proportional drain, giving a natural ease-out
      //   3. ceiling:    refuse to paint faster than the eye reads as typing
      //   4. bound:      unless being that smooth would leave us too far behind
      // Swapping 3 and 4 would put the ceiling last and reinstate the wall of
      // text; dropping 3 is what made 4 unreachable in the first place.
      let rate = Math.max(MIN_CPS / 1000, emaRateRef.current);
      rate += lag / CATCHUP_TAU_MS; // proportional drain - natural ease-out
      rate = Math.min(rate, SMOOTH_CPS / 1000); // smoothness ceiling

      // The trailing bound, as an actual DEADLINE rather than a fixed ratio.
      //
      // `lag / MAX_LAG_MS` reads like "drain it within MAX_LAG_MS" and is not:
      // used as a rate against a shrinking lag it is an exponential decay with
      // a 1500 ms time constant, so it approaches zero and never arrives.
      // Measured with that form in place, 2000 characters took 1808 ms and
      // 24000 took 5472 ms while the docblock promised 1500. Dividing by the
      // time LEFT instead of by the whole budget is what makes the promise
      // true: as the deadline approaches the required rate rises to meet it.
      const remaining = Math.max(1, (deadlineRef.current ?? now) - now);
      rate = Math.max(rate, lag / remaining);

      const emit = Math.min(lag, Math.ceil(rate * dt));
      if (emit > 0) {
        // Scan only [shownLen, shownLen+emit] from the known boundary - not
        // the whole buffer (A2 smoothness: flat per-frame cost).
        const nextLen = advanceToBoundary(target, shownLen, shownLen + emit);
        if (nextLen > shownLen) {
          setShownLen(nextLen); // re-runs the effect -> next frame
          return;
        }
      }
      raf = requestAnimationFrame(tick); // boundary not reached yet - retry
    };
    raf = requestAnimationFrame(tick);

    // Tab returning to visibility: reset the clock so the hidden gap does
    // not register as one huge dt (the MAX_DT clamp + hard bound then drain
    // the backlog over a few visible frames instead of a single dump).
    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        lastTimeRef.current = null;
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [target, shownLen, disabled]);

  if (disabled) return target;
  // No per-render re-segmentation: shownLen is only ever set to a grapheme
  // boundary of the current target (append-only growth preserves prefix
  // boundaries; non-extension resets snap to 0 or target.length), so the
  // displayed prefix is already boundary-aligned. Dropping the old full-string
  // snapToGraphemeBoundary here removes the second O(n)-per-frame pass. The
  // clamp guards the transient case where a shrunk target lags shownLen.
  return target.slice(0, Math.min(shownLen, target.length));
}
