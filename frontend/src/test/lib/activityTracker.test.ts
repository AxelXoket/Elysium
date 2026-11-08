/**
 * activityTracker.test.ts - what "somebody is here" means, measured.
 *
 * The idle lock only ever saw HTTP requests; the tracker under test turns
 * real input into a ping. Every claim here is made through the module's
 * public surface (the stamp, and the POST the mocked backend receives) with
 * real DOM events on the real window, in the real capture phase.
 *
 * Two jsdom facts shape the file, and both are used rather than worked
 * around. First, event.isTrusted is unforgeable and dispatchEvent() forces
 * it false, so the tracker's accept path can only be reached with events
 * jsdom itself marks trusted: helpers/trustedEvents.ts fires those through
 * jsdom's own dispatch helper, and the plain dispatchEvent everyone else
 * uses is this file's ground control for the trust check. Second,
 * document.hasFocus() is false until something is really focused, so the
 * gated tests focus a real input (jsdom answers hasFocus() from its record
 * of the focused element) and blur it to take focus away again.
 *
 * Every constant is imported: the event lists, the gap, the threshold, the
 * tick. Nothing here retypes a number the module owns.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ACTIVITY_EVENTS,
  GATED_EVENTS,
  MOVE_THRESHOLD_PX,
  PING_CHECK_INTERVAL_MS,
  PING_MIN_GAP_S,
  SELF_PROVING_EVENTS,
  installActivityTracker,
  lastActivityAt,
  setActivityTrackerEnabled,
} from "@/lib/activity/installActivityTracker";
import { postActivity } from "@/lib/api/activity";
import { isApiError, setVaultLockedHandler } from "@/lib/api/client";
import { fireTrusted } from "@/test/helpers/trustedEvents";
import { mockFetch } from "@/test/mocks/api";

const GAP_MS = PING_MIN_GAP_S * 1000;

/** The mocked backend: POST /activity answers 204 with no body, which is the
 *  only honest shape for it (a Response with a body and a 204 throws). */
function pingBackend(status = 204) {
  return mockFetch({ "POST /activity": { status, body: undefined } });
}

function pings(fetchMock: ReturnType<typeof mockFetch>): number {
  return fetchMock.mock.calls.filter(
    ([input, init]) =>
      String(input).endsWith("/activity") &&
      (init?.method ?? "GET").toUpperCase() === "POST",
  ).length;
}

let uninstall: (() => void) | null = null;
let focusHolder: HTMLInputElement | null = null;

/** Real focus. jsdom's hasFocus() reports whether an element is focused, so
 *  a real element.focus() is the smallest correct way to make it true and
 *  blur() the way back. focus() also fires a trusted focusin, which the
 *  tracker accepts; every test takes its baseline AFTER this. */
function takeFocus(): void {
  focusHolder = document.createElement("input");
  document.body.appendChild(focusHolder);
  focusHolder.focus();
  if (!document.hasFocus()) throw new Error("jsdom did not record the focus");
}

function dropFocus(): void {
  if (focusHolder) {
    focusHolder.blur();
    focusHolder.remove();
    focusHolder = null;
  }
  if (document.hasFocus()) throw new Error("jsdom still reports focus");
}

/** Change visibilityState and say nothing about it. This is the case the
 *  gated class carries its own inline visibility check for: the page is
 *  hidden and no visibilitychange arrives, so the latch never closes and the
 *  ticker never learns. setVisibility() below announces the change instead,
 *  which puts a different gate in the way. */
function setVisibilityQuietly(state: "visible" | "hidden"): void {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => state,
  });
}

function setVisibility(state: "visible" | "hidden"): void {
  setVisibilityQuietly(state);
  document.dispatchEvent(new Event("visibilitychange"));
}

function restoreVisibility(): void {
  // The own property shadows jsdom's getter; removing it restores "visible".
  Reflect.deleteProperty(document, "visibilityState");
}

/** Fire and report whether the stamp moved. The fake clock is advanced
 *  first so a fresh acceptance is distinguishable from the previous one. */
function stamps(fire: () => void): boolean {
  vi.advanceTimersByTime(1);
  const before = lastActivityAt();
  fire();
  const after = lastActivityAt();
  return after !== before && after === Date.now();
}

/** A pointer event far from anything the anchor could be. */
function far(type: string, x = 400, y = 400): void {
  fireTrusted(type, document.body, { clientX: x, clientY: y });
}

/** Focus, then put the anchor at (x, y). The first gated event after
 *  install opens the latch and places the anchor WITHOUT counting: there is
 *  nothing to measure a distance from yet, and that is not a free
 *  acceptance. Every test that needs an anchor starts here. */
function anchorAt(x: number, y: number): void {
  takeFocus();
  if (stamps(() => fireTrusted("pointerover", document.body, { clientX: x, clientY: y }))) {
    throw new Error("the first gated event counted as activity");
  }
}

/** The same event type, dispatched the ordinary way: never trusted. */
function untrusted(type: string): void {
  const init = { bubbles: true, clientX: 400, clientY: 400, key: "a" };
  const event =
    type === "keydown"
      ? new KeyboardEvent(type, init)
      : type === "wheel"
        ? new WheelEvent(type, init)
        : type === "focusin"
          ? new FocusEvent(type, init)
          : type.startsWith("pointer")
            ? new MouseEvent(type, init)
            : new Event(type, init);
  document.body.dispatchEvent(event);
}

beforeEach(() => {
  vi.useFakeTimers();
  uninstall = installActivityTracker();
  setActivityTrackerEnabled(true);
});

afterEach(() => {
  dropFocus();
  uninstall?.();
  uninstall = null;
  setVaultLockedHandler(null);
  restoreVisibility();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("the trusted-event helper, the ground under everything below", () => {
  it("fires what the browser would mark trusted, and dispatchEvent does not", () => {
    const seen: boolean[] = [];
    const record = (event: Event) => {
      seen.push(event.isTrusted);
    };
    window.addEventListener("keydown", record, { capture: true });
    try {
      fireTrusted("keydown", document.body, { key: "a" });
      untrusted("keydown");
    } finally {
      window.removeEventListener("keydown", record, { capture: true });
    }
    expect(seen).toEqual([true, false]);
  });
});

describe("T-13: every listed event counts, and only when trusted", () => {
  it("listens to exactly the seven events KARAR-G names, in their two classes", () => {
    // The names here are the specification (KARAR-G and FIX 1 name them),
    // not a copy of the module's constant: the derived it.each below cannot
    // notice a name that was REMOVED from the list, because its case
    // disappears with it. This is the test that does.
    for (const name of ["keydown", "focusin", "pointerdown", "touchstart", "wheel"]) {
      expect(SELF_PROVING_EVENTS, `${name} must be self-proving`).toContain(name);
    }
    for (const name of ["pointermove", "pointerover"]) {
      expect(GATED_EVENTS, `${name} must be gated`).toContain(name);
    }
    expect(ACTIVITY_EVENTS).toHaveLength(7);
    expect(ACTIVITY_EVENTS).not.toContain("scroll");
  });

  it.each(["click", "keyup", "mousemove"])("%s is not listened to, even trusted", (type) => {
    // The complement of the list: events a person also produces but that
    // the plan left out (a click already arrived as pointerdown; keyup and
    // mousemove would double-count). An event added to either class turns
    // its row here red, which is what the union assertion this replaces
    // could never do.
    expect(ACTIVITY_EVENTS).not.toContain(type);
    takeFocus();
    far("pointerover", 0, 0);
    expect(stamps(() => fireTrusted(type, document.body, { key: "a" }))).toBe(false);
    // Ground: a listed event in the same state counts.
    expect(stamps(() => fireTrusted("keydown", document.body, { key: "a" }))).toBe(true);
  });

  it.each([...ACTIVITY_EVENTS])("%s updates the stamp when trusted", (type) => {
    // Gate satisfied for the gated pair: focus, visible, the latch opened by
    // a pointerover at the origin (which also puts the anchor there), and a
    // point far from it. A pointermove with the latch still closed is
    // rejected on purpose - that is T-14d - so the priming is not optional.
    takeFocus();
    far("pointerover", 0, 0);
    expect(stamps(() => far(type))).toBe(true);
  });

  it.each([...ACTIVITY_EVENTS])("%s is ignored when untrusted", (type) => {
    takeFocus();
    far("pointerover", 0, 0); // latch open, anchor at the origin
    expect(stamps(() => untrusted(type))).toBe(false);
  });

  it("hover counts: a trusted pointerover past the threshold updates the stamp", () => {
    // My "hovering" half, and the one thing M-07-1 (pointerover
    // dropped from the list) turns red.
    anchorAt(0, 0);
    expect(stamps(() => far("pointerover"))).toBe(true);
  });

  it("touch counts: a trusted touchstart updates the stamp", () => {
    expect(stamps(() => fireTrusted("touchstart", document.body))).toBe(true);
  });
});

describe("T-14: capture phase, past a component that stops propagation", () => {
  it("still sees an event a bubble-phase listener stopped", () => {
    const stopper = document.createElement("div");
    document.body.appendChild(stopper);
    stopper.addEventListener("keydown", (event) => event.stopPropagation());
    // Control: a bubble-phase listener on window really is starved.
    const bubbleSaw = vi.fn();
    window.addEventListener("keydown", bubbleSaw);
    try {
      expect(stamps(() => fireTrusted("keydown", stopper, { key: "a" }))).toBe(true);
      expect(bubbleSaw).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener("keydown", bubbleSaw);
      stopper.remove();
    }
  });
});

describe("T-14z: the ping is born from an event, never from the timer", () => {
  it("GROUND: a full gap with no event sends nothing", async () => {
    const backend = pingBackend();
    await vi.advanceTimersByTimeAsync(GAP_MS + PING_CHECK_INTERVAL_MS);
    expect(pings(backend)).toBe(0);
  });

  it("POSITIVE: one event, then a full gap, sends exactly one ping", async () => {
    const backend = pingBackend();
    fireTrusted("keydown", document.body, { key: "a" });
    await vi.advanceTimersByTimeAsync(GAP_MS);
    expect(pings(backend)).toBe(1);
    const [, init] = backend.mock.calls[0];
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeUndefined();
  });

  it("an event inside the gap waits for it, then one more ping goes", async () => {
    const backend = pingBackend();
    fireTrusted("keydown", document.body, { key: "a" });
    await vi.advanceTimersByTimeAsync(PING_CHECK_INTERVAL_MS);
    expect(pings(backend)).toBe(1);

    fireTrusted("keydown", document.body, { key: "b" });
    await vi.advanceTimersByTimeAsync(GAP_MS - 2 * PING_CHECK_INTERVAL_MS);
    expect(pings(backend), "a second ping inside the gap").toBe(1);

    await vi.advanceTimersByTimeAsync(2 * PING_CHECK_INTERVAL_MS);
    expect(pings(backend)).toBe(2);
  });
});

describe("the distance threshold", () => {
  it("a move under MOVE_THRESHOLD_PX does not count; one at it does", () => {
    anchorAt(100, 100);
    expect(
      stamps(() => far("pointermove", 100 + MOVE_THRESHOLD_PX - 1, 100)),
    ).toBe(false);
    expect(stamps(() => far("pointermove", 100 + MOVE_THRESHOLD_PX, 100))).toBe(true);
  });
});

describe("T-14a: the focus gate is selective, not a blanket", () => {
  it("without focus, a big move and a hover count for nothing and ping nothing", async () => {
    const backend = pingBackend();
    expect(document.hasFocus()).toBe(false);
    // The first gated event of a session only places the anchor, so it is
    // spent here rather than left to answer for the focus gate below: both
    // assertions that follow fail for the focus reason and no other.
    far("pointerover", 0, 0);
    expect(stamps(() => far("pointerover"))).toBe(false);
    expect(stamps(() => far("pointermove", 800, 800))).toBe(false);
    await vi.advanceTimersByTimeAsync(GAP_MS + PING_CHECK_INTERVAL_MS);
    expect(pings(backend)).toBe(0);
  });

  it("GROUND: with focus the same two events count", () => {
    anchorAt(0, 0);
    expect(stamps(() => far("pointerover"))).toBe(true);
    expect(stamps(() => far("pointermove", 800, 800))).toBe(true);
  });

  it("POSITIVE: without focus a keydown still counts", () => {
    expect(document.hasFocus()).toBe(false);
    expect(stamps(() => fireTrusted("keydown", document.body, { key: "a" }))).toBe(true);
  });
});

describe("the visibility gate, without a visibilitychange to lean on", () => {
  it("a hidden page rejects a gated move even when nobody announced the change", () => {
    // The gated class tests visibilityState itself rather than trusting the
    // pageHidden flag the visibilitychange listener keeps, because a host
    // can leave the page hidden without firing the event. Focus is held for
    // the whole test, so gate (1) cannot be what rejects anything below, and
    // no visibilitychange is sent, so the latch stays open and gate (3)
    // cannot be it either. Visibility is the only gate left standing.
    anchorAt(0, 0);
    // GROUND: while visible, this exact move counts.
    expect(stamps(() => far("pointermove", 300, 300))).toBe(true);

    setVisibilityQuietly("hidden");
    expect(document.hasFocus()).toBe(true);
    expect(stamps(() => far("pointermove", 900, 900))).toBe(false);
    expect(stamps(() => far("pointerover", 1500, 1500))).toBe(false);

    // GROUND the other way: nothing above was consumed or latched shut, and
    // the same move counts again the moment the page is visible.
    setVisibilityQuietly("visible");
    expect(stamps(() => far("pointermove", 900, 900))).toBe(true);
  });

  it("POSITIVE: a keydown still counts on a page hidden that way", () => {
    // The gate belongs to the gated pair alone. It is not a global mute, and
    // somebody typing into a window the host calls hidden is still here.
    takeFocus();
    setVisibilityQuietly("hidden");
    expect(stamps(() => fireTrusted("keydown", document.body, { key: "a" }))).toBe(true);
  });
});

describe("T-14b: hovering in place is not activity", () => {
  it("repeated pointerovers at one coordinate do not update the stamp", () => {
    anchorAt(50, 50);
    for (let i = 0; i < 3; i += 1) {
      expect(stamps(() => far("pointerover", 50, 50))).toBe(false);
    }
  });
});

describe("T-14c: the anchor is the last ACCEPTED point", () => {
  const STEP_PX = 3;
  const STEPS = 20;

  it("twenty three-pixel wobbles around a point never count", () => {
    anchorAt(100, 100);
    let accepted = 0;
    for (let i = 0; i < STEPS; i += 1) {
      const x = i % 2 === 0 ? 100 + STEP_PX : 100 - STEP_PX;
      if (stamps(() => far("pointermove", x, 100))) accepted += 1;
    }
    expect(accepted).toBe(0);
  });

  it("twenty three-pixel steps in one direction count exactly as often as they cross the threshold from the last accepted point", () => {
    // From the anchor, every ceil(threshold / step)th step is at least the
    // threshold away from the LAST ACCEPTED point and moves it; the steps
    // between are under it. With the anchor moving on every event instead
    // (M-07-12) no step is ever far enough and this is zero; with an anchor
    // that never moved, every step past the first crossing would count.
    const stepsPerAcceptance = Math.ceil(MOVE_THRESHOLD_PX / STEP_PX);
    const expected = Math.floor(STEPS / stepsPerAcceptance);
    expect(expected).toBeGreaterThanOrEqual(1);

    anchorAt(100, 100);
    let accepted = 0;
    for (let i = 1; i <= STEPS; i += 1) {
      if (stamps(() => far("pointermove", 100 + i * STEP_PX, 100))) accepted += 1;
    }
    expect(accepted).toBe(expected);
  });
});

describe("T-14d: the pointer-inside latch", () => {
  it("closes on pointerleave, stays closed for a move, reopens on the next pointerover", () => {
    anchorAt(0, 0);
    document.documentElement.dispatchEvent(new MouseEvent("pointerleave"));

    expect(stamps(() => far("pointermove", 300, 300))).toBe(false);
    // The pointerover is sent AT the anchor, so it is under the threshold
    // and cannot be what updates the stamp below.
    expect(stamps(() => far("pointerover", 0, 0))).toBe(false);
    expect(stamps(() => far("pointermove", 300, 300))).toBe(true);
  });

  it("a pointermove after pointerleave reopens it without counting; the next move past the threshold counts", () => {
    // The ruling: the latch opens on a gated pointerover OR pointermove, but
    // the four gates are judged against the latch as it stood before the
    // event, so the opener itself is never activity.
    anchorAt(0, 0);
    document.documentElement.dispatchEvent(new MouseEvent("pointerleave"));

    expect(stamps(() => far("pointermove", 300, 300))).toBe(false);
    expect(stamps(() => far("pointermove", 600, 600))).toBe(true);
  });

  const closers: [string, () => void][] = [
    [
      "pointerout on the document with no relatedTarget",
      () => document.dispatchEvent(new MouseEvent("pointerout", { relatedTarget: null })),
    ],
    ["window blur", () => window.dispatchEvent(new Event("blur"))],
    ["pointercancel", () => window.dispatchEvent(new MouseEvent("pointercancel"))],
    ["the page going hidden", () => setVisibility("hidden")],
  ];

  it.each(closers)("%s closes it too", (_name, close) => {
    anchorAt(0, 0);
    // Ground: open latch, far move counts.
    expect(stamps(() => far("pointermove", 300, 300))).toBe(true);
    close();
    restoreVisibility();
    expect(stamps(() => far("pointermove", 600, 600))).toBe(false);
  });

  it("a blur inside the page does NOT close it, only the window's own does", () => {
    // Positive control for the target test in onWindowBlur. blur does not
    // bubble, but the closer is registered on window in the capture phase,
    // so it is on the capture path of every element's blur; without the
    // test, tabbing from one input to the next would shut the latch and the
    // next real pointer move would be spent reopening it. The "window blur"
    // row above is the ground: a blur of the window itself still closes it.
    anchorAt(0, 0);
    expect(stamps(() => far("pointermove", 300, 300))).toBe(true);

    const next = document.createElement("input");
    document.body.appendChild(next);
    next.focus(); // fires blur on the input takeFocus() focused
    expect(document.hasFocus()).toBe(true);

    expect(stamps(() => far("pointermove", 600, 600))).toBe(true);

    next.blur();
    next.remove();
  });

  it("a pointerout that has a relatedTarget is a move between elements, not an exit", () => {
    // Positive control for the relatedTarget condition: without it every
    // hover from one element to the next would close the latch.
    anchorAt(0, 0);
    document.dispatchEvent(
      new MouseEvent("pointerout", { relatedTarget: document.body }),
    );
    expect(stamps(() => far("pointermove", 300, 300))).toBe(true);
  });

  it("a trusted pointerdown reopens it as well", () => {
    anchorAt(0, 0);
    document.documentElement.dispatchEvent(new MouseEvent("pointerleave"));
    // pointerdown is self-proving, so it counts by itself; what matters here
    // is that the very next move counts, which only a latch the pointerdown
    // opened explains (a move that had to open it would not count).
    expect(stamps(() => far("pointerdown", 0, 0))).toBe(true);
    expect(stamps(() => far("pointermove", 300, 300))).toBe(true);
  });
});

describe("the first gated event only places the anchor", () => {
  it("after install: two moves, and only the second one counts", () => {
    // The sequence as a person makes it, with nothing primed: the pointer
    // crosses the window once, and only the move after that has a point to
    // be measured against. Before this rule the first one was accepted
    // whatever its distance, because the anchor was unset.
    takeFocus();
    expect(stamps(() => far("pointermove", 400, 400))).toBe(false);
    expect(stamps(() => far("pointermove", 800, 800))).toBe(true);
  });

  it("after install: one big move counts for nothing, the next one past the threshold counts", () => {
    takeFocus();
    // The latch is opened by a self-proving pointerdown, which never touches
    // the anchor, so what is measured below is the anchor rule alone.
    fireTrusted("pointerdown", document.body, { clientX: 0, clientY: 0 });
    expect(stamps(() => far("pointermove", 400, 400))).toBe(false);
    expect(stamps(() => far("pointermove", 800, 800))).toBe(true);
  });

  it("after a disable and enable, the same", () => {
    anchorAt(0, 0);
    expect(stamps(() => far("pointermove", 300, 300))).toBe(true);
    setActivityTrackerEnabled(false);
    setActivityTrackerEnabled(true);
    fireTrusted("pointerdown", document.body, { clientX: 300, clientY: 300 });
    expect(stamps(() => far("pointermove", 700, 700))).toBe(false);
    expect(stamps(() => far("pointermove", 1100, 1100))).toBe(true);
  });
});

describe("T-14e: programmatic scrolling is not a person", () => {
  it("scroll is not listened to, and a trusted scroll does not count", () => {
    expect(ACTIVITY_EVENTS).not.toContain("scroll");
    takeFocus();
    expect(stamps(() => fireTrusted("scroll", document.body))).toBe(false);
    // Ground: the wheel that a person's scroll produces does count.
    expect(stamps(() => fireTrusted("wheel", document.body))).toBe(true);
  });
});

describe("a hidden page is silent", () => {
  it("sends no ping while hidden and resumes when visible again", async () => {
    const backend = pingBackend();
    fireTrusted("keydown", document.body, { key: "a" });
    setVisibility("hidden");
    await vi.advanceTimersByTimeAsync(GAP_MS + PING_CHECK_INTERVAL_MS);
    expect(pings(backend)).toBe(0);

    // POSITIVE: the event that happened is reported once the page is back.
    setVisibility("visible");
    await vi.advanceTimersByTimeAsync(PING_CHECK_INTERVAL_MS);
    expect(pings(backend)).toBe(1);
  });
});

describe("enable and disable, as the vault gate drives it", () => {
  it("disabled, nothing counts and nothing is pinged; enabled, both resume", async () => {
    const backend = pingBackend();
    setActivityTrackerEnabled(false);
    expect(stamps(() => fireTrusted("keydown", document.body, { key: "a" }))).toBe(false);
    await vi.advanceTimersByTimeAsync(GAP_MS + PING_CHECK_INTERVAL_MS);
    expect(pings(backend)).toBe(0);

    setActivityTrackerEnabled(true);
    expect(stamps(() => fireTrusted("keydown", document.body, { key: "a" }))).toBe(true);
    await vi.advanceTimersByTimeAsync(PING_CHECK_INTERVAL_MS);
    expect(pings(backend)).toBe(1);
  });

  it("an event from before a lock does not ping after the unlock", async () => {
    const backend = pingBackend();
    fireTrusted("keydown", document.body, { key: "a" });
    setActivityTrackerEnabled(false);
    setActivityTrackerEnabled(true);
    await vi.advanceTimersByTimeAsync(GAP_MS + PING_CHECK_INTERVAL_MS);
    expect(pings(backend)).toBe(0);
  });
});

describe("the ping's transport", () => {
  it("resolves on a 204 with no body", async () => {
    const backend = pingBackend();
    await expect(postActivity()).resolves.toBeUndefined();
    expect(pings(backend)).toBe(1);
  });

  it("treats a body on /activity as a shape error, not as data", async () => {
    mockFetch({ "POST /activity": { status: 200, body: { ok: true } } });
    const failure = await postActivity().catch((err: unknown) => err);
    expect(isApiError(failure) && failure.detail).toBe("invalid_response_shape");
  });

  it("lets a 423 reach the lock signal, once per burst", async () => {
    mockFetch({ "POST /activity": { status: 423, body: { detail: "vault_locked" } } });
    const locked = vi.fn();
    setVaultLockedHandler(locked);
    const first = await postActivity().catch((err: unknown) => err);
    const second = await postActivity().catch((err: unknown) => err);
    expect(isApiError(first) && first.status).toBe(423);
    expect(isApiError(second) && second.status).toBe(423);
    expect(locked).toHaveBeenCalledTimes(1);
  });
});
