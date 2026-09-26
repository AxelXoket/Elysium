import { useState } from "react";

/**
 * The last non-null value, kept after the value goes back to null.
 *
 * A Collapse closes by animating its content's height down to nothing, so it
 * needs that content to still be there while it closes. A notice built from
 * state that has just been cleared (an error string, a refusal) would empty
 * the Collapse first and it would snap shut instead. This hands the Collapse
 * the last thing it showed; the Collapse's own inert and aria-hidden take it
 * away from readers and the tab order the moment it starts closing.
 */
export function useHeldValue<T>(value: T | null | undefined): T | null {
  const [held, setHeld] = useState<T | null>(value ?? null);
  if (value != null && value !== held) setHeld(value);
  return value ?? held;
}
