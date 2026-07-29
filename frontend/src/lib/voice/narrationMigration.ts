import { saveTagPrefs, type NarrationMode } from "@/lib/api/tts";
import { useUiStore } from "@/lib/store/uiStore";

/**
 * One-time move of the narration mode from this device into the vault.
 *
 * WHY THIS FILE EXISTS
 *   The mode used to live in two places at once: `uiStore.narrationVoice` in
 *   localStorage, which the live stream sent on every request, and
 *   `tts_narrative` in the vault, which the Speak button read. They were
 *   written together whenever the dropdown was touched - and only then. A
 *   value chosen before that wiring existed, or a vault write that failed
 *   once, left the two disagreeing permanently: a reply performed one way as
 *   it arrived and another way when it was replayed.
 *
 *   The vault is the source now. Dropping the device copy without moving it
 *   first would silently reset the setting for exactly the people whose two
 *   copies disagreed - the same class of failure pointed the other way.
 *
 * The device copy wins when they differ. It is what the settings dropdown has
 * been SHOWING, so it is the answer the user believes they chose.
 *
 * IT HAS TO RUN ONCE AND SAY SO. Leaving the legacy key in place is not the
 * harmless option it looks like: the next mode the user picks goes to the
 * vault, and on the following launch this would read the stale device copy,
 * find it different, and put it back. The flag is set through the store
 * because zustand rewrites the persisted blob from `partialize` - which no
 * longer lists `narrationVoice` - so the same write that records the migration
 * is the write that clears it. That is also why nothing here writes to
 * device storage directly - static-safety S-09 reserves that for stores,
 * and this is one of the cases the rule was written for.
 *
 * Best-effort by construction: a failed write leaves the flag unset and the
 * next launch tries again. Deletable, with the flag, once no install can still
 * be carrying one - nothing else imports it.
 */
const UI_STATE_KEY = "elysium-ui-state";
const FIELD = "narrationVoice";
const MODES = ["same", "narrator", "skip"] as const;

function isMode(value: unknown): value is NarrationMode {
  return typeof value === "string" && (MODES as readonly string[]).includes(value);
}

/** The mode this device was carrying, or null when there is nothing to move. */
export function readDeviceNarration(): NarrationMode | null {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(UI_STATE_KEY);
  } catch {
    return null;            // private mode, disabled storage: nothing to do
  }
  if (!raw) return null;
  try {
    const state = (JSON.parse(raw) as { state?: Record<string, unknown> })?.state;
    const mode = state?.[FIELD];
    return isMode(mode) ? mode : null;
  } catch {
    return null;            // not ours, or corrupt - leave it alone
  }
}

export async function migrateDeviceNarration(
  vaultValue: NarrationMode,
): Promise<void> {
  const store = useUiStore.getState();
  if (store.narrationMigrated) return;

  const device = readDeviceNarration();
  if (device !== null && device !== vaultValue) {
    try {
      await saveTagPrefs({ narrative: device });
    } catch {
      return;               // unset flag, stale key: try again next launch
    }
  }
  store.markNarrationMigrated();
}
