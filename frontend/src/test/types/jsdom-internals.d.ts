/**
 * Ambient declarations for the jsdom internals test/helpers/trustedEvents.ts
 * reaches. jsdom ships no types for its lib/ tree; these are the two
 * functions and the event classes that file uses, typed as loosely as they
 * are used. Test tree only: tsconfig.app.json excludes src/test.
 */
declare module "jsdom/lib/jsdom/living/helpers/events.js" {
  export function fireAnEvent(
    type: string,
    targetImpl: unknown,
    eventInterface?: unknown,
    attributes?: Record<string, unknown>,
    legacyTargetOverrideFlag?: boolean,
  ): boolean;
}

declare module "jsdom/lib/generated/idl/utils.js" {
  export function implForWrapper(wrapper: unknown): unknown;
}

declare module "jsdom/lib/generated/idl/Event.js" {
  const eventInterface: unknown;
  export default eventInterface;
}

declare module "jsdom/lib/generated/idl/FocusEvent.js" {
  const eventInterface: unknown;
  export default eventInterface;
}

declare module "jsdom/lib/generated/idl/KeyboardEvent.js" {
  const eventInterface: unknown;
  export default eventInterface;
}

declare module "jsdom/lib/generated/idl/PointerEvent.js" {
  const eventInterface: unknown;
  export default eventInterface;
}

declare module "jsdom/lib/generated/idl/TouchEvent.js" {
  const eventInterface: unknown;
  export default eventInterface;
}

declare module "jsdom/lib/generated/idl/WheelEvent.js" {
  const eventInterface: unknown;
  export default eventInterface;
}
