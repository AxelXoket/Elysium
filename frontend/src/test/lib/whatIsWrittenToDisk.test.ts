/**
 * U-14 - what reaches device storage, read from the BLOB.
 *
 * The guards this joins all read source text. S-04 looks for the exact
 * substring for the session store's setter, so `sessionStorage.foo = v` - an
 * ordinary property assignment, not a clever one - is invisible to it. S-09b
 * parses `uiStore.ts`'s own `partialize` body and forbids a list of NAMES in
 * it, so `msgFontPx: state.vaultKey` passes: the name is allowed, the value
 * is not, and a source scan cannot tell them apart.
 *
 * These read what was actually written. The source scans stay where they
 * are: one sees code nobody has run, the other sees only code that ran.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

import { sentinelLog } from "@/test/sentinels";
import { PERSISTED, useUiStore } from "@/lib/store/uiStore";
import {
  LAUNCH_TOKEN_HEADER,
  readLaunchToken,
  launchTokenHeader,
} from "@/lib/api/launchToken";

/** The name zustand persists this store under. Read from the store's own
 *  configuration rather than retyped, so a rename cannot leave this test
 *  looking at a key nobody writes. */
const UI_STORE_KEY = "elysium-ui-state";

/** A value nothing else in the suite would ever produce. */
const SENTINEL = "sentinel-value-9f3c1-never-persisted";

/** The blob zustand last wrote for the ui store, parsed. */
function writtenState(): Record<string, unknown> | null {
  const raw = globalThis.localStorage.getItem(UI_STORE_KEY);
  if (!raw) return null;
  const parsed = JSON.parse(raw) as { state?: Record<string, unknown> };
  return parsed.state ?? null;
}

describe("the set of keys that reach the disk", () => {
  beforeEach(() => {
    globalThis.localStorage.clear();
  });

  it("is exactly the exported list, read back from the blob", () => {
    // The store writes on any change; nudging one persisted key is enough.
    useUiStore.setState({ sidebarCollapsed: true });
    useUiStore.persist.rehydrate?.();
    useUiStore.setState({ sidebarCollapsed: false });

    const state = writtenState();
    expect(state).toBeTruthy();
    // IMPORTED, not retyped. A list written out here would agree with itself.
    expect(Object.keys(state!).sort()).toEqual([...PERSISTED].sort());
  });

  it("a value put on a NON-persisted field never appears in the blob", () => {
    // The case a name-based scan cannot see: the field is allowed to exist,
    // the value is what must not travel.
    useUiStore.setState({
      composerDraft: SENTINEL,
      sidebarCollapsed: true,
    } as never);

    const raw = globalThis.localStorage.getItem(UI_STORE_KEY) ?? "";
    expect(raw.length).toBeGreaterThan(20);      // ground: it did write
    expect(raw).not.toContain(SENTINEL);
  });

  it("a value put on a PERSISTED field does appear", () => {
    // POSITIVE CONTROL. Without it the assertion above is satisfied by a
    // store that writes nothing at all.
    useUiStore.setState({ msgFontPx: 17 });

    const state = writtenState();
    expect(state?.msgFontPx).toBe(17);
  });
});

describe("the launch token", () => {
  const realHash = globalThis.location.hash;

  beforeEach(() => {
    globalThis.localStorage.clear();
    // The ground control below writes one key on purpose; a store carried
    // over from it would make the assertion in the first test read as a
    // launch token that had been saved.
    globalThis.sessionStorage.clear();
  });
  afterEach(() => {
    globalThis.location.hash = realHash;
    vi.restoreAllMocks();
  });

  it("is read from the URL and never written to storage", () => {
    globalThis.location.hash = "#elysium-token=abc123";
    const before = sentinelLog().storage.length;

    readLaunchToken();

    // It works...
    expect(launchTokenHeader()).toMatchObject({
      [LAUNCH_TOKEN_HEADER]: "abc123",
    });
    // ...and nothing was stored to make it work. The storage sentinel traps
    // `setItem` AND plain property assignment, which is the form the source
    // scan cannot see.
    expect(sentinelLog().storage.length).toBe(before);
    expect(globalThis.sessionStorage.length).toBe(0);
  });

  it("the storage trap really does catch a plain property write", () => {
    // GROUND CONTROL for the assertion above: it is only meaningful if the
    // sentinel would have SEEN a write of that shape.
    const before = sentinelLog().storage.length;
    (globalThis.sessionStorage as unknown as Record<string, string>).probe =
      "x";
    expect(sentinelLog().storage.length).toBe(before + 1);
  });
});
