/**
 * reasonProse - ONE sentence per reason, for every place that says it.
 *
 * The same refusal used to be written three times: the reading strip's
 * preview (backfillProse BLOCKED_PROSE), the answer to a press of "Start
 * reading" (WorkerPanel SWEEP_PROSE), and the worker card's census and
 * chat line (WorkerPanel SKIP_PROSE / CHAT_STATE_PROSE). Three wordings of
 * 52 to 59 words each, drifting apart ("strict form" and "strict schema",
 * "endpoints" and "keep no copy"). Each table now builds its entry from the
 * sentence here, and adds only what its own moment needs: "It cannot
 * start." before a press, "Nothing was sent." after one, a count in the
 * census. The tests hold every place to containing the same constant.
 *
 * Every sentence is true in all three moments: present tense for what is
 * still so, past tense only for what happened before the reader looked.
 * At most 12 words a sentence, action last so it is the thing remembered.
 */

import { CHAT_MODEL_OPTION_LABEL } from "@/lib/notebook/labels";

export const REASON = {
  /** The chat's note model - picked, or bound to the chat model - is not
   *  one notes can be written with: it cannot answer in the strict note
   *  format, or it is not on the list of endpoints that keep no copy
   *  (notebook_worker._unfit_reason refuses a picked model this way too).
   *  The usual cause, the chat-model option, is named. */
  modelNotExtractable:
    "Pick a note model under Note suggestions. This chat's note model"
    + " cannot write notes.",
  /** Not the model's fault, so not a word about the model: the provider's
   *  list could not be fetched, and a chat model used as the note model is
   *  vetted by that list and nothing else. No read is promised and no list
   *  is pointed at: the picker is fed by the same list. */
  /** Past tense on purpose: the census keeps these rows after the list
   *  comes back, so the sentence stays true there as well as before and
   *  after a press. */
  modelListUnavailable:
    "The provider's list of note models could not be fetched. Without it,"
    + " a chat model used for notes could not be checked.",
  nothingUnread: "Every message here has already been read.",
  /** The history cap, not the day's whole cap: the live reader keeps a
   *  reserve of its own (see capProse). */
  historyCapUsed: "Today's calls for reading history are used up.",
  /** One chat's own breaker. Every sentence about one chat says the rest
   *  are fine, or a reader goes looking for a fault that is not there. */
  chatPaused: "Note reading is paused in this chat after repeated failures."
    + " Other chats are unaffected.",
  chatStopped: "Note reading stopped in this chat after too many failures."
    + " Other chats keep running.",
} as const;

/** The rest of a reason, behind a "Why" door beside its line (the line
 *  is action first and at most two sentences). */
export const REASON_WHY = {
  modelNotExtractable: [
    "Note models keep no copy of your words.",
    "They also follow a strict format.",
    `Usually "${CHAT_MODEL_OPTION_LABEL}" is picked.`,
    "A chat model that can write notes also works.",
  ],
} as const;

/** The tails, so a tail is also written once. */
export const REASON_TAIL = {
  /** Before a press: the strip's preview. */
  cannotStart: "It cannot start.",
  /** After a refused press. */
  notSent: "Nothing was sent.",
  /** In the census: nothing left the machine and nothing was billed. */
  notSpent: "Nothing was sent or spent.",
  tryLater: "Try again later.",
  tryTomorrow: "Try again tomorrow.",
  /** The census's own future for a list that was out of reach: the model is
   *  checked again, which promises a check and not a read. */
  checkedAgain: "The model is checked again once the list is back.",
} as const;
