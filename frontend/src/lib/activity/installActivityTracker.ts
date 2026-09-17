/**
 * installActivityTracker - what "somebody is here" means to the idle lock.
 *
 * The idle clock lives on the backend and used to move only for HTTP
 * requests, so twenty minutes of reading, scrolling and typing without
 * pressing Send looked like an empty room, and the vault locked on the fifth
 * minute. This module watches real input on the window and tells the backend,
 * through POST /activity, that a person is present.
 *
 * Two classes of event, and the difference is the security of the thing.
 *
 * SELF-PROVING: keydown, focusin, pointerdown, touchstart, wheel. Each is a
 * key, a finger, a wheel or a button, and none is produced by software that
 * only moves the cursor. The one check is event.isTrusted: a synthetic event
 * dispatched by page script is not a person. keydown and focusin are also the
 * browser's own proof of system focus (WHATWG defines hasFocus() by whether
 * keyboard events are being routed to this document), so asking hasFocus()
 * again for those two would be circular. One caveat, written down rather than
 * fixed: element.focus() called from script fires a focusin the browser marks
 * trusted. Every focus() call in this app today is inside a user event
 * handler or downstream of one in the same interaction, with three that run
 * when the request a click started settles (Composer.tsx on stream end,
 * ChatList.tsx and ApiKeySection.tsx on a mutation's success); those land
 * at the instant the backend already stamped the clock for that request, so
 * they credit nothing the request did not. A focus() started from a timer
 * or a lifecycle event with nobody behind it would be a new hole, and the
 * audit of those call sites is what keeps this class honest.
 *
 * GATED: pointermove and pointerover, the two events a pointer produces on
 * its own. They count only when four things hold at once:
 *   (1) document.hasFocus() is true;
 *   (2) document.visibilityState is "visible";
 *   (3) the pointer is inside the window (the latch below);
 *   (4) the point is at least MOVE_THRESHOLD_PX from the ANCHOR.
 * The anchor is the last ACCEPTED point, never the previous event's point. If
 * it moved with every event, a cursor wobbling three pixels at a time would
 * never cross the threshold while dragging the anchor along with it, so the
 * total displacement would never be measured. Anchored at acceptance, the
 * same wobble can only ever get three pixels from the anchor, while a slow
 * but real hand accumulates and crosses.
 *
 * The failure the gate closes has a name: a mouse parked over an unfocused
 * window. Windows still delivers mouse messages to a window without focus,
 * and Chromium fires pointerover and pointerout on it, so without (1) the
 * vault would stay open forever. Movement over some OTHER window never
 * reaches this page at all (hit-testing), so that half of the rule costs
 * nothing. What the gate cannot close: the window is focused, the person has
 * left, and a piece of software moves the cursor more than MOVE_THRESHOLD_PX
 * over it. No web signal separates that from a hand; the only authoritative
 * one is the OS-level injected-input flag, which needs a global input hook,
 * and this app will not install one.
 *
 * The latch (pointerInside) starts false. It opens when a trusted gated
 * event (pointerover or pointermove) or a trusted pointerdown arrives. It
 * closes on a pointerout on the document whose relatedTarget is null, a
 * pointerleave on documentElement, a blur of the WINDOW itself, the page
 * going hidden, or a pointercancel. A blur inside the page, focus moving
 * from one input to the next, is not the pointer leaving and does not close
 * it; onWindowBlur is where that line is drawn. The four conditions are
 * judged against the latch as it stood BEFORE the event, so the event that
 * opens the latch never counts by itself: after the pointer left and came
 * back, the first move only reopens the door and the next one past the
 * threshold is the first that counts.
 * The same holds for the very first gated event after install or after
 * enable: it only places the anchor, since there is nothing to measure a
 * distance from yet, and "nothing to measure" is not a free acceptance. The
 * latch is secondary on purpose, since pointerleave is not guaranteed at the
 * window edge; (1) carries the security.
 *
 * Network: events only update a local stamp. A separate tick, every
 * PING_CHECK_INTERVAL_MS, sends one POST when both hold: at least
 * PING_MIN_GAP_S since the last ping AND a real event since the last ping. So
 * a ping is born from an event, never from the timer alone, and the worst
 * case is two requests a minute. Silent while the page is hidden
 * (visibilitychange) and while the vault is locked (setActivityTrackerEnabled
 * is fed from the vault status by VaultGate). A 423 that still gets through
 * is harmless: client.ts folds it into the same coalesced lock signal every
 * other request raises.
 *
 * Capture phase, because eleven places in this app call stopPropagation() on
 * React synthetic events and React attaches its listeners to #root: in the
 * bubble phase those events never reach window. Passive, because nothing
 * here ever calls preventDefault().
 *
 * Tests reach the trust check with genuinely trusted events (jsdom's own
 * dispatch helper, see test/helpers/trustedEvents.ts), so there is no test
 * toggle in this file and nothing here accepts an untrusted event.
 */
import { postActivity } from "@/lib/api/activity";

/** Events that need nothing beyond isTrusted. */
export const SELF_PROVING_EVENTS = [
  "keydown",
  "focusin",
  "pointerdown",
  "touchstart",
  "wheel",
] as const;

/** Events a pointer produces on its own; counted only behind the four-part gate. */
export const GATED_EVENTS = ["pointermove", "pointerover"] as const;

/** Everything the tracker listens to. "scroll" is deliberately absent: six
 *  places in this app scroll programmatically and the browser marks those
 *  scroll events trusted, while every way a person scrolls is already here
 *  (wheel, touchstart, keydown, and a scrollbar drag is pointerdown plus
 *  pointermove). */
export const ACTIVITY_EVENTS = [...SELF_PROVING_EVENTS, ...GATED_EVENTS] as const;
export type ActivityEvent = (typeof ACTIVITY_EVENTS)[number];

/** At most one ping per this many seconds. The backend watchdog looks every
 *  30 seconds and the shortest timeout is a minute, so this resolution is
 *  enough. */
export const PING_MIN_GAP_S = 30;

/** A gated event counts only this far from the last accepted point. */
export const MOVE_THRESHOLD_PX = 10;

/** How often the tick asks whether a ping is due. It sends nothing on its
 *  own: without a real event since the last ping it does nothing. */
export const PING_CHECK_INTERVAL_MS = 1000;

const LISTEN: AddEventListenerOptions = { passive: true, capture: true };

let enabled = false;
let stamp: number | null = null;
let lastPingAt: number | null = null;
let eventSincePing = false;
let pointerInside = false;
let anchorX: number | null = null;
let anchorY: number | null = null;
let pageHidden = false;
let ticker: ReturnType<typeof setInterval> | null = null;
let uninstall: (() => void) | null = null;

/** When the last accepted event happened (Date.now() ms), or null if none. */
export function lastActivityAt(): number | null {
  return stamp;
}

function accept(): void {
  stamp = Date.now();
  eventSincePing = true;
}

function closeLatch(): void {
  pointerInside = false;
}

function onSelfProving(event: Event): void {
  if (!enabled || !event.isTrusted) return;
  if (event.type === "pointerdown") pointerInside = true;
  accept();
}

function onGated(event: Event): void {
  if (!enabled || !event.isTrusted) return;
  // A trusted pointerover or pointermove is how the pointer announces it is
  // over the page. The four gates are judged against the latch as it stood
  // BEFORE this event, so the event that opens the latch never counts.
  const wasInside = pointerInside;
  pointerInside = true;
  const { clientX, clientY } = event as PointerEvent;
  if (anchorX === null || anchorY === null) {
    // The first point after install or after enable only places the anchor.
    anchorX = clientX;
    anchorY = clientY;
    return;
  }
  if (!wasInside) return;
  if (!document.hasFocus()) return;
  if (document.visibilityState !== "visible") return;
  if (Math.hypot(clientX - anchorX, clientY - anchorY) < MOVE_THRESHOLD_PX) return;
  // Accepted, and only now does the anchor move.
  anchorX = clientX;
  anchorY = clientY;
  accept();
}

function onPointerOut(event: Event): void {
  if ((event as PointerEvent).relatedTarget === null) closeLatch();
}

function onWindowBlur(event: Event): void {
  // Only the window losing focus says anything about where the pointer is.
  // blur does not bubble, but a capture listener on window sits on the
  // capture path of every element's blur, so without this test focus moving
  // from one input to the next would close the latch and the next real
  // pointer move would be spent reopening it. An element target is the one
  // case that is NOT the window going away; a window blur targets the
  // Window and a document-level one targets the Document, and neither is an
  // Element. Comparing against the window object itself would be the
  // obvious test and is wrong under jsdom, where the global is a proxy and
  // the event carries the Window behind it. When the window really does
  // blur while an input is focused the browser fires both events, so the
  // latch still closes on the window's own. The security of the gated class
  // does not rest here in any case: a window that is not focused is refused
  // by document.hasFocus() whatever the latch says.
  if (event.target instanceof Element) return;
  closeLatch();
}

function onVisibility(): void {
  pageHidden = document.visibilityState === "hidden";
  if (pageHidden) closeLatch();
}

function tick(): void {
  if (!enabled || pageHidden || !eventSincePing) return;
  const now = Date.now();
  if (lastPingAt !== null && now - lastPingAt < PING_MIN_GAP_S * 1000) return;
  eventSincePing = false;
  lastPingAt = now;
  void postActivity().catch(() => {
    // A ping that failed is not retried on its own; the next real event
    // arms the next one, and a 423 has already been reported by client.ts.
  });
}

function syncTicker(): void {
  const wanted = uninstall !== null && enabled;
  if (wanted && ticker === null) {
    ticker = setInterval(tick, PING_CHECK_INTERVAL_MS);
  } else if (!wanted && ticker !== null) {
    clearInterval(ticker);
    ticker = null;
  }
}

/**
 * Switch the tracker on for an unlocked vault and off for a locked one.
 * Fed by VaultGate from the vault status; safe to call before install.
 */
export function setActivityTrackerEnabled(on: boolean): void {
  if (enabled === on) return;
  enabled = on;
  if (!on) {
    // A lock ends the session: nothing that happened before it may produce
    // a ping after the next unlock, and the pointer is nowhere in particular.
    eventSincePing = false;
    closeLatch();
    anchorX = null;
    anchorY = null;
  }
  syncTicker();
}

/**
 * Attach the listeners to the window. Returns the uninstaller. Installing
 * again first removes the previous installation, so there is never more than
 * one set of listeners.
 */
export function installActivityTracker(): () => void {
  uninstall?.();
  stamp = null;
  lastPingAt = null;
  eventSincePing = false;
  pointerInside = false;
  anchorX = null;
  anchorY = null;
  pageHidden = document.visibilityState === "hidden";

  const root = document.documentElement;
  for (const type of SELF_PROVING_EVENTS) {
    window.addEventListener(type, onSelfProving, LISTEN);
  }
  for (const type of GATED_EVENTS) {
    window.addEventListener(type, onGated, LISTEN);
  }
  document.addEventListener("pointerout", onPointerOut, LISTEN);
  root.addEventListener("pointerleave", closeLatch, LISTEN);
  window.addEventListener("blur", onWindowBlur, LISTEN);
  window.addEventListener("pointercancel", closeLatch, LISTEN);
  document.addEventListener("visibilitychange", onVisibility, LISTEN);

  const remove = (): void => {
    for (const type of SELF_PROVING_EVENTS) {
      window.removeEventListener(type, onSelfProving, LISTEN);
    }
    for (const type of GATED_EVENTS) {
      window.removeEventListener(type, onGated, LISTEN);
    }
    document.removeEventListener("pointerout", onPointerOut, LISTEN);
    root.removeEventListener("pointerleave", closeLatch, LISTEN);
    window.removeEventListener("blur", onWindowBlur, LISTEN);
    window.removeEventListener("pointercancel", closeLatch, LISTEN);
    document.removeEventListener("visibilitychange", onVisibility, LISTEN);
    uninstall = null;
    enabled = false;
    eventSincePing = false;
    closeLatch();
    syncTicker();
  };
  uninstall = remove;
  syncTicker();
  return remove;
}
