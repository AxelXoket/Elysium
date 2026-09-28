/**
 * noteProse - every word the Notes tab's note rows and its "could not
 * load" lines put on screen.
 *
 * A module of its own, like backfillProse, so the tests import the words
 * instead of retyping them and the component files export components only
 * (a file that exports a component and a constant is one fast refresh
 * cannot reload). Each one is short on purpose: the action first, one
 * idea, plain words.
 */

/** Button names on a note row. Each is the button's accessible name AND
 *  its tooltip, the same words (label in name). Verb, then thing, the way
 *  the panel's older names already read ("Delete note", "Pin note"). */
export const NOTE_ROW_WORDS = {
  edit: "Edit note",
  save: "Save note",
  cancel: "Cancel edit",
  field: "Edit note text",
  keep: "Keep suggestion",
  discard: "Discard suggestion",
  pin: "Pin note",
  unpin: "Unpin note",
  delete: "Delete note",
  /** The delete question, as the two words on its buttons. */
  confirmDelete: "Delete",
  cancelDelete: "Cancel",
  /** Where a discarded suggestion was, until the panel leaves the chat. */
  discarded: "Suggestion discarded.",
  undoDiscard: "Undo discard",
} as const;

/** Under the edit box while it is empty: why Save is off, and the way out. */
export const EDIT_EMPTY = "Write something, or press Escape to keep the old note.";

/** Under the edit box when the save did not go through, for a reason the
 *  server did not name. */
export const EDIT_FAILED = "Could not save. Your words are still here.";

/** The same line, when the server said why. Each at most 12 words, what
 *  happened and then what to do; the words typed stay in the box. */
export const EDIT_FAILED_WHY: Readonly<Record<string, string>> = {
  notebook_entry_not_found: "This note was removed. Copy your text to keep it.",
  notebook_entry_too_long: "This is too long to save. Shorten it, then save.",
  notebook_entry_empty: "Write something, or press Escape to keep the old note.",
  // The edit box does not survive the lock screen, so this says so.
  vault_locked: "The vault locked. Copy your words before you unlock.",
};

/** The sentence for a failed save, from the server's reason code. */
export function saveFailedProse(code: string | null): string {
  return (code != null && EDIT_FAILED_WHY[code]) || EDIT_FAILED;
}

/** Who wrote a model note. `provenance` stays `model` for good, so after
 *  the reader changes the words the line says both halves rather than
 *  handing the note to either one. `edited` shows exactly when the store
 *  has written `edited_at`. */
export const MODEL_NOTE_WORDS = {
  wrote: "Written by the model.",
  edited: "Model wrote it, you edited it.",
} as const;

/** In place of a setting whose saved value could not be read. The control
 *  beside it stays off and shows no value, so nothing on screen is a guess. */
export const SETTING_UNREAD = "Could not load this setting.";

/** The button beside that line. */
export const SETTING_RETRY = "Try again";

/** In place of a setting while its saved value is on its way. */
export const SETTING_LOADING = "Loading this setting...";

/** The three older "could not load" lines of the tab, each beside a Try
 *  again now instead of "Try again in a moment." */
export const NOTES_UNREAD = "Could not load the notes. They are still saved.";
export const LIMITS_UNREAD = "Could not load the limits. They are still saved.";
export const CHOICE_UNREAD =
  "Could not read your saved choice. Nothing was changed.";
