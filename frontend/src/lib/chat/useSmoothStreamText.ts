/**
 * useSmoothStreamText - presentation-layer typewriter pacing (v1.1 A2).
 *
 * Providers deliver tokens in bursts; painting each burst at once reads as
 * jumpy "wall of text" streaming. This hook paces the DISPLAYED prefix of
 * the accumulating stream text: an EMA of the real arrival rate sets the
 * base speed (a slow model types slowly), a proportional catch-up term
 * drains backlog, and a hard bound guarantees the display never trails the
 * buffer by more than MAX_LAG_MS.
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

const MIN_CPS = 90; // floor chars/sec - dead-air protection
const CATCHUP_TAU_MS = 300; // proportional backlog drain time constant
const MAX_LAG_MS = 1500; // hard bound: display never trails more than this
const EMA_ALPHA = 0.2;
const MAX_DT_MS = 100; // clamp tab-hidden gaps so lag drains fast, not in one dump
const SNAP_JUMP_CHARS = 200; // a bigger single jump = context switch, not typing (H13)
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
  opts?: { disabled?: boolean },
): string {
  const disabled = opts?.disabled ?? false;
  const [shownLen, setShownLen] = useState(target.length);
  const [prevTarget, setPrevTarget] = useState(target);
  const emaRateRef = useRef(0); // chars per ms
  const lastTimeRef = useRef<number | null>(null);
  const seenLenRef = useRef(target.length);

  // Render-phase adjustment (the React-endorsed "derive from prop change"
  // pattern - no effects involved):
  if (target !== prevTarget) {
    setPrevTarget(target);
    if (!target.startsWith(prevTarget)) {
      // Not an extension: regenerate restart, teardown to "", chat switch.
      // H13: a reset landing on LARGE text is a switch INTO an already-
      // streaming chat - snap instead of retyping hundreds of chars.
      setShownLen(target.length > SNAP_JUMP_CHARS ? target.length : 0);
    } else if (
      shownLen >= prevTarget.length &&
      target.length - prevTarget.length > SNAP_JUMP_CHARS
    ) {
      // H13, sneaky variant: "" -> big text IS a valid prefix extension
      // (idle chat -> streaming chat). A huge single jump while fully
      // caught up is a context switch, not typing - snap.
      setShownLen(target.length);
    }
  }

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
      let rate = Math.max(MIN_CPS / 1000, emaRateRef.current);
      rate += lag / CATCHUP_TAU_MS; // proportional drain - natural ease-out
      rate = Math.max(rate, lag / MAX_LAG_MS); // hard trailing bound

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
