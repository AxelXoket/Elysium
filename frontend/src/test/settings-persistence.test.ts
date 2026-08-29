/**
 * settings-persistence.test.ts - nothing the user sets should need setting
 * twice.
 *
 * Every preference in Elysium lives in exactly one of two places, and which
 * one is a PRIVACY decision, not a convenience one:
 *
 *   localStorage (uiStore.partialize)  scalars and enums that say nothing
 *                                      about a person - font size, contrast,
 *                                      sampling numbers, which model is picked
 *   encrypted vault (settings table)   anything content-bearing - the API key,
 *                                      the selected persona, the proxy, and
 *                                      the stop sequences (character names)
 *
 * The failure this guards against is the quiet one: a new preference gets a
 * `useState`, works perfectly for a session, and silently resets forever after.
 * Stop sequences did exactly that for two versions.
 *
 * (The OTHER half of "settings reset every launch" was not a storage bug at
 * all - the server bound a random port each start, so localStorage was keyed
 * to a new origin every time and all of it was dropped. Fixed in run_app.py;
 * pinned by test_release_hardening.py.)
 *
 * Until KADEME 18b this file proved all of the above by reading uiStore.ts as
 * TEXT and regex-matching the `partialize` literal. A source scan can pin a
 * DELETION; it cannot show that a value survives a reload, and every presence
 * claim here was that second thing - the describe promised "survives a
 * restart" while no store was ever built, written or reloaded. It now drives
 * the real store, reads the real localStorage, and reloads the real module.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";

import { useUiStore } from "@/lib/store/uiStore";
import { readDeviceNarration } from "@/lib/voice/narrationMigration";
// Seeding a blob the store no longer produces cannot go through the store,
// and static-safety S-09 reserves direct device-storage writes for lib/store.
// This mock is the exemption that already exists for exactly that; writing
// here by hand would widen the rule instead of using it. (S-09 scans source
// text, comments included, so it is also why this sentence does not spell
// the call out - the same trap caught KADEME 18a.)
import { seedDeviceNarration, seedRawUiState } from "./mocks/legacyStorage";

/**
 * What the store writes to device storage. Exact, not a subset: localStorage
 * is not encrypted, so every ADDITION to this list is a privacy decision and
 * has to be made on purpose. A subset check would wave new keys through.
 */
const PERSISTED = [
  "selectedCharacterId",
  "selectedChatId",
  "activeRightPanelTab",
  "sidebarCollapsed",
  "rightPanelCollapsed",
  "msgFontPx",
  "msgLineHeight",
  "msgContrast",
  "narrationEnabled",
  "quoteTintEnabled",
  "continuousVoice",
  "voiceHintDismissed",
  "narrationMigrated",
  "msgInk",
  "surfaceFinish",
  "msgOpacity",
  "chatBgOn",
  "chatBgLum",
  "chatBgContrast",
  "chatBgTint",
  "chatBgFocusX",
  "chatBgFocusY",
  "chatBgZoom",
  "chatBgAspect",
  "ambientFogOn",
  "genTemperature",
  "genTopP",
  "genTopK",
  "genRepetitionPenalty",
  "genMaxOutput",
  "genSeed",
  "genContextBudget",
] as const;

/** Names that have each been in the wrong home at some point. */
const NEVER = [
  // Lived here AND in the vault, written together only when the dropdown was
  // touched, so the two could disagree forever: one performance while a reply
  // streamed and another when the Speak button repeated it. A device copy is
  // the wrong home for a setting the server also has to know.
  "narrationVoice",
  // v1.2 privacy fix (audit finding): an OpenRouter model id such as
  // "anthropic/claude-3.5-sonnet" is a NAME a person reads on screen, not a
  // bare number - the shape the owner's own rule bans from ever sitting
  // outside the vault. It moved into the encrypted settings table (see
  // lib/query/settings.ts's useSetSelectedModel); version-3 `migrate` strips
  // the old plaintext copy out of every install that already has one, proven
  // below in "cleans a stale plaintext model id out of an existing install".
  // The other two selections next to it in the old blob - selectedChatId,
  // selectedCharacterId - are bare numbers and the owner's rule permits
  // those to stay device-local, so they remain in PERSISTED above.
  "selectedModelId",
  // Character names are user content and localStorage is not encrypted. The
  // answer was the encrypted settings table, not "do not persist at all" -
  // which is what made them a per-session retyping chore. The vault round
  // trip itself is proven in GenerationSettingsPanel.test.tsx ("FF7
  // persistence"), by remounting the provider and reading the chips back.
  "stopSequences",
  "genStopSequences",
  // A dialog that reopens itself on launch is a bug, not a preference.
  "settingsOpen",
  "settingsInitialPage",
  // Session-only: a repaint signal, and a measurement of the current window.
  "chatBgRev",
  "chatAreaAspect",
];

/**
 * The name uiStore files its blob under, learned by watching it write rather
 * than spelled here. Three modules spell it independently - uiStore.ts owns
 * it, lib/voice/narrationMigration.ts repeats it as a private const, and
 * mocks/legacyStorage.ts a third time - so a fourth copy, in the test whose
 * job is to notice a rename, would be the wrong direction.
 */
function keyTheStoreWrites(): string {
  localStorage.clear();
  useUiStore.getState().setMsgFontPx(15);
  expect(localStorage.length, "the store wrote nothing to device storage")
    .toBe(1);
  return localStorage.key(0)!;
}

/** The same, for the seed helper the legacy tests share. */
function keyTheSeedWrites(): string {
  localStorage.clear();
  seedRawUiState("{}");
  expect(localStorage.length, "the seed wrote nothing").toBe(1);
  return localStorage.key(0)!;
}

/** The blob as the next launch would find it. */
function stored(): Record<string, unknown> {
  expect(localStorage.length, "the store wrote nothing to device storage")
    .toBe(1);
  const parsed = JSON.parse(localStorage.getItem(localStorage.key(0)!)!);
  expect(parsed.state, "the persisted blob has no state").toBeTypeOf("object");
  return parsed.state as Record<string, unknown>;
}

/** A fresh module instance, i.e. what a relaunch builds. */
async function relaunch() {
  const store = (await import("@/lib/store/uiStore")).useUiStore;
  expect(store, "the module was reused, so nothing was rehydrated")
    .not.toBe(useUiStore);
  return store.getState();
}

describe("every preference survives a restart", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.resetModules();
  });

  it("writes exactly the preferences that are safe on an unencrypted disk", () => {
    // Any set at all makes the middleware write the whole partialized blob.
    useUiStore.getState().setMsgFontPx(17);

    const state = stored();
    expect(Object.keys(state).sort()).toEqual([...PERSISTED].sort());
    for (const name of NEVER) {
      expect(name in state, `${name} must not be written to device storage`)
        .toBe(false);
    }
  });

  it("writes back what the store actually holds, under the same name", () => {
    // A rename here is how an allowlisted key ends up aliasing something it
    // should not carry (static-safety S-09b owns the privacy half).
    useUiStore.getState().setMsgFontPx(16);
    useUiStore.getState().setGenSettings({ genTemperature: 1.3 });

    const state = stored();
    expect(Object.keys(state).length, "nothing to compare, the loop was empty")
      .toBe(PERSISTED.length);
    const live = useUiStore.getState() as unknown as Record<string, unknown>;
    for (const [name, value] of Object.entries(state)) {
      expect(live[name], `persisted "${name}" is not state.${name}`)
        .toEqual(value);
    }
  });

  it("hands a preference set now to the next launch", async () => {
    useUiStore.getState().setMsgFontPx(17);
    useUiStore.getState().setActiveRightPanelTab("persona");
    useUiStore.getState().setGenSettings({ genTemperature: 1.3, genTopK: 55 });
    // Transient by design, and the reason it is set here: without it,
    // "everything came back" could not be told apart from "the blob was
    // read whole and nothing was filtered".
    useUiStore.getState().setSettingsOpen(true);

    const next = await relaunch();
    expect(next.msgFontPx).toBe(17);
    expect(next.activeRightPanelTab).toBe("persona");
    expect(next.genTemperature).toBe(1.3);
    expect(next.genTopK).toBe(55);
    expect(next.settingsOpen, "a dialog reopened itself on launch").toBe(false);
  });

  it("opens on a tab that still exists after the rename", async () => {
    // Version 2 renamed the right-panel tabs. An install that skipped the
    // release in between carries "model", which matches no tab any more, so
    // without the migration the panel opens on nothing.
    seedRawUiState(
      JSON.stringify({ version: 0, state: { activeRightPanelTab: "model" } }),
    );

    expect((await relaunch()).activeRightPanelTab).toBe("models");
  });

  it("cleans a stale plaintext model id out of an existing install", async () => {
    // The migration that protects everyone who already has the leak: an
    // install from before v1.2 carries all three selections in this blob,
    // written by a version-2 store. THIS is the test that proves an existing
    // user's disk stops holding the plaintext model name - the other tests
    // in this file only prove a FRESH write is already clean, which a broken
    // migrate would pass trivially by never being exercised at all.
    seedRawUiState(
      JSON.stringify({
        version: 2,
        state: {
          selectedChatId: 7,
          selectedCharacterId: 3,
          selectedModelId: "anthropic/claude-3.5-sonnet",
          activeRightPanelTab: "models",
        },
      }),
    );

    // Not `relaunch()`: that helper discards the fresh module's store
    // reference and hands back only a state snapshot, and this test needs
    // the store itself to force a write afterwards. Same re-import it uses
    // internally, so this is still "what the next launch builds".
    const store = (await import("@/lib/store/uiStore")).useUiStore;
    expect(store, "the module was reused, so nothing was rehydrated")
      .not.toBe(useUiStore);
    const next = store.getState();

    // The bare numeric ids survive the migration untouched - the owner's own
    // rule permits them to stay device-local, so there is nothing to clean.
    expect(next.selectedChatId).toBe(7);
    expect(next.selectedCharacterId).toBe(3);
    // The model NAME does not: it is gone from the live store...
    expect(next.selectedModelId).toBeNull();

    // ...and, the part that actually protects an existing user, gone from
    // what lands back on disk.
    store.getState().setMsgFontPx(15); // force one write of the whole blob
    const blob = stored();
    expect(
      "selectedModelId" in blob,
      "the plaintext model id is still on disk after the migration ran",
    ).toBe(false);
    expect(blob.selectedChatId).toBe(7);
    expect(blob.selectedCharacterId).toBe(3);
  });

  it("is read by the narration migration under the name it writes", () => {
    // Three modules spell this name independently: uiStore.ts owns it,
    // lib/voice/narrationMigration.ts repeats it as a private const, and
    // test/mocks/legacyStorage.ts a third time. Nothing links them, so a
    // rename on the store side leaves the migration reading a key that no
    // longer exists - and the stale device copy it was written to clear
    // stays behind forever, without a word. Asserting the agreement rather
    // than the spelling is what keeps the test from becoming a fourth copy.
    expect(
      keyTheSeedWrites(),
      "the seed helper and the store disagree about where the blob lives",
    ).toBe(keyTheStoreWrites());

    localStorage.clear();
    seedDeviceNarration("narrator");
    expect(readDeviceNarration(), "the migration is reading another key")
      .toBe("narrator");
  });

  it("ignores a blob it cannot read instead of refusing to start", async () => {
    seedRawUiState("{not json at all");

    expect((await relaunch()).msgFontPx).toBe(14);
  });
});
