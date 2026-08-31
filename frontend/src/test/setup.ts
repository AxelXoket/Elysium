import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { useDraftStore } from "@/lib/store/draftStore";
// Imported for its side effect, and the side effect is the point. This module
// writes to storage while it is being LOADED, which is the one thing the
// setupFiles order exists to catch: sentinels.ts is listed before this file
// so the trap is already in place when this import runs. Swap the two and the
// write happens unobserved, and sentinels.test.ts goes red saying so.
//
// It has to be imported from HERE rather than from a test file. A test file
// loads after every setup file, whatever order they are in, so an import
// there proves nothing about the order at all - measured, by swapping them
// and watching nothing fail.
import "./helpers/writesWhileBeingImported";

/**
 * Drafts outlive components on purpose, which means they also outlive TESTS.
 *
 * The draft store is module scope - that is the whole point of it, since a
 * component-scoped one is what let a vault lock destroy unsent text. The cost
 * is that a draft typed in one test is still there in the next one in the
 * same file, and several existing suites reuse chat id 1 and assert on an
 * empty composer. Reset between tests so the isolation the rest of the suite
 * assumes still holds; nothing in the app calls this.
 */
afterEach(() => {
  useDraftStore.getState().clearAll();
});

// Mock window.matchMedia for jsdom (not available by default)
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});
