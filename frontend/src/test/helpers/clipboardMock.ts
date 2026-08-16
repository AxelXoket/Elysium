/**
 * clipboardMock.ts - jsdom has no Clipboard implementation at all, so
 * `navigator.clipboard` is undefined and `vi.spyOn(navigator.clipboard, ...)`
 * throws before it can spy on anything. The property has to be defined.
 *
 * ORDER MATTERS, and getting it wrong fails quietly. `userEvent.setup()`
 * unconditionally installs its own clipboard stub over `window.navigator`.
 * Install this AFTER setup() or the stub replaces it and the assertions
 * watch a mock nothing calls.
 */
import { vi } from "vitest";

export interface ClipboardMock {
  writeText: ReturnType<typeof vi.fn>;
  restore: () => void;
}

function swap(value: unknown): () => void {
  const previous = Object.getOwnPropertyDescriptor(navigator, "clipboard");
  Object.defineProperty(navigator, "clipboard", {
    value,
    configurable: true,
    writable: true,
  });
  return () => {
    if (previous) Object.defineProperty(navigator, "clipboard", previous);
    else Reflect.deleteProperty(navigator as unknown as object, "clipboard");
  };
}

export function installClipboardMock(
  impl: (text: string) => Promise<void> = () => Promise.resolve(),
): ClipboardMock {
  const writeText = vi.fn(impl);
  const restore = swap({ writeText });
  return { writeText, restore };
}

/** The environment where the API is missing entirely, not merely refusing. */
export function removeClipboardApi(): () => void {
  return swap(undefined);
}
