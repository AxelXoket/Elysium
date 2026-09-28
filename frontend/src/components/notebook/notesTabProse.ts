/**
 * notesTabProse - the words of the Notes tab's own frame: its three views,
 * the one line about this chat at the top, the waiting group, and the count
 * on the tab button.
 *
 * A module of its own, like noteProse, so the tests import the words instead
 * of retyping them and the component files export components only. Every
 * sentence is short: at most 12 words, one idea, plain words.
 */

/** The three views inside the Notes tab, and the list's name. The first is
 *  "Notebook", the name its section has always carried, and not "Notes": a
 *  view named like the panel tab it sits in was two tabs with one name to a
 *  screen reader and to voice control ("Notes 3 to review" twice). */
export const NOTES_VIEW_WORDS = {
  list: "Notes tab views",
  notes: "Notebook",
  limits: "Limits",
  settings: "Settings",
  /** Added to a view's name while something in it needs the reader. */
  needsLook: "needs a look",
} as const;

export type NotesView = "notes" | "limits" | "settings";

/** A view's accessible name: its visible word first (label in name), then
 *  the reason to open it when there is one. */
export function viewName(view: NotesView, needsLook: boolean): string {
  const word = NOTES_VIEW_WORDS[view];
  return needsLook ? `${word}, ${NOTES_VIEW_WORDS.needsLook}` : word;
}

/** The one line at the top of the Notes view: is this chat being read? */
export const CHAT_READ_WORDS = {
  reading: "Reading this chat as you talk.",
  /** Only on this chat's live unread count being zero (`chat_unread`). */
  upToDate: "Up to date.",
  checking: "Checking whether this chat is read...",
  couldNotCheck: "Could not check whether this chat is read.",
  /** A pause whose wait is over: the next reply sends one test call. */
  testHere: "Paused here. Your next reply sends one billed test call.",
  testAll: "Paused. Your next reply sends one billed test call.",
  readingNow: "Reading earlier messages now.",
  notRead: "Not read:",
  why: {
    noKey: "no API key is set.",
    proxy: "the required proxy is not working.",
    noModel: "no note model picked.",
    readerStopped: "the note reader stopped.",
    modelCannot: "this chat's model cannot write notes.",
    chatPaused: "paused here after failures.",
    chatStopped: "stopped here after failures.",
    allPaused: "paused after failures.",
    allStopped: "stopped after failures.",
    capUsed: "today's call limit is used.",
  },
  /** The button beside a "Not read" line: where the fix is. The tab's own
   *  Settings view, or the panel's Security tab for the key and the proxy. */
  openSettings: "Open Settings",
  openSecurity: "Open Security",
  /** The button beside "Reading earlier messages now.": the strip's Stop. */
  showProgress: "Show progress",
} as const;

export type NotReadWhy = keyof typeof CHAT_READ_WORDS.why;

/** The waiting suggestions, counted and grouped first. */
export const WAITING_WORDS = {
  count: (n: number) => `${n} waiting`,
  /** Where one Discard all put its suggestions: one line for the batch,
   *  with one Undo, until the panel leaves the chat. */
  discarded: (n: number) => `${n} ${n === 1 ? "suggestion" : "suggestions"} discarded.`,
  keepAll: "Keep all",
  discardAll: "Discard all",
  undoAll: "Undo discard all",
  /** Once, under the count: what every row in the group has in common. */
  hint: "Not sent until you keep them.",
} as const;

/** The count on the Notes tab button: model notes not yet seen plus
 *  suggestions waiting. Its name keeps the visible "Notes 3" whole. */
export function notesTabName(n: number): string {
  return n > 0 ? `Notes ${n} to review` : "Notes";
}

/** Where a "Not read" line's button puts the keyboard: the
 *  `data-notes-target` of the control that fixes it. */
export const NOTES_TARGET = {
  apiKey: "api-key",
  proxy: "proxy",
  noteModel: "note-model",
  workerCard: "worker-card",
  tryChat: "try-chat",
  tryAll: "try-all",
} as const;

export type NotesTarget = (typeof NOTES_TARGET)[keyof typeof NOTES_TARGET];
