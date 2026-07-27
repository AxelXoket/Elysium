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
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import path from "path";

const SRC = path.resolve(__dirname, "..");
const uiStore = readFileSync(path.join(SRC, "lib", "store", "uiStore.ts"), "utf-8");

/** The keys uiStore actually writes to localStorage. */
function persistedKeys(): Set<string> {
  const head = /partialize:\s*\([^)]*\)\s*=>\s*\(\s*\{/.exec(uiStore);
  expect(head, "partialize(...) not found in uiStore").not.toBeNull();
  const open = head!.index + head![0].length - 1;
  let depth = 0;
  let close = -1;
  for (let i = open; i < uiStore.length; i += 1) {
    if (uiStore[i] === "{") depth += 1;
    else if (uiStore[i] === "}") {
      depth -= 1;
      if (depth === 0) {
        close = i;
        break;
      }
    }
  }
  const body = uiStore.slice(open + 1, close);
  return new Set([...body.matchAll(/(\w+):\s*state\./g)].map((m) => m[1]));
}

describe("every preference survives a restart", () => {
  const persisted = persistedKeys();

  it("keeps the chat selection and the chosen model", () => {
    // "Which model am I talking to" is the setting people notice losing first.
    for (const key of [
      "selectedCharacterId",
      "selectedChatId",
      "selectedModelId",
      "activeRightPanelTab",
      "sidebarCollapsed",
    ]) {
      expect(persisted.has(key), `${key} is not persisted`).toBe(true);
    }
  });

  it("keeps every generation sampling scalar", () => {
    for (const key of [
      "genTemperature",
      "genTopP",
      "genTopK",
      "genRepetitionPenalty",
      "genMaxOutput",
      "genSeed",
      "genContextBudget",
    ]) {
      expect(persisted.has(key), `${key} is not persisted`).toBe(true);
    }
  });

  it("keeps every appearance preference", () => {
    for (const key of [
      "msgFontPx",
      "msgLineHeight",
      "msgContrast",
      "msgInk",
      "surfaceFinish",
      "narrationEnabled",
      "quoteTintEnabled",
      "ambientFogOn",
      "chatBgOn",
      "chatBgLum",
      "chatBgContrast",
      "chatBgTint",
    ]) {
      expect(persisted.has(key), `${key} is not persisted`).toBe(true);
    }
  });

  it("keeps the voice preferences", () => {
    for (const key of ["continuousVoice", "narrationVoice", "voiceHintDismissed"]) {
      expect(persisted.has(key), `${key} is not persisted`).toBe(true);
    }
  });

  it("does NOT persist transient UI state", () => {
    // A dialog that reopens itself on launch is a bug, not a preference.
    for (const key of ["settingsOpen", "settingsInitialPage", "chatBgRev"]) {
      expect(persisted.has(key), `${key} must not be persisted`).toBe(false);
    }
  });

  it("keeps stop sequences OUT of browser storage, in the vault instead", () => {
    // Character names are user content; localStorage is not encrypted. The
    // answer is the encrypted settings table, not "do not persist at all" -
    // which is what made them a per-session retyping chore.
    expect(persisted.has("stopSequences")).toBe(false);
    expect(persisted.has("genStopSequences")).toBe(false);

    const context = readFileSync(
      path.join(SRC, "components", "generation", "GenerationSettingsContext.tsx"),
      "utf-8",
    );
    expect(context, "stop sequences no longer read from the vault")
      .toMatch(/useSettings\(\)/);
    expect(context, "stop sequences no longer written to the vault")
      .toMatch(/useSetStopSequences\(\)/);

    const schema = readFileSync(
      path.join(SRC, "lib", "schemas", "settings.ts"),
      "utf-8",
    );
    expect(schema).toMatch(/stop_sequences/);
  });

  it("every persisted entry mirrors a field of the same name", () => {
    // A rename here is how an allowlisted key ends up aliasing something it
    // should not carry (see static-safety S-09b for the privacy half).
    const body = uiStore.slice(uiStore.indexOf("partialize"));
    for (const m of body.matchAll(/(\w+):\s*state\.(\w+)/g)) {
      expect(m[2], `persisted "${m[1]}" reads state.${m[2]}`).toBe(m[1]);
    }
  });
});
