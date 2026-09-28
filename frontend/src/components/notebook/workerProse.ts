/**
 * workerProse - the Background notes card's counted sentences, as functions
 * of the numbers they carry.
 *
 * A module of its own so the tests import the words instead of retyping
 * them, and the card file keeps its tables beside the component. Every
 * sentence is at most 12 words and says one thing; a count comes first.
 *
 * WHAT IS ON SCREEN AND WHAT IS ONE PRESS AWAY. The card shows the
 * state, today's spend, and one summary of anything that went wrong; the
 * census that explains each count sits behind "Show details". A line that
 * asks the reader to act (the reader has stopped, no API key, this chat's
 * refusal, a paused or stopped reader with its Try again) is never behind
 * the door.
 */

/** "1 call", "2 calls": a count and its noun, singular at one. */
export const counted = (n: number, one: string, many: string): string =>
  `${n} ${n === 1 ? one : many}`;

/** Money, the way the reading strip says it: dollars, five places. */
export const dollars = (cost: number): string => `$${cost.toFixed(5)}`;

/** Today's calls against the day's cap, and what they cost. */
export function todayProse(calls: number, cap: number, cost: number): string {
  return `Today: ${calls} of ${counted(cap, "call", "calls")}, ${dollars(cost)}.`;
}

/** Everything so far, apart from today's line so the two never read as one
 *  figure: runs done, calls made, and their cost. */
export function lifetimeProse(runs: number, calls: number, cost: number): string {
  return `All time: ${counted(runs, "run", "runs")}, `
    + `${counted(calls, "call", "calls")}, ${dollars(cost)}.`;
}

/** "We do not know" is not "it was free": calls the provider did not price
 *  are left out of the dollar figure, and the card says so beside it. */
export function unpricedProse(n: number): string {
  return `${counted(n, "call", "calls")} today had no price. `
    + "The total above leaves them out.";
}

/** The counts the summary line adds up, each from the census below it. */
export interface Trouble {
  failed: number;
  /** Runs that ended in an unexpected error: counted apart from `failed`,
   *  so the summary's failure count is the census's own denominator. */
  unexpected: number;
  skipped: number;
  dropped: number;
  empty: number;
}

/** One line for everything that went wrong, or null when nothing did.
 *  Each part is a count the details spell out, in the same words. */
export function troubleProse(t: Trouble): string | null {
  const parts = [
    t.failed > 0 ? `${counted(t.failed, "run", "runs")} failed` : null,
    t.unexpected > 0 ? `${counted(t.unexpected, "unexpected error", "unexpected errors")}` : null,
    t.skipped > 0 ? `${counted(t.skipped, "turn", "turns")} skipped` : null,
    t.dropped > 0 ? `${counted(t.dropped, "suggestion", "suggestions")} dropped` : null,
    t.empty > 0 ? `${counted(t.empty, "reply", "replies")} gave no notes` : null,
  ].filter((p): p is string => p != null);
  // One short sentence per count (a list of five in one sentence ran
  // to 17 words).
  return parts.length ? parts.map((p) => `${p}.`).join(" ") : null;
}

/** A skip count, with its noun. */
export const skippedProse = (n: number): string => `${counted(n, "turn", "turns")} skipped.`;

/** A failure whose sentence names an action, on screen: no denominator. */
export const actFailureProse = (n: number): string => `${counted(n, "run", "runs")} failed.`;

/** A census failure line's count, its own sentence so the reason after it
 *  stays within 12 words: "2 of 5 failures." */
export const failureOfProse = (n: number, total: number): string =>
  `${n} of ${counted(total, "failure", "failures")}.`;

/** The census, one function per line. */
export const CENSUS = {
  /** Failed runs that cost nothing: their messages are simply unread. */
  failedFree: (n: number) =>
    `${counted(n, "run", "runs")} failed. Nothing was lost: `
    + `${n === 1 ? "its" : "their"} messages stay unread.`,
  /** Cut off after the request went out: paid for, and not sent again. */
  abandoned: (n: number) =>
    `${counted(n, "run was", "runs were")} cut off after sending, so `
    + `${n === 1 ? "it was" : "they were"} paid for. `
    + `${n === 1 ? "Its" : "Their"} messages are not sent again, so no notes come`
    + ` from ${n === 1 ? "it" : "them"}.`,
  /** 68 a: a failed part of a very long message here is read
   *  again, so "Nothing was lost" would say too much. */
  failedPartRetried: (n: number) =>
    `${counted(n, "run", "runs")} failed. What ${n === 1 ? "it" : "they"}`
    + " could not read stays unread and is tried again.",
  /** 68 a: a part given up is never read, so not "lost"-free. */
  failedPartUnread: (n: number) =>
    `${counted(n, "run", "runs")} failed. Some of what`
    + ` ${n === 1 ? "it" : "they"} could not read stays unread.`,
  unhandled: (n: number) =>
    `${counted(n, "run", "runs")} ended in an unexpected error. Those messages stay unread.`,
  /** Paid for and empty, with the most recent reason when there is one. */
  settledEmpty: (n: number) =>
    `${counted(n, "reply was", "replies were")} paid for but gave no notes.`,
  settledEmptyWhy:
    "Another read got there first, or the messages changed meanwhile.",
  dropped: (n: number) => `${counted(n, "suggested note", "suggested notes")} dropped. Why:`,
  pausedTurns: (n: number) =>
    `${counted(n, "turn was", "turns were")} skipped while reading was paused. `
    + `${n === 1 ? "Its" : "Their"} messages stay unread.`,
  failingChatTurns: (n: number) =>
    `${counted(n, "turn was", "turns were")} skipped in chats that keep failing.`,
  failingNow: (n: number) =>
    `${counted(n, "chat is", "chats are")} failing now.`,
  otherChatsRan: "Every other chat kept running.",
  busyTurns: (n: number) =>
    `${counted(n, "turn was", "turns were")} skipped while the note reader was busy. `
    + `${n === 1 ? "Its" : "Their"} messages stay unread for a later run.`,
} as const;

/** The worker thread died. Replaces the state line rather than sitting
 *  under a "Running." that is no longer true. */
export const WORKER_DEAD =
  "The note reader has stopped. Restart Elysium to bring it back."
  + " Nothing is lost; unread messages wait.";

/** The backlog, as one line and its details. */
export function backlogProse(chats: number, messages: number): string {
  return `${counted(chats, "chat has", "chats have")} `
    + `${counted(messages, "message", "messages")} not yet read for notes. `
    + "New ones are read automatically, at a cost.";
}

/** What reads without being asked, behind "Show details". `threshold` 0
 *  is an answer older than the field: said without a number. */
export function backlogHowProse(threshold: number): string[] {
  return [
    threshold > 0
      ? `Each chat is read by itself every ${threshold} new messages.`
      : "Each chat is read by itself as new messages arrive.",
    "A short new chat is read from its first message.",
    "Older messages are read only when you ask here.",
  ];
}

/** 68 a: the line for a read of this chat that took more than one call. */
export function readCallsProse(calls: number): string {
  return `Long messages here: the last read took ${calls} calls.`;
}

/** 68 a: later parts of a very long message wait for later
 *  replies, a few per reply, so one paste cannot spend the day. */
export const PARTS_PENDING_PROSE =
  "A very long message is read in parts over your next replies.";

/** 68 a: a part given up (failed PART_RETRIES_MAX times, or no
 *  longer plannable). Final; nothing reads it again. */
export const PARTS_UNREAD_PROSE =
  "Part of a very long message could not be read.";

/** 68 a: a part whose read failed; it is tried again like any failed read. */
export const PARTS_FAILED_PROSE =
  "Part of a very long message failed; it is tried again.";
