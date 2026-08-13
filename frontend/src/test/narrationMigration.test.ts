/**
 * narrationMigration.test.ts - moving one setting off the device and into the
 * vault, exactly once.
 *
 * The narration mode used to live in the browser-side settings blob. It is a
 * per-character preference, so it belongs in the encrypted vault with the rest
 * of them; leaving a copy behind means two sources of truth, and the one the
 * user cannot see wins on the next relaunch.
 *
 * Three things have to hold together and each is pinned below: the old value
 * is READ, it is SENT to the vault, and the old copy is GONE afterwards - not
 * merely shadowed. A migration that only writes the new home is the shape that
 * quietly resurrects the old value the first time the vault call fails.
 *
 * The seeding helpers come from ./mocks/legacyStorage on purpose. This file
 * has to arrange device-side state that no other test is allowed to touch, and
 * routing it through the sanctioned helper is what keeps static-safety S-09
 * meaningful for every other file.
 *
 * KADEME 19b added this header; the file had none.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/tts", () => ({ saveTagPrefs: vi.fn(async () => ({})) }));

import { saveTagPrefs } from "@/lib/api/tts";
import { useUiStore } from "@/lib/store/uiStore";
import {
  migrateDeviceNarration,
  readDeviceNarration,
} from "@/lib/voice/narrationMigration";

import { seedDeviceNarration as withDeviceMode, seedRawUiState }
  from "./mocks/legacyStorage";

describe("moving the narration mode into the vault", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useUiStore.setState({ narrationMigrated: false });
    vi.mocked(saveTagPrefs).mockClear();
    vi.mocked(saveTagPrefs).mockImplementation(async () => ({}) as never);
  });

  it("moves a device setting the vault never received", async () => {
    // The failure this exists for: the two copies were only written together
    // when the dropdown was touched, so a choice made before that wiring - or
    // a write that failed once - lived on this device and nowhere else.
    withDeviceMode("narrator");
    await migrateDeviceNarration("same");
    expect(saveTagPrefs).toHaveBeenCalledWith({ narrative: "narrator" });
  });

  it("leaves the vault alone when the two already agree", async () => {
    withDeviceMode("narrator");
    await migrateDeviceNarration("narrator");
    expect(saveTagPrefs).not.toHaveBeenCalled();
    expect(useUiStore.getState().narrationMigrated).toBe(true);
  });

  it("does nothing on an install that never had a device copy", async () => {
    withDeviceMode(null);
    await migrateDeviceNarration("skip");
    expect(saveTagPrefs).not.toHaveBeenCalled();
  });

  it("clears the device copy, so a later change cannot be reverted by it", async () => {
    // The user migrates as "narrator", then picks "skip" in Settings, which
    // goes to the vault. If the old key survived, the next launch would read
    // "narrator" off the device, find it different, and quietly put it back.
    // The clearing is a side effect of recording the migration: zustand
    // rewrites the blob from `partialize`, which no longer lists the field.
    withDeviceMode("narrator");
    await migrateDeviceNarration("same");
    expect(readDeviceNarration()).toBeNull();
  });

  it("does not migrate twice even if the old key comes back", async () => {
    // The flag on its own, isolated from the clearing above - a restored
    // backup, a second tab that wrote an older blob. Belt and braces, and the
    // braces are what this pins: the answer must not depend on the key being
    // gone.
    withDeviceMode("narrator");
    await migrateDeviceNarration("same");
    vi.mocked(saveTagPrefs).mockClear();

    withDeviceMode("narrator");          // it is back
    await migrateDeviceNarration("skip");
    expect(saveTagPrefs).not.toHaveBeenCalled();
  });

  it("does not record success when the vault write failed", async () => {
    // Recording it anyway would strand the setting: the device copy is what
    // Settings has been showing, and the vault would never learn it.
    withDeviceMode("narrator");
    vi.mocked(saveTagPrefs).mockRejectedValueOnce(new Error("vault locked"));
    await migrateDeviceNarration("same");
    expect(useUiStore.getState().narrationMigrated).toBe(false);

    await migrateDeviceNarration("same");
    expect(saveTagPrefs).toHaveBeenCalledTimes(2);
  });

  it("survives storage that is unreadable or holds something else", async () => {
    seedRawUiState("not json at all");
    expect(readDeviceNarration()).toBeNull();
    await expect(migrateDeviceNarration("same")).resolves.toBeUndefined();

    seedRawUiState(JSON.stringify({ state: { narrationVoice: 7 } }));
    expect(readDeviceNarration()).toBeNull();
  });
});
