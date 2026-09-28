/**
 * backfillProse - the sentences BackfillStrip puts on screen, as functions
 * of the wire bodies they describe.
 *
 * A sibling module rather than exports beside the component, because a
 * file that exports a component and a function is one fast refresh cannot
 * reload in place (react-refresh/only-export-components), and because the
 * tests that check these sentences want to check them AGAINST the numbers
 * in a body, which is what a pure function of the body makes possible.
 * Every sentence here is written from the body's own fields; none of them
 * echoes a token a person did not type.
 */
import { SWEEP_PROSE, echoable } from "@/components/notebook/WorkerPanel";
import { REASON, REASON_TAIL } from "@/components/notebook/reasonProse";
import type { BackfillScope } from "@/lib/api/notebook";
import type {
  BackfillPlan,
  BackfillStatus,
  BackfillUndo,
} from "@/lib/schemas/notebook";

/** The three scopes in the order the picker offers them.
 *
 *  A tuple rather than `Object.keys`, so the order on screen is a decision
 *  written down once: "everything" first because it is what the strip is
 *  for, the single stretch second, and the range last because it is the
 *  only one that asks for two more numbers. */
export const SCOPE_ORDER = ["all", "next", "range"] as const;

/** What each scope READS AS, on the option and on the closed trigger both.
 *
 *  One table for the two places, because Base UI resolves the trigger's
 *  text from the root's `items` and NOT from the mounted options: with no
 *  `items` it falls back to serialising the value, so the field showed
 *  `all` and then `next` - the wire token, on the face of the control, in
 *  every state including its default - while three written sentences sat
 *  one click away. Passing this record is the primitive's own mechanism
 *  for the mapping and needs nothing of the call site's own; reading the
 *  option text out of the same record is what keeps the closed field and
 *  the open list from ever saying two different things. */
export const SCOPE_LABELS: Record<BackfillScope, string> = {
  all: "Every message not yet read for notes",
  next: "Only the next part not yet read",
  range: "A range of messages, read again",
};

/** The range block's own words. The two field names are also what a screen
 *  reader calls the fields; the rest is why "Start reading" is off, said
 *  where the reader is looking. None of them mentions an id: the fields take
 *  a message's place in this chat, 1 to N, which the chat itself shows. */
export const RANGE_WORDS = {
  firstLabel: "First message",
  lastLabel: "Last message",
  /** While both ends are empty. Action first, and both ways in: the book
   *  icon on a message (the same book as the tab's Read button), or a
   *  number. */
  pick: "Pick messages with the book icon in the chat, or type numbers.",
  /** Once the first end is set and the last is not. */
  pickLast: "Pick the last message with the book icon, or type its number.",
  /** Once the last end is set and the first is not. */
  pickFirst: "Pick the first message with the book icon, or type its number.",
  /** While the chat's messages are still on their way. */
  loading: "Loading this chat's messages...",
  /** When they could not be read; a Try again sits beside it. */
  unread: "Could not load this chat's messages.",
  noMessages: "This chat has no messages to read yet.",
  /** Two typed numbers the wrong way round. What to do, then why. */
  order: "Swap the numbers so the first comes before the last.",
} as const;

/** A typed place that is not in this chat: what to type instead, with the
 *  chat's size in it. The number typed is not repeated back; the reader
 *  can see it in the box. */
export function noSuchMessage(total: number): string {
  return total === 1
    ? "Type 1. This chat has one message."
    : `Type a number from 1 to ${total}.`;
}

/** The chosen range in words, the way the chat counts: "Messages 3 to 12
 *  of 12." A range a pick had to turn round says so, because the reader
 *  clicked the two ends the other way up and should not have to work out
 *  why the numbers read backwards from what they did. */
export function rangeSummary(
  first: number, last: number, total: number, swapped: boolean,
): string {
  const what = first === last
    ? `Message ${first} of ${total} only.`
    : `Messages ${first} to ${last} of ${total}.`;
  return swapped ? `${what} First and last were swapped.` : what;
}

/** Why the preview says a run cannot start, per `blocked_by` token.
 *
 *  SIX KEYS, one per member of the backend's `_BLOCKER_REFUSALS`, and the
 *  test walks all six. Each is a whole short sentence, and the three this
 *  table shares with `SWEEP_PROSE` and `SKIP_PROSE` (nothing unread, a model
 *  that cannot write notes, a model list out of reach) are the SAME
 *  sentence, imported from reasonProse.ts. What differs is only the
 *  moment's own tail: this table is read before anything is pressed, so it
 *  never says "nothing was sent"; `blockedProse` puts "It cannot start."
 *  in front of it.
 *
 *  (The reason once recorded here for a separate wording was that
 *  `blocked_by` "is not a closed vocabulary the client can check ahead of
 *  time". That was false - `_BLOCKER_REFUSALS` is a closed tuple, six now,
 *  and section 5 enumerates the same tokens by name.) */
export const BLOCKED_PROSE: Record<string, string> = {
  no_character: "This chat has no character.",
  no_chat_model: "This chat has no model yet. Send one message first.",
  no_extract_model: "No note model is chosen. Pick one under Note suggestions.",
  model_not_extractable: REASON.modelNotExtractable,
  // Not the model's fault, so not a word about the model, and no
  // list is pointed at: the picker is fed by the same list.
  model_list_unavailable: `${REASON.modelListUnavailable} ${REASON_TAIL.tryLater}`,
  nothing_unread: REASON.nothingUnread,
};

/** The blocking reasons in words: "It cannot start." and one sentence per
 *  reason. A token nobody wrote a sentence for still gets one, and it rides
 *  along in brackets only when `echoable` allows it - the same guard
 *  `outcomeProse` puts on `last_error`, for the same reason. The vocabulary
 *  is closed today, so the fallback is for the version skew a packaged
 *  build lives with: a backend one release ahead sends a seventh token, and
 *  the screen says "it is blocked" rather than printing it. */
export function blockedProse(tokens: string[]): string {
  const parts = tokens.map((t) => BLOCKED_PROSE[t]
    ?? `It is blocked${echoable(t) ? ` (${t})` : ""}.`);
  return [REASON_TAIL.cannotStart, ...parts].join(" ");
}

/** What the strip says when the preview could not be worked out and the
 *  server named no code the catalogue has a sentence for.
 *
 *  It deliberately does NOT read "could not be loaded". This strip lives
 *  inside the Notebook panel, whose own failed-load line reads "Notes could
 *  not be loaded", and the panel can show both at once - a reader meeting
 *  two paragraphs that end in the same five words cannot tell which thing
 *  failed, and the two mean different things: one is the notes, the other
 *  is a number nobody has spent anything to learn yet. */
export const PREVIEW_FAILED = "The preview could not be worked out.";

/** A refusal from the STOP route, in words.
 *
 *  The sentences are the one table WorkerPanel owns, looked up by the same
 *  token; only the last line differs, and it has to. `sweepProse` ends with
 *  "It did not start (...)", which is both the opposite of what a press of
 *  "Stop reading" asked for and a raw backend token on a screen. A refusal
 *  this route can actually answer with says the reading is still going. */
export function stopProse(reason: string | undefined): string {
  const said = reason ? SWEEP_PROSE[reason] : undefined;
  if (said) return said;
  return "It did not stop, and did not say why. It may still be reading.";
}

/** "1 note", "2 notes", "0 notes": a count and its noun, singular at one. */
const counted = (n: number, one: string, many: string): string =>
  `${n} ${n === 1 ? one : many}`;

/** The preview in one sentence. `cost_estimate` null is said as unknown and
 *  never as a number: a call whose price nobody knows is not a free call.
 *  A known number is said as "up to": its reply half is every call's whole
 *  reply allowance at the completion price, which is the most a reply can
 *  bill, so that half is a ceiling and "about" undersold it. The prompt half
 *  is an estimate (characters over a fixed characters-per-token figure), and
 *  a job that meets failures may spend more calls than planned, so the whole
 *  figure is not a promise. */
export function planProse(plan: BackfillPlan): string {
  const what = plan.scope === "range"
    ? `${counted(plan.messages_unread, "message", "messages")} in that range`
    : counted(plan.messages_unread, "unread message", "unread messages");
  const calls = counted(plan.calls_planned, "call", "calls");
  const cost = plan.cost_estimate == null
    ? "at a price this app does not know"
    : `up to $${plan.cost_estimate.toFixed(5)}`;
  return `${what}: ${calls}, ${cost}. History reading used today: `
    + `${plan.calls_spent_today} of ${counted(plan.backfill_cap, "call", "calls")}.`;
}

/** What the cap means for THIS run, said before the button, or "" when it
 *  fits. Two cases, because they call for different things: nothing fits
 *  (come back tomorrow) and part fits (it will stop at the limit). */
export function capProse(plan: BackfillPlan): string {
  if (plan.fits_today) return "";
  const left = Math.max(0, plan.backfill_cap - plan.calls_spent_today);
  if (left === 0) {
    // WHY it is used up while the day's counter still has room, which is
    // the shape a reader meets: the Notes tab says 50 of 60 and this says
    // no. Those two are only contradictory until the reserve is named, so
    // it is named here rather than left as a wire field nobody reads.
    return [REASON.historyCapUsed, REASON_TAIL.tryTomorrow,
      ...(plan.live_reserve > 0 ? [reserveProse(plan)] : [])].join(" ");
  }
  return `Only ${left} of those ${counted(plan.calls_planned, "call", "calls")} `
    + `${left === 1 ? "fits" : "fit"} today. It stops at the limit; the rest `
    + "waits for tomorrow.";
}

/** The live reader's reserve, named: "The last 10 of 60 daily calls are
 *  kept for notes on new replies." (NOTEBOOK_LIVE_RESERVE, config.py) */
export function reserveProse(plan: BackfillPlan): string {
  const n = plan.live_reserve;
  return `The last ${n === 1 ? "one" : n} of ${plan.daily_cap} daily `
    + `${plan.daily_cap === 1 ? "call" : "calls"} ${n === 1 ? "is" : "are"} kept for notes on new replies.`;
}

/** How a finished job ended, in one sentence, from its row.
 *
 *  `last_error` is echoed only through WorkerPanel's own `echoable`, which
 *  is imported rather than restated: a second copy of the rule here drifted
 *  looser than the original (it allowed dots, hyphens and a leading digit,
 *  so a file path with a person's folder in it passed it) and the pinned
 *  other side could not see it. One rule, one place. */
export function outcomeProse(job: BackfillStatus): string {
  const did = `${counted(job.calls_spent, "call", "calls")}, `
    + `${counted(job.notes_written, "note", "notes")} written`;
  const left = job.messages_left === 1
    ? "1 message is still unread"
    : `${job.messages_left} messages are still unread`;
  // Short sentences: what stopped it, what it did, what is left. The
  // failure's class name is not in the sentence; `outcomeToken` hands it to
  // the line's title for a bug report.
  if (job.state === "done") return `Finished reading: ${did}.`;
  if (job.state === "failed") return `Stopped after a failure. ${did}. ${left}.`;
  switch (job.last_error) {
    case "daily_cap_reached":
      return `Stopped at today's limit. ${did}. ${left}; they wait for tomorrow.`;
    // The chat's own sentence, not the refusal's: a job that stopped here
    // HAD sent calls, so "Nothing was sent." would be false in front of it.
    case "chat_paused":
      return `${REASON.chatPaused} Stopped after ${did}. ${left}.`;
    case "chat_stopped":
      return `${REASON.chatStopped} Stopped after ${did}. ${left}.`;
    case "interrupted":
    case "vault_locked":
      return `Interrupted: the vault locked or the app closed. ${did}. ${left}.`;
    default:
      return `Stopped. ${did}. ${left}.`;
  }
}

/** A failed job's error class, for the outcome line's title, only when
 *  `echoable` allows it (the same guard as before, one rule, one place). */
export function outcomeToken(job: BackfillStatus): string | undefined {
  return job.state === "failed" && job.last_error && echoable(job.last_error)
    ? job.last_error : undefined;
}

/** The undo question, before anything is removed: what goes and what
 *  stays, true to `delete_job_notes` (a note the reader kept, pinned or
 *  edited is not removed). */
export const UNDO_RUN_ASK =
  "Remove this run's untouched notes? Notes you kept, pinned or edited stay.";

/** The button that answers it; its words are its accessible name. */
export const UNDO_RUN_CONFIRM = "Remove untouched notes";

/** The run's notes were all gone before the undo: nothing removed, nothing
 *  kept. */
export const UNDO_NOTHING_LEFT =
  "Nothing to remove. Those notes were already gone.";

/** One reason a note stayed, as a count, for the undo answer. `alone` is
 *  how it reads when it is the only reason ("Kept 2 pinned notes."). */
const KEPT_WHY: ReadonlyArray<{
  key: "kept_pinned" | "kept_edited" | "kept_accepted" | "kept_older";
  word: string;
  alone: (n: number) => string;
}> = [
  { key: "kept_pinned", word: "pinned",
    alone: (n) => `${n} pinned ${n === 1 ? "note" : "notes"}` },
  { key: "kept_edited", word: "edited",
    alone: (n) => `${n} edited ${n === 1 ? "note" : "notes"}` },
  { key: "kept_accepted", word: "kept",
    alone: (n) => `${n} ${n === 1 ? "note" : "notes"} you chose to keep` },
  { key: "kept_older", word: "earlier",
    alone: (n) => `${n} earlier ${n === 1 ? "note" : "notes"}` },
];

/** The undo answer, from the body's numbers: what went, then what stayed,
 *  counted by why, in the words the tab's own buttons use.
 *
 *    "Removed 9 notes. Kept 5: 1 pinned, 1 edited, 1 kept, 2 earlier."
 *    "Removed 3 notes. Kept 2 pinned notes."   (one reason only)
 *
 *  Every count comes from an exact mark on the note (the server's
 *  `delete_job_notes`): pinned = pinned now, edited = words changed, kept =
 *  Keep pressed, earlier = written before the app recorded any of that.
 *  None is claimed without its mark. Two sentences, each at most 12 words. */
export function undoProse(out: BackfillUndo): string {
  const why = KEPT_WHY.filter((w) => out[w.key] > 0);
  const kept = why.reduce((n, w) => n + out[w.key], 0);
  if (out.removed === 0 && kept === 0) return UNDO_NOTHING_LEFT;
  const removed = out.removed === 0
    ? "Removed nothing."
    : `Removed ${counted(out.removed, "note", "notes")}.`;
  if (kept === 0) return removed;
  if (why.length === 1) return `${removed} Kept ${why[0].alone(out[why[0].key])}.`;
  return `${removed} Kept ${kept}: ${why.map((w) => `${out[w.key]} ${w.word}`).join(", ")}.`;
}

/** After the generic or the catalogue's failure sentence: nothing started,
 *  and the price is not known either. */
export const PREVIEW_FAILED_TAIL = "Nothing was started, and the cost is unknown.";

/** A whole range picked in the chat while the strip is closed. */
export function pickWaitingProse(first: number, last: number): string {
  const what = first === last
    ? `Message ${first} picked.`
    : `Messages ${first} to ${last} picked.`;
  return `${what} Read the earlier messages to start.`;
}
