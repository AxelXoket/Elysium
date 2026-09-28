/**
 * limitsProse - the words the Limits section puts on screen.
 *
 * A module of its own for the reason noteProse is one: the tests import
 * these instead of retyping them, and BoundaryPanel.tsx exports components
 * only. Every sentence is at most 12 words, action or fact first.
 */

/** The disclosure buttons' words (their accessible names too). */
export const DISCLOSURE_WORDS = {
  how: "How it works",
  details: "Show details",
  /** The backlog's door: a different name from the card's, so a reader's
   *  list of buttons tells the two apart (WCAG 2.4.6). */
  whenRead: "When messages are read",
  /** The door beside a model that cannot write notes. */
  whyModel: "Why this model cannot",
} as const;

/** The safeword's name, over its box. */
export const SAFEWORD_LABEL = "Safeword";

/** The one line that stays on screen: what the word does. */
export const SAFEWORD_LINE = "Put it in a message and nothing is sent or saved.";

/** On screen beside it, and only beside a box there is: with the saved
 *  word unread, no box is drawn and there is nothing to leave empty. It is
 *  the only way to switch the word off, so it is not behind the door. */
export const SAFEWORD_OFF = "Leave it empty to turn it off.";

/** Behind "How it works". Every promise the old three paragraphs made, in
 *  the same order, one idea per sentence:
 *    - what "nothing" covers (none of it is SENT: completions.py raises
 *      before anything is assembled), and that the check is not the model's;
 *    - a copy of the word in something OLDER does not stop the turn: it is
 *      cut from what the model is sent, and a line the cut leaves empty is
 *      not sent at all;
 *    - regenerate is the exception, because it sends that message again;
 *    - the reader's own copy is never rewritten. */
export const SAFEWORD_HOW = {
  covers: "Your message, notes and limits are not sent to the model.",
  where: "Elysium looks for the word itself; the model is not asked.",
  older: "In an earlier message, a note or a character, it stops nothing.",
  cut: "There it is cut from what the model is sent.",
  emptyLine: "A line left empty by the cut is not sent.",
  regenerate: "Regenerate resends your message, so a word in it stops the reply.",
  ownCopy: "Your own copy never changes.",
  ownCopyWhere: "The word stays in the chat, the note and on screen.",
} as const;

/** The limits' one promise: never trimmed, all or nothing. */
export const LIMITS_LINE = "Your limits go with every message, in full. If they do not fit, nothing is sent.";

/** Placeholders (and titles), short enough to show whole at the 300 px
 *  floor. */
export const NEW_LIMIT_PROMPT = "Something to keep out...";
export const SAFEWORD_PROMPT = "A word you'd never type by accident...";
