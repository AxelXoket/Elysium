/**
 * voiceParamDrafts.ts - unsaved model settings, outliving their component.
 *
 * ModelParams held the whole edit draft in local state and is mounted with
 * `{open && ...}`, so collapsing the row, opening a DIFFERENT model's row, or
 * closing Settings destroyed it without a word - while the file's own rule two
 * lines away says unsaved intent is withdrawn only by an explicit action
 * (audit KÖK 15).
 *
 * Module scope rather than component state, because the third of those cases
 * unmounts the whole dialog: nothing inside React survives it. Keyed by uid so
 * two models cannot bleed into each other. Cleared on save and on reset, which
 * ARE the explicit actions.
 */

import type { TtsParamValue } from "@/lib/schemas/tts";

export type ParamDraft = Record<string, TtsParamValue>;

const drafts = new Map<string, ParamDraft>();
/** What the last save for this uid actually delivered - see ModelParams. */
const delivered = new Map<string, ParamDraft>();

export function readDraft(uid: string): ParamDraft {
  return drafts.get(uid) ?? {};
}

export function writeDraft(uid: string, draft: ParamDraft): void {
  if (Object.keys(draft).length === 0) drafts.delete(uid);
  else drafts.set(uid, draft);
}

export function readDelivered(uid: string): ParamDraft {
  return delivered.get(uid) ?? {};
}

export function writeDelivered(uid: string, sent: ParamDraft): void {
  delivered.set(uid, sent);
}

/** An explicit save or reset. The one thing allowed to discard the edit. */
export function clearDraft(uid: string): void {
  drafts.delete(uid);
  delivered.delete(uid);
}

/** Test seam only - production never wants to forget everything at once. */
export function clearAllDrafts(): void {
  drafts.clear();
  delivered.clear();
}
