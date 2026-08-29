/**
 * holdReaderPlace - keep the sentence somebody is reading where it is, while
 * the column around it changes width.
 *
 * WHY THE BROWSER CANNOT DO THIS FOR US ANY MORE
 *   Chromium's own scroll anchoring used to absorb exactly this. It stopped
 *   for two independent reasons, both introduced by the column-pinning work:
 *     1. `.chat-gutter` carries `overflow-anchor: none`, and per the Scroll
 *        Anchoring spec that excludes the element AND ITS DESCENDANTS from
 *        anchor selection - so the scroller has no candidates left at all.
 *     2. Even with that removed it would still be suppressed: the spec lists
 *        a computed `width` change on any element between the anchor and the
 *        scroller as a suppression trigger, and `.chat-gutter` is on that path
 *        by construction and animates its width for the whole 320ms.
 *   So this is not a case of preferring JS to a platform feature. The platform
 *   feature cannot fire here without deleting the animation it exists to
 *   smooth over.
 *
 * WHAT GOES WRONG WITHOUT IT
 *   The column is the scroller's only child, so every bubble ABOVE the
 *   viewport re-wraps when it changes width. In a long chat that is a document
 *   height change of thousands of pixels with `scrollTop` untouched: the
 *   conversation appears to leap up or down and a message the reader was not
 *   looking at arrives at the bottom.
 *
 * The correction is one addition per frame against a real element's measured
 * position, not a computed guess, so it stays exact through easing.
 */

/** Class every message bubble carries; the anchor is always one of these. */
const ROW = ".message-bubble-shell";

/**
 * Holds the topmost on-screen bubble still for `ms`, then reports back.
 *
 * `onSettled` exists because a layout-driven height change fires no `scroll`
 * event, which leaves `nearBottom` stale - and a stale "reader is at the
 * bottom" makes the NEXT reply scroll the view a second time from wherever
 * the reader actually ended up.
 *
 * Returns a cancel function. Cancelling is not just tidiness: a reader who
 * starts scrolling mid-correction must win immediately, or the loop fights
 * their wheel.
 */
export function holdReaderPlace(
  scroller: HTMLElement,
  ms: number,
  onSettled?: () => void,
): () => void {
  // At the very top there is nothing to hold: content growing below the
  // reader does not move anything they can see, and pinning would fight the
  // clamp instead.
  if (scroller.scrollTop <= 0) {
    onSettled?.();
    return () => {};
  }

  const viewportTop = scroller.getBoundingClientRect().top;
  let anchor: HTMLElement | null = null;
  for (const row of scroller.querySelectorAll<HTMLElement>(ROW)) {
    // The first row still showing any of itself. Rows above are off-screen and
    // holding one of those would pin a position nobody can see.
    if (row.getBoundingClientRect().bottom > viewportTop) {
      anchor = row;
      break;
    }
  }
  if (!anchor) {
    onSettled?.();
    return () => {};
  }

  const want = anchor.getBoundingClientRect().top - viewportTop;
  let raf = 0;
  let cancelled = false;

  const stop = () => {
    if (cancelled) return;
    cancelled = true;
    cancelAnimationFrame(raf);
    for (const type of USER_SCROLL_EVENTS) {
      scroller.removeEventListener(type, stop);
    }
    onSettled?.();
  };

  const deadline = performance.now() + ms;
  const step = () => {
    if (cancelled) return;
    const drift =
      anchor.getBoundingClientRect().top -
      scroller.getBoundingClientRect().top -
      want;
    // Sub-pixel drift is below what anyone can see and adding it back every
    // frame would only accumulate rounding.
    if (Math.abs(drift) >= 1) scroller.scrollTop += drift;
    if (performance.now() < deadline) raf = requestAnimationFrame(step);
    else stop();
  };

  for (const type of USER_SCROLL_EVENTS) {
    scroller.addEventListener(type, stop, { passive: true });
  }
  raf = requestAnimationFrame(step);
  return stop;
}

/** Anything that means the reader has taken over. */
const USER_SCROLL_EVENTS = ["wheel", "touchstart", "keydown"] as const;
