import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { useDraftStore } from "@/lib/store/draftStore";

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
