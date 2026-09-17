/**
 * Trusted synthetic events under jsdom.
 *
 * The DOM makes event.isTrusted unforgeable: it is an own, non-configurable
 * accessor on the event, and dispatchEvent() sets it to false before
 * dispatching, so nothing page script does can produce a trusted event. That
 * is the property the activity tracker relies on, and it is also why a test
 * cannot reach the tracker's accept path with dispatchEvent.
 *
 * jsdom dispatches its OWN events (focus after element.focus(), load,
 * readystatechange) through an internal helper that creates the event with
 * isTrusted true and dispatches it without the reset. This file reaches that
 * helper, so a test fires exactly what a browser fires for a real key or a
 * real pointer: no seam in production code, no toggle that accepts untrusted
 * events in test builds, and the ground control is the plain dispatchEvent
 * everyone else uses. The coupling is to jsdom's file layout (jsdom 29, which
 * has no exports map); activityTracker.test.ts's first test fails loudly if a
 * jsdom upgrade moves it.
 */
import EventIdl from "jsdom/lib/generated/idl/Event.js";
import FocusEventIdl from "jsdom/lib/generated/idl/FocusEvent.js";
import KeyboardEventIdl from "jsdom/lib/generated/idl/KeyboardEvent.js";
import PointerEventIdl from "jsdom/lib/generated/idl/PointerEvent.js";
import TouchEventIdl from "jsdom/lib/generated/idl/TouchEvent.js";
import WheelEventIdl from "jsdom/lib/generated/idl/WheelEvent.js";
import { implForWrapper } from "jsdom/lib/generated/idl/utils.js";
import { fireAnEvent } from "jsdom/lib/jsdom/living/helpers/events.js";

/** Which jsdom event class each type is created with. Anything not listed is
 *  a plain Event, which is what a scroll or a visibilitychange is. */
const INTERFACES: Record<string, unknown> = {
  keydown: KeyboardEventIdl,
  keyup: KeyboardEventIdl,
  focusin: FocusEventIdl,
  focusout: FocusEventIdl,
  blur: FocusEventIdl,
  pointerdown: PointerEventIdl,
  pointerup: PointerEventIdl,
  pointermove: PointerEventIdl,
  pointerover: PointerEventIdl,
  pointerout: PointerEventIdl,
  pointerleave: PointerEventIdl,
  pointercancel: PointerEventIdl,
  touchstart: TouchEventIdl,
  wheel: WheelEventIdl,
};

/**
 * Fire a TRUSTED event of `type` at `target`, the way the browser itself
 * would. `init` takes the same fields as the matching event constructor
 * (clientX, key, relatedTarget, bubbles, ...); bubbles defaults to true so
 * the event reaches window like a real one does.
 */
export function fireTrusted(
  type: string,
  target: EventTarget,
  init: Record<string, unknown> = {},
): void {
  const iface = INTERFACES[type] ?? EventIdl;
  fireAnEvent(type, implForWrapper(target), iface, {
    bubbles: true,
    cancelable: true,
    ...init,
  });
}
