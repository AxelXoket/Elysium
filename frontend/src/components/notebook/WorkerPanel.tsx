/**
 * WorkerPanel - what the background extractor has been doing with your money.
 *
 * The whole feature spends the user's own OpenRouter credits, unattended,
 * while they are reading something else. There are exactly two ways that goes
 * wrong: a loop that will not stop, and a refusal nobody can see. This screen
 * is the answer to the second one.
 *
 * Without it, "the notebook has proposed nothing this week" and "the notebook
 * refused sixty times for a reason nobody can see" are the same screen - and
 * the second is the one that needs a person.
 *
 * Surface: `.glass-right`, the app's one LIGHT island. Heading and controls
 * follow the vocabulary its siblings use, not the dark settings dialog's.
 */
import { RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useUiStore } from "@/lib/store/uiStore";
import { SwitchRow } from "@/components/settings/SwitchRow";
import { settledAt } from "@/components/notebook/useRetryPress";
import { SettingUnread } from "@/components/notebook/SettingUnread";
import { NOTES_TARGET } from "@/components/notebook/notesTabProse";
import { PendingButton } from "@/components/notebook/PendingButton";
import { AUTO_ACCEPT_OFF, SETTING_LOADING } from "@/components/notebook/noteProse";
import { useHeldValue } from "@/components/settings/useHeldValue";
import { SlideIn } from "@/components/motion/SlideIn";
import { Collapse } from "@/components/motion/Collapse";
import { FadeIn } from "@/components/motion/FadeIn";
import { useErrorStore } from "@/lib/errors/errorStore";
// The sentences for this app's own reason codes, borrowed rather than
// rewritten: a failed call's error_type carries one of those codes after a
// colon, and the file below is where every code the backend can put in front
// of a person keeps its English.
import { getErrorMessage, isKnownErrorCode } from "@/lib/errors/errorMessages";
import { REASON, REASON_TAIL, REASON_WHY } from "@/components/notebook/reasonProse";
import { Disclosure } from "@/components/notebook/Disclosure";
import { DISCLOSURE_WORDS } from "@/components/notebook/limitsProse";
import {
  CENSUS,
  WORKER_DEAD,
  actFailureProse,
  failureOfProse,
  skippedProse,
  lifetimeProse,
  todayProse,
  readCallsProse,
  PARTS_PENDING_PROSE,
  PARTS_FAILED_PROSE,
  PARTS_UNREAD_PROSE,
  troubleProse,
  unpricedProse,
} from "@/components/notebook/workerProse";
import {
  useWorkerStatus,
  useNotesAfterReply,
  useResetWorker,
  useAutoAccept,
  useSetAutoAccept,
  useSetChatAutoAccept,
} from "@/lib/query/notebook";

/** The auto-accept switch's id, so "Try again" can hand it focus once its
 *  value has been read. */
const AUTO_ACCEPT_SWITCH = "auto-accept-switch";

/** The card's button words; a sentence that names a button imports them. */
// eslint-disable-next-line react-refresh/only-export-components -- words beside the component, same boundary as SKIP_PROSE
export const WORKER_WORDS = {
  tryAll: "Try again now",
  tryChat: "Try this chat again",
} as const;

/** Why a run was refused, in words. These are machine tokens on the wire and
 *  must not reach a reader as such.
 *
 *  Exported so the tests can walk this table by its own keys rather than
 *  retyping the vocabulary beside it. A second hand-kept list of reasons is
 *  exactly how `plan_invalidated` reached a reader as a token in the first
 *  place.
 *
 *  The two rollback reasons below were described here, and in the contract
 *  document, as "you edited or deleted" versus "the chat was cleared". That
 *  is not the split the backend makes. `commit_extraction` asks ONE question
 *  when a reply lands and finds its running row gone: does the LAST message
 *  of the range it was reading still exist? Probed against the real routes,
 *  every action lands like this:
 *
 *    edit, range ended AT the edited message      -> plan_invalidated
 *    edit, range ended AFTER it (the swept tail)  -> range_cleared
 *    delete a message (takes everything after)    -> range_cleared
 *    clear the chat                               -> range_cleared
 *    an aborted send's cleanup                    -> range_cleared
 *    delete the chat                              -> neither: the foreign key
 *                                                    fires and it is counted
 *                                                    as an unexpected error
 *
 *  So "or deleted" named the wrong reason for every delete there is, and
 *  "the chat was cleared" named one of the four things that reach
 *  `range_cleared`. Both sentences are written from the real discriminator
 *  now: whether the messages are still there.
 *
 *  CORRECTED 2026-08-20. This table listed "regenerate a reply ->
 *  range_cleared" and it was wrong: regenerating does not delete anything.
 *  `_append_variant` deactivates the old variant in place and inserts the new
 *  one - its own docstring says "Nothing is deleted" - and the three callers
 *  of `forget_proposals_from_messages` are a message delete, an edit, and an
 *  aborted send's cleanup. None of them is the regenerate path. The fourth
 *  real route, the aborted send, was missing from this list, so the count of
 *  four happened to come out right while one entry was invented and one was
 *  absent.
 *
 *  What follows from that is NOT cosmetic, and it is still open rather than
 *  something this comment settles: because nothing rolls
 *  the notebook back on a regenerate, an extraction in flight over a reply
 *  you then threw away still lands, still gets written, and - with automatic
 *  acceptance on by default - still goes into the prompt. The panel used to
 *  tell you that case was thrown away. It is not. */
// This table must stay EXPORTED: the test that proves every skip reason the
// backend can write reaches the screen as words imports it, mounts the panel
// once per reason and reads the rendered line. It no longer reads this file's
// text, so the table may move to a sibling module, but it may not stop being
// exported without blinding that check.
// eslint-disable-next-line react-refresh/only-export-components -- co-located with the component on purpose (see above); fast-refresh boundary accepted, in the idiom button.tsx and badge.tsx already use
export const SKIP_PROSE: Record<string, string> = {
  notebook_daily_cap_reached: "Today's call limit was already used.",
  // Named rather than counted as a failure, because nothing was sent. The
  // old order claimed the day's quota first and only then discovered there
  // was no key, so twenty of sixty calls burned without a byte leaving the
  // machine and the panel said "stopped" with nothing attached.
  api_key_not_set:
    "No API key was set, so nothing was sent or spent. Add one in"
    + " Settings and reading picks up again.",
  proxy_gate: "Your proxy was required and was not healthy. Check it in Settings.",
  // 68 a: one message longer than a whole call is read in parts; a later
  // part still to be read is this row until it is. Chat-scoped: the card
  // says it from `long_message`, the chat on screen, never from the
  // install-wide count, which would read "not read" during a healthy read.
  message_part_pending: "Part of a very long message is still waiting to be read.",
  // 68 a: a later part given up. Chat-scoped like the one above; the
  // card says it from `long_message.unread`.
  message_part_unread: "Part of a very long message could not be read.",
  // NOT a line in the install-wide list below (3.8). The count of these rows
  // is every chat's, and the sentence is about one chat's model, so it is
  // said only through `chat_refusal`: the backend sends it while a refusal
  // of the selected chat, newer than that chat's last read, names the model
  // the chat uses now. Before this, the refusal was silent
  // everywhere but the reading strip's preview, and the notes just stopped.
  // The reason is REASON's, shared with the strip and the run.
  model_not_extractable:
    `${REASON.modelNotExtractable} ${REASON_TAIL.notSpent}`,
  // The gate had no evidence either way: the provider's list of models that
  // can write notes could not be fetched, so a chat whose note model is its
  // own chat model was not read at that time. It says nothing about the
  // model, so this sentence does not either, and it is an install-wide line
  // like the rest: counted, over every chat. It promises no read: once the
  // list is fetched the model is checked again, and a model the list does
  // not carry is then refused as `model_not_extractable`.
  model_list_unavailable:
    `${REASON_TAIL.notSpent} ${REASON.modelListUnavailable} Chats using their`
    + ` own model for notes were not read then. ${REASON_TAIL.checkedAgain}`,
  // Written by a different path from the others - commit_extraction's
  // require_trace branch, not the worker's own SKIP_REASONS - so the gate
  // that keeps this table complete never covered it, and the panel printed
  // the raw token at the reader. Which is the one thing this table exists
  // to stop.
  plan_invalidated:
    "You edited a message while notes were being read from it. The reply"
    + " was paid for and thrown away. Those messages are read again later.",
  // Not "the chat was cleared". Anything that removes the last message of
  // the range while the reply is out lands here: a deleted message, a
  // cleared chat, an aborted send being tidied up, or an edit whose swept
  // tail happened to be where the range ended. NOT a regenerated reply -
  // that deletes nothing, so nothing is skipped and the note is written from
  // the reply you discarded. See the correction note at the top of this file.
  range_cleared:
    "A message was deleted, or the chat cleared, during a read. The reply"
    + " was paid for and thrown away. Messages still there are read again."
    + " Deleted ones are not.",
};

/** The skip reasons the card never counts in its install-wide list, because
 *  their sentence is about one chat. Each reaches the screen only through
 *  the status body's `chat_refusal`, which the backend scopes to the chat on
 *  screen. Exported so the test that walks every reason can take the right
 *  road for these. */
// eslint-disable-next-line react-refresh/only-export-components -- same boundary as SKIP_PROSE
export const CHAT_SCOPED_SKIPS: ReadonlySet<string> = new Set([
  "model_not_extractable",
  "message_part_pending",
  "message_part_unread",
]);

/** After the chat's refusal, behind its door: what happens once fixed. */
export const CHAT_REFUSAL_AFTER = "After you pick one, the next reply is read.";

/** Skip reasons whose line asks the reader to act: never behind the
 *  details door. */
const ASKS_TO_ACT: ReadonlySet<string> = new Set(["api_key_not_set", "proxy_gate"]);

/** What a reason with no sentence reads as. A snake_case token is the one
 *  thing this whole table exists to keep off the screen, and the fallback
 *  was printing exactly that for anything the backend adds ahead of this
 *  file. The code rides along in brackets so a bug report can still carry
 *  it; the sentence is what a reader gets. */
// eslint-disable-next-line react-refresh/only-export-components -- reads the table above; same reason, same boundary
export function skipProse(reason: string): string {
  return (
    SKIP_PROSE[reason]
    ?? `Refused for a reason this version has no words for (${reason}).`
  );
}

/** What may be echoed back to the reader in brackets.
 *
 *  The three fallbacks below print the raw token for a reason no table has
 *  words for, and the value they print now comes from a DATABASE COLUMN
 *  rather than from a fixed vocabulary. `error_type` has no gate of any
 *  kind: `skip_reason` is checked against a frozen set before it is stored
 *  and this one is not, and one of its five writers stores the text of an
 *  exception rather than a class name. Every one of those texts is a bare
 *  token today, so nothing leaks today, and that is exactly the shape of
 *  promise that stops being true one raise site later.
 *
 *  So the echo is refused unless the value looks like what the writers are
 *  supposed to produce: a leading letter or underscore, then letters,
 *  digits, underscores and the one colon `_failure_type` puts between a
 *  class and its reason code. Anything carrying a space, a path, a quote or
 *  a sentence is dropped rather than rendered, and the count still reaches
 *  the reader with its sentence. A screen this feature can reach is not a
 *  place to find out that a message was interpolated somewhere upstream. */
// eslint-disable-next-line react-refresh/only-export-components -- read by the three fallbacks below; same boundary
export function echoable(token: string): boolean {
  return /^[A-Za-z_][A-Za-z0-9_]*(:[A-Za-z_][A-Za-z0-9_]*)?$/.test(token);
}

/** The token in brackets, or nothing at all when it may not be echoed. */
function bracket(token: string): string {
  return echoable(token) ? ` (${token})` : "";
}

/** Why a run FAILED, in words - the other half of the question SKIP_PROSE
 *  answers. A refusal is a limit doing its job; a failure is something that
 *  went wrong, and it reached this screen as a bare number with no reason
 *  attached while the reason sat written on every one of those rows.
 *
 *  Short on purpose, and it is not the shape of the table above. The skip
 *  vocabulary is a closed list the backend declares; this one is not - what
 *  is written here is an EXCEPTION CLASS NAME, so a name this file has never
 *  heard of is the ordinary case rather than the exception. The decoding
 *  lives in `failureProse` below and this table holds only the fixed tokens
 *  that are not class names. */
// eslint-disable-next-line react-refresh/only-export-components -- same boundary as SKIP_PROSE above, and the tests walk this table by its own keys
export const FAILURE_PROSE: Record<string, string> = {
  // The one fixed literal the backend writes into this column. It also has a
  // paragraph of its own further down and this is not a duplicate of it:
  // that paragraph says what it COST and what happens to those messages,
  // this line names the cause in a census that would otherwise print the
  // token itself beside every other reason.
  abandoned_in_flight:
    "Elysium was shut down, or the vault locked, mid-request.",

  // The other fixed family, and the one a reader most often needs: when the
  // reply itself cannot be trusted the worker writes the bare reason with no
  // class name in front of it. Eight tokens, declared by the parser, and
  // every one of them is a direct answer to "why did it produce nothing".
  no_choices: "The provider answered without a reply in it.",
  truncated: "The reply was cut off, so none of it could be trusted.",
  refused: "The model declined to answer for those messages.",
  empty_content: "The reply came back empty.",
  unparseable: "The reply was not in the form notes need.",
  not_an_object: "The reply came back in a shape this feature cannot read.",
  // Named apart from the two around it because it is the one that could be
  // mistaken for a quiet stretch. An empty list is how a model says it found
  // nothing worth writing down, and it is not an error; this is the answer
  // that left the list out altogether.
  no_facts_key:
    "The reply had no list of notes at all. An empty list would have"
    + " been fine.",
  facts_not_a_list: "The reply had no list where the notes go.",
};

/** The sentence for one `error_type`, in whatever shape it arrives.
 *
 *  Five shapes reach this column and they are five different events:
 *
 *    a bare token           a reply that arrived and could not be trusted;
 *                           the eight the parser declares, in the table above
 *    prompt_Something       the request could not even be built, so nothing
 *                           was sent and nothing was spent
 *    write_Something        the reply came back and was billed, and the
 *                           notes it carried could not be written down
 *    Something:reason_code  the call failed and the cause is one of this
 *                           app's own reason codes
 *    Something              an exception class and nothing else
 *
 *  The reason code half is NOT given a sentence here. Those codes already
 *  have their English in lib/errors/errorMessages.ts, which is where every
 *  code this backend can put in front of a person keeps it; a second table
 *  beside it is exactly how two screens come to disagree about what one code
 *  means, and `plan_invalidated` reaching a reader as a raw token is what a
 *  second hand-kept list already cost once in this file.
 *
 *  Returns a finished sentence rather than the clause `skipProse` returns,
 *  because one of its answers is a borrowed one and those are written as
 *  whole sentences. The caller therefore does not add a full stop.
 *
 *  The raw token rides along in brackets for anything not named, the same
 *  way the skip fallback does it: a bug report needs it, a reader does not. */
// eslint-disable-next-line react-refresh/only-export-components -- reads the table above; same reason, same boundary
export function failureProse(errorType: string): string {
  const known = FAILURE_PROSE[errorType];
  if (known) return known;

  // The write path stamps this prefix and the store matches on it, so it is
  // the one piece of the vocabulary that is structure rather than a name.
  if (errorType.startsWith("write_")) {
    return `The reply was paid for, but its notes could not be saved${bracket(errorType)}.`;
  }

  // The mirror image, and worth its own sentence for exactly that reason:
  // this one happens BEFORE the request, so no call was made and nothing was
  // spent. Rolled into the generic answer it would read like a failure the
  // reader had paid for.
  if (errorType.startsWith("prompt_")) {
    return `The request could not be built. Nothing was sent or spent${bracket(errorType)}.`;
  }

  const reason = errorType.slice(errorType.indexOf(":") + 1);
  if (errorType.includes(":") && isKnownErrorCode(reason)) {
    return getErrorMessage(reason);
  }

  return `Failed for a reason this version has no words for${bracket(errorType)}.`;
}

/** Whether a failure's sentence asks the reader to act. A failure whose
 *  cause is one of this app's own reason codes is said in the catalogue's
 *  sentence, and those name the action (top up the account, check the API
 *  key, check the proxy). Such a line stays on screen, outside the details
 *  door; the rest are census. */
// eslint-disable-next-line react-refresh/only-export-components -- reads the catalogue like failureProse; same boundary
export function failureAsksToAct(errorType: string): boolean {
  if (FAILURE_PROSE[errorType] || !errorType.includes(":")) return false;
  return isKnownErrorCode(errorType.slice(errorType.indexOf(":") + 1));
}

/** Why a suggestion the model made never became a note.
 *
 *  These are the parser's own refusals, counted in memory for the life of
 *  the process. Every one of them is a note the reader paid for and did not
 *  get, and the whole set was declared on the wire and rendered nowhere -
 *  so a gate throwing away good notes and a model that had nothing to say
 *  were the same empty list.
 *
 *  A closed list, unlike the failures above: the parser declares these. */
// eslint-disable-next-line react-refresh/only-export-components -- same boundary as SKIP_PROSE above, and the tests walk this table by its own keys
export const DROP_PROSE: Record<string, string> = {
  // Said after "3 suggested notes dropped. Why:", so each is the reason
  // alone, with no subject to match against a plural count.
  over_cap: "a run keeps only so many notes.",
  off_schema: "not in note form.",
  too_long: "too long.",
  empty_text: "no text.",
  ungrounded: "its quote was not in the messages read.",
  // The honest half of a gate that is not free. Measured over a thirty case
  // set it catches nine invented notes in ten and drops four true notes in
  // twenty, and that second number only ever reaches a person here - so it
  // is said, rather than left to look like the model having nothing to say.
  unanchored:
    "no name or number from its quote was in the note. This check also"
    + " drops about 1 true note in 5. One you expected may be missing.",
  not_worth_a_slot: "not worth a place in the notes sent with every message.",
};

/** As `skipProse`, for the parser's refusals. Same fallback, same reason:
 *  the backend can grow a reason ahead of this file, and the token itself is
 *  the one thing that must not be what a reader gets. */
// eslint-disable-next-line react-refresh/only-export-components -- reads the table above; same reason, same boundary
export function dropProse(reason: string): string {
  return (
    DROP_PROSE[reason]
    ?? `a reason this version has no words for${bracket(reason)}.`
  );
}

/** Why a reply that was paid for landed nothing.
 *
 *  The fourth vocabulary on this card, and the only one that arrives as a
 *  SINGLE most-recent value rather than as a count per reason: the worker
 *  keeps one `last_error`, and `_empty_reason` is the only thing that ever
 *  writes a `settled_` token into it. So the prefix is what says this line
 *  is the one the value belongs to, and the panel says "the most recent"
 *  rather than describing all of them.
 *
 *  NOT promoted to a catalogue code, and that is a decision rather than an
 *  omission. `shared/error_catalogue.json` and lib/errors/errorMessages.ts
 *  hold the codes the BACKEND puts in front of a person through an error
 *  channel - an HTTP detail, a stream notice - and every record there names
 *  the raise site that can send it. None of these is ever raised: they are
 *  a status-screen vocabulary, exactly like the skip reasons above, and
 *  registering them would mean writing a producer entry for a code no
 *  producer can emit. Same shape as its neighbours here, same fallback. */
// eslint-disable-next-line react-refresh/only-export-components -- same boundary as SKIP_PROSE above, and the tests walk this table by its own keys
export const EMPTY_PROSE: Record<string, string> = {
  settled_empty: "every note in the reply was already here",
  settled_duplicate: "those messages were already read",
  settled_stale_attempt: "a second try at those messages took over",
  settled_chat_gone: "the chat was deleted while the reply was out",
  settled_nothing: "saving the notes gave no answer",
};

/** As `skipProse`, for the empty settles. */
// eslint-disable-next-line react-refresh/only-export-components -- reads the table above; same reason, same boundary
export function emptyProse(reason: string): string {
  return (
    EMPTY_PROSE[reason]
    ?? `a reason this version has no words for${bracket(reason)}`
  );
}

/** Whether `last_error` is describing an empty settle rather than a failure.
 *
 *  One field carries the most recent event of ANY kind - a failed write, a
 *  provider class, an empty settle - so a line that claimed it without
 *  asking would attribute one signal's value to the other. The prefix is the
 *  discriminator because `_empty_reason` is its only writer. */
// eslint-disable-next-line react-refresh/only-export-components -- decides which line owns the table above; same boundary
export function isEmptySettle(lastError: string | null | undefined): boolean {
  return typeof lastError === "string" && lastError.startsWith("settled_");
}

/** The three states, said plainly. "closed" is engineering vocabulary for
 *  "working", and a panel that prints it is asking the reader to learn the
 *  implementation before they can read their own status.
 *
 *  EXPORTED for the reason SKIP_PROSE is: the fallback below prints the raw
 *  token when the backend grows a state this table has never heard of, and
 *  the only thing that catches that is a test walking every state the
 *  backend can report through this map. */
// eslint-disable-next-line react-refresh/only-export-components -- co-located with the component, same boundary as SKIP_PROSE above
export const STATE_PROSE: Record<string, string> = {
  closed: "Running.",
  open: "Paused after repeated failures. It tries again by itself later.",
  // The state that was missing, and the omission was on the screen rather
  // than in the breaker. Once the cooldown elapses exactly one call is let
  // through as a trial - a real, billed request - while `opened_at` stays
  // set, so the panel went on saying "Paused" over a call that was going
  // out. The distinction matters because one of these two costs money.
  half_open: "Paused, but the wait is over. The next reply sends one test"
    + " call, billed as usual.",
  stopped: "Stopped after too many failures."
    + ` It waits until you press "${WORKER_WORDS.tryAll}".`,
};

/** The same states for ONE chat, and every sentence has to say that the rest
 *  are fine. A reader who sees "Paused after repeated failures" with no such
 *  clause reads it as the feature being down and goes looking for a fault
 *  that is not there - which is the whole reason the breaker was split. */
// eslint-disable-next-line react-refresh/only-export-components -- same boundary as STATE_PROSE
export const CHAT_STATE_PROSE: Record<string, string> = {
  open: `${REASON.chatPaused} It tries again here by itself later.`,
  half_open: "Note reading is paused here, but the wait is over. Your next"
    + " reply here sends one test call, billed as usual. Other chats are"
    + " unaffected.",
  stopped: `${REASON.chatStopped} It waits until you press "${WORKER_WORDS.tryChat}".`,
};

/** Why a reading job refused to start (or to stop), said plainly.
 *
 *  This was three arms of an inline ternary whose last one was a catch-all
 *  reading "It is already reading. Give it a moment." The moment the worker
 *  learned to refuse a paused chat, that arm told the reader the exact
 *  opposite of what was happening. A table with a named fallback cannot do
 *  that quietly, and a test can walk it against the backend's own set.
 *
 *  TEN keys, one per token in the backend's SWEEP_REFUSALS: the five the
 *  sweep always had, the four a backfill job adds (`not_ready`,
 *  `daily_cap_reached`, `model_not_extractable`, `model_list_unavailable`)
 *  and the stop route's `nothing_running`. The strip that reads this table lives in
 *  NotebookPanel (BackfillStrip.tsx); the table stays here because this is
 *  the file every reason-to-sentence table lives in, and the test that
 *  walks it imports it from here. */
// eslint-disable-next-line react-refresh/only-export-components -- same boundary as STATE_PROSE
export const SWEEP_PROSE: Record<string, string> = {
  nothing_unread: REASON.nothingUnread,
  already_running: "It is already reading. Give it a moment.",
  already_in_flight: "It is already reading this chat. Give it a moment.",
  chat_paused: `${REASON_TAIL.notSent} ${REASON.chatPaused}`,
  chat_stopped: `${REASON_TAIL.notSent} ${REASON.chatStopped} Use "${WORKER_WORDS.tryChat}", below.`,
  not_ready: "It cannot start yet. It needs a character, a chat model and a"
    + " note model first.",
  daily_cap_reached: `${REASON.historyCapUsed} Nothing was sent or lost.`
    + ` ${REASON_TAIL.tryTomorrow}`,
  model_not_extractable: `${REASON.modelNotExtractable} ${REASON_TAIL.notSent}`,
  model_list_unavailable:
    `${REASON_TAIL.notSent} ${REASON.modelListUnavailable} ${REASON_TAIL.tryLater}`,
  nothing_running: "Nothing is reading right now, so there is nothing to"
    + " stop.",
};

/** What a refusal with no sentence reads as. Same shape and same reason as
 *  `skipProse`: the code rides along in brackets for a bug report, the
 *  sentence is what a reader gets, and neither is a bare token. */
// eslint-disable-next-line react-refresh/only-export-components -- same boundary as STATE_PROSE
export function sweepProse(reason: string | undefined): string {
  if (!reason) return "It did not start, and did not say why.";
  return SWEEP_PROSE[reason] ?? `It did not start (${reason}).`;
}

export function WorkerPanel() {
  const chatId = useUiStore((s) => s.selectedChatId);
  // The chat is part of the QUESTION, not just of the answer: the worker's
  // per-chat breaker is only reported for the chat that was asked about.
  const status = useWorkerStatus(chatId);
  // After a reply, ask fast until this chat's read ends (58 a). Here because
  // this panel is mounted only while the Notes tab is open; the right panel
  // itself collapses to zero width and stays mounted, so a collapsed panel
  // is asked about as no chat at all.
  const panelCollapsed = useUiStore((s) => s.rightPanelCollapsed);
  useNotesAfterReply(panelCollapsed ? null : chatId);
  const reset = useResetWorker();
  const auto = useAutoAccept(chatId);
  const setAuto = useSetAutoAccept();
  const setChatAuto = useSetChatAutoAccept();
  const pushError = useErrorStore((s) => s.pushError);

  const body = status.data;
  const state = body?.worker.state ?? "closed";
  // Undefined when nothing is selected OR when this chat has never failed -
  // a healthy chat holds no breaker at all, and "no record" is exactly
  // "nothing to report".
  const chatState = body?.worker.chat?.state;
  // Every reason except the chat-scoped ones: a count over every chat must
  // not read as a sentence about the chat on screen.
  const allSkips = Object.entries(body?.stats.skip_reasons ?? {})
    .filter(([why]) => !CHAT_SCOPED_SKIPS.has(why));
  // No API key asks the reader to act, so its line stays on screen; every
  // other reason is census, behind "Show details".
  const keySkips = Object.fromEntries(
    allSkips.filter(([why]) => ASKS_TO_ACT.has(why)));
  const skips = Object.fromEntries(
    allSkips.filter(([why]) => !ASKS_TO_ACT.has(why)));
  const chatRefusal = body?.chat_refusal ?? null;
  // The line closes through its Collapse, so it keeps the last reason it
  // said while the height animates down (KURAL 5.5); the Collapse takes it
  // away from readers the moment it starts closing.
  const heldRefusal = useHeldValue(chatRefusal);
  // Held for the same reason: the line keeps its number while it closes.
  const heldReadCalls = useHeldValue(body?.worker.read_calls ?? null);
  // The backend reports both as 0 while this chat is being read, so
  // neither line shows during a healthy read.
  const partsPending = body?.long_message?.pending ?? 0;
  const partsFailed = body?.long_message?.failed ?? 0;
  const partsUnread = body?.long_message?.unread ?? 0;
  const allFailures = Object.entries(body?.stats.error_types ?? {});
  // A failure whose sentence names an action is on screen; the rest are
  // census behind "Show details".
  const actFailures = allFailures.filter(([why]) => failureAsksToAct(why));
  const failures = Object.fromEntries(
    allFailures.filter(([why]) => !failureAsksToAct(why)));
  const drops = body?.worker.dropped ?? {};
  // Only when that value is describing THIS line's event. See isEmptySettle.
  const emptyWhy = isEmptySettle(body?.worker.last_error)
    ? body!.worker.last_error!
    : "";
  const freeFailures = body ? body.stats.failed - body.stats.paid_and_lost : 0;
  // The summary adds up exactly the census lines it stands for.
  const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
  const trouble = body ? troubleProse({
    failed: body.stats.failed,
    // A write failure is counted twice at the source: a failed row
    // (`write_*`) AND `unhandled` (notebook_worker.py, the commit path). The
    // summary counts it once, under "failed". The rows are every run on
    // record and `unhandled` is this process's, so the difference never
    // counts a write failure twice; the census line keeps the exact number.
    unexpected: Math.max(0, body.worker.unhandled - sum(Object.entries(body.stats.error_types ?? {})
      .filter(([why]) => why.startsWith("write_")).map(([, n]) => n))),
    skipped: sum(Object.values(skips)) + body.worker.refused_by_breaker
      + body.worker.refused_by_chat_breaker + body.worker.dropped_offers,
    dropped: sum(Object.values(drops)),
    empty: body.worker.settled_empty,
  }) : null;

  /* The refresh that rode on this card's poll (a settled run invalidates
   * the note list) moved to the panel frame: this card now lives in the
   * Settings view, and the list and the Notes tab's count must follow a
   * run whichever view or tab is open. See useNotesFollowRuns in
   * notesTabState.ts. */

  async function toggle(next: boolean) {
    try {
      await setAuto.mutateAsync([next]);
    } catch (err) {
      pushError(err, "error");
    }
  }

  async function overrideForThisChat(next: boolean | null) {
    if (chatId == null) return;
    try {
      await setChatAuto.mutateAsync([chatId, next]);
    } catch (err) {
      pushError(err, "error", { chatId });
    }
  }

  async function unstick(only?: number) {
    try {
      await reset.mutateAsync(only);
    } catch (err) {
      pushError(err, "error");
    }
  }

  return (
    <SlideIn>
      <section className="space-y-3 p-4" aria-label="Background notes">
        <h4
          className="text-label font-semibold"
          style={{ color: "var(--color-es-text-light)" }}
        >
          Background notes
        </h4>

        {/* No aria-label. The wrapping label already names this control
            with the words on screen; an aria-label OVERRIDES those, so
            voice control could not address the switch by what it says -
            and here it also duplicated the name, which is worse. SwitchRow
            is that rule made into a component. */}
        {/* NO SWITCH UNTIL THE VALUE IS KNOWN. A two-state switch cannot
            say "unknown": drawn off it reads as off, and a screen reader
            says "off". So until the stored value is read there is no switch
            here at all - its name, and a line that says it is loading or
            could not be read, with Try again. The line that explains "Off"
            waits for the value too. It used to be drawn ON (`?? true`),
            then OFF; both were guesses. */}
        {auto.isSuccess ? (
          <SwitchRow
            id={AUTO_ACCEPT_SWITCH}
            checked={auto.data.enabled}
            disabled={setAuto.isPending}
            onCheckedChange={(v) => void toggle(v)}
          >
            Keep suggestions without asking
          </SwitchRow>
        ) : (
          <p className="text-ui leading-relaxed text-muted-foreground">
            Keep suggestions without asking
          </p>
        )}
        {/* The first read only: a retry keeps its own line open instead. */}
        <Collapse open={auto.isPending && auto.errorUpdateCount === 0} className="min-w-0">
          <p className="text-label leading-relaxed text-muted-foreground" role="status">
            {SETTING_LOADING}
          </p>
        </Collapse>
        <SettingUnread
          open={auto.isError}
          retrying={auto.isFetching}
          settled={settledAt(auto)}
          onRetry={() => void auto.refetch()}
          // The id lands on Base UI's hidden checkbox, not on the switch
          // the reader sees; the switch is its role="switch" sibling.
          focusAfter={() => document.getElementById(AUTO_ACCEPT_SWITCH)
            ?.closest("label")?.querySelector<HTMLElement>('[role="switch"]') ?? null}
        />

        <Collapse open={auto.isSuccess} className="min-w-0">
          <p className="text-label leading-relaxed text-muted-foreground">
            {AUTO_ACCEPT_OFF}
          </p>
        </Collapse>

        {/* WHAT THIS CHAT WILL ACTUALLY DO.
            The switch above is the GLOBAL setting; a chat can carry its own
            answer, and one opened from an imported card does. The panel used
            to render the global value alone, so that chat showed "on" while
            the extractor was correctly forcing review - the indicator and the
            decision disagreeing about the one case the override exists for.

            And the reason it is a control rather than a label: the import
            signal cannot see somebody pasting a downloaded card's fields
            into the form by hand. Nothing in the application can. The reader
            can, so this is where they say so.

            THE WORDS WRAP. "Keep this chat's suggestions without asking" is
            274 px on one line and this block's column, the section's, is
            250 px at the panel's 300 px floor, so a label held to one line
            ran past the column and the whole tab scrolled sideways. The
            button's height follows its lines, from the 28 px of one. The
            Collapse hands its inner a minimum width of 0 as well: the inner
            is a grid item, whose automatic minimum is its content's own
            width, so a wide child also pushed the sentence above it past the
            column. */}
        <Collapse open={chatId != null && auto.isSuccess} className="min-w-0">
          {auto.data && (
            <div className="space-y-1" data-testid="chat-auto-accept">
              {/* Only when this chat's answer is not simply the switch's:
                  otherwise the line restated the switch above it. */}
              <Collapse
                open={auto.data.overridden
                  || (auto.data.effective ?? auto.data.enabled) !== auto.data.enabled}
                className="min-w-0"
              >
              <p
                className="text-label leading-relaxed text-muted-foreground"
                data-testid="chat-auto-accept-line"
              >
                In this chat, most suggestions are{" "}
                {auto.data.effective ?? auto.data.enabled
                  ? "kept without asking"
                  : "held for review"}
                {auto.data.overridden
                  ? " - this chat decides, not the switch above."
                  : "."}
              </p>
              </Collapse>
              <PendingButton
                type="button"
                size="sm"
                variant="ghost"
                // Keeps the keyboard while it saves: its words change in
                // place, and a disabled button would have dropped focus.
                pending={setChatAuto.isPending}
                data-testid="chat-auto-accept-toggle"
                onClick={() => void overrideForThisChat(
                  auto.data.overridden
                    ? null
                    : !(auto.data.effective ?? auto.data.enabled))}
                className="persona-ghost-action h-auto min-h-7 whitespace-normal px-2 py-1 text-left text-ui"
              >
                {auto.data.overridden
                  ? "Follow the setting above"
                  : ((auto.data.effective ?? auto.data.enabled)
                      ? "Hold this chat's suggestions for review"
                      : "Keep this chat's suggestions without asking")}
              </PendingButton>
            </div>
          )}
        </Collapse>

        <Collapse open={body != null} className="min-w-0">
          {body && (
            <div
              className="persona-card space-y-1"
              data-testid="worker-status"
              // "Open Settings" under a "Not read" line lands here when the
              // reason has no button of its own (a stopped reader, the
              // day's cap): the card says why, and holds the keyboard.
              data-notes-target={NOTES_TARGET.workerCard}
              tabIndex={-1}
            >
              {/* THE STATE, unless the reader is dead. A dead worker used to
                  sit under "Running.", two status sentences contradicting
                  each other on one card; now the alert takes the
                  state line's place. */}
              <Collapse open={body.worker.alive} className="min-w-0">
                <p className="text-label leading-relaxed text-muted-foreground">
                  {STATE_PROSE[state] ?? state}
                </p>
              </Collapse>
              {/* The worker died. Asks the reader to act, so it is never
                  behind the details door. The exception's class name rides
                  in the title for a bug report, not in the sentence. */}
              <Collapse open={!body.worker.alive} className="min-w-0">
                <p
                  className="persona-local-error"
                  role="alert"
                  title={body.worker.died && echoable(body.worker.died)
                    ? body.worker.died : undefined}
                >
                  {WORKER_DEAD}
                </p>
              </Collapse>
              {/* A SECOND line, never a replacement for the first. The
                  sentence above is about the whole feature and must not
                  change because one conversation broke; this one is about the
                  chat in front of the reader, and it exists because a chat
                  stopped on its own is otherwise a panel reading "Running."
                  over a conversation nothing will ever read. */}
              <Collapse open={!!chatState && chatState !== "closed"} className="min-w-0">
                <p
                  className="text-label leading-relaxed text-muted-foreground"
                  data-testid="worker-chat-state"
                >
                  {CHAT_STATE_PROSE[chatState ?? ""] ?? chatState}
                </p>
              </Collapse>
              {/* 68 a: a read of long messages takes more than one call
                  rather than skipping what did not fit, and each call is
                  one of today's. Said only when it happened, about the chat
                  on screen. */}
              <Collapse open={(body.worker.read_calls ?? 0) > 1} className="min-w-0">
                <p
                  className="text-label leading-relaxed text-muted-foreground"
                  data-testid="worker-read-calls"
                >
                  {readCallsProse(heldReadCalls ?? 0)}
                </p>
              </Collapse>
              <Collapse open={partsPending > 0} className="min-w-0">
                <p
                  className="text-label leading-relaxed text-muted-foreground"
                  data-testid="worker-parts-pending"
                >
                  {PARTS_PENDING_PROSE}
                </p>
              </Collapse>
              <Collapse open={partsFailed > 0} className="min-w-0">
                <p
                  className="text-label leading-relaxed text-muted-foreground"
                  data-testid="worker-parts-failed"
                >
                  {PARTS_FAILED_PROSE}
                </p>
              </Collapse>
              <Collapse open={partsUnread > 0} className="min-w-0">
                <p
                  className="text-label leading-relaxed text-muted-foreground"
                  data-testid="worker-parts-unread"
                >
                  {PARTS_UNREAD_PROSE}
                </p>
              </Collapse>
              {/* Today's spend, in dollars as the reading strip says it. The
                  lifetime figure is its own line behind the door, named "All
                  time", so the two are never read as one number. */}
              <p className="text-label leading-relaxed text-muted-foreground">
                {todayProse(body.spend.calls, body.daily_cap, body.spend.cost)}
              </p>

              {/* "We do not know" is not "it was free". The dollar figure is a
                  sum over a NOT NULL column, so a call the provider declined
                  to price landed in it as zero. Beside the figure it
                  qualifies, not behind the door. */}
              <Collapse open={body.spend.cost_unknown > 0} className="min-w-0">
                <p
                  className="text-label leading-relaxed text-muted-foreground"
                  data-testid="worker-cost-unknown"
                >
                  {unpricedProse(body.spend.cost_unknown)}
                </p>
              </Collapse>

              {/* ASKS THE READER TO ACT, so on screen: no key, or a proxy
                  that is required and down, means nothing can be read until
                  it is fixed. Its count stays with it. */}
              {Object.entries(keySkips).map(([why, n]) => (
                <p
                  key={why}
                  className="text-label leading-relaxed text-muted-foreground"
                  data-testid="worker-key-skip"
                >
                  {skippedProse(n)} {skipProse(why)}
                </p>
              ))}

              {/* A failure whose cause names an action (the catalogue's
                  sentence: top up, check the key, check the proxy). Same
                  denominator as the census lines behind the door. */}
              {actFailures.map(([why, n]) => (
                <p
                  key={why}
                  className="text-label leading-relaxed text-muted-foreground"
                  data-testid="worker-failure-act"
                >
                  {actFailureProse(n)} {failureProse(why)}
                </p>
              ))}

              {/* About THIS chat and nothing else, with no count: the backend
                  sends it only while the refusal still names the model this
                  chat uses (3.8). It asks the reader to pick a model, so it
                  is never behind the door. It changes the card's height, so
                  it arrives and leaves through a Collapse. */}
              <Collapse open={chatRefusal != null} className="min-w-0">
                <p
                  className="text-label leading-relaxed text-muted-foreground"
                  data-testid="worker-chat-refusal"
                >
                  {heldRefusal
                    ? skipProse(heldRefusal)
                    : null}
                </p>
                {heldRefusal === "model_not_extractable" && (
                  <Disclosure label={DISCLOSURE_WORDS.whyModel} testId="worker-refusal-why" className="mt-1">
                    <p className="text-label leading-relaxed text-muted-foreground">
                      {REASON_WHY.modelNotExtractable.join(" ")} {CHAT_REFUSAL_AFTER}
                    </p>
                  </Disclosure>
                )}
              </Collapse>

              {/* ONE LINE FOR WHAT WENT WRONG. The census below used
                  to be the card: in the stress state 724 words, and the one
                  line that needed the reader sat among ten that did not.
                  Each count here is spelled out, in the same words, behind
                  "Show details". */}
              <Collapse open={trouble != null} className="min-w-0">
                <p
                  className="text-label leading-relaxed text-muted-foreground"
                  data-testid="worker-trouble"
                >
                  {trouble}
                </p>
              </Collapse>

              <Disclosure label={DISCLOSURE_WORDS.details} testId="worker-details">
                <p className="text-label leading-relaxed text-muted-foreground">
                  {lifetimeProse(body.stats.done, body.spend_lifetime.calls,
                    body.spend_lifetime.cost)}
                </p>

                {/* Failures and refusals are separate lines because they call
                    for different things: one is the provider's problem, the
                    other is a limit doing its job.

                    Rows that cost money carry status 'failed' too, so they are
                    a SUBSET of this count, and "nothing was lost" is only true
                    of the rest. The subset is `paid_and_lost`, not
                    `abandoned`: a `write_*` failure happens AFTER the reply
                    was billed. */}
                <Collapse open={freeFailures > 0} className="min-w-0">
                  <p className="text-label leading-relaxed text-muted-foreground">
                    {partsUnread > 0
                      ? CENSUS.failedPartUnread(freeFailures)
                      : partsFailed > 0
                        ? CENSUS.failedPartRetried(freeFailures)
                        : CENSUS.failedFree(freeFailures)}
                  </p>
                </Collapse>

                <Collapse open={body.stats.abandoned > 0} className="min-w-0">
                  <p
                    className="text-label leading-relaxed text-muted-foreground"
                    data-testid="worker-abandoned"
                  >
                    {CENSUS.abandoned(body.stats.abandoned)}
                  </p>
                </Collapse>

                {/* WHY they failed. THE DENOMINATOR IS PART OF THE SENTENCE:
                    these add up to `failed`, which the two lines above are
                    subsets of, so "2 of 3 failures" is what keeps a reader
                    from adding them to those. */}
                {Object.entries(failures).map(([why, n]) => (
                  <p
                    key={why}
                    className="text-label leading-relaxed text-muted-foreground"
                    data-testid="worker-failure-reason"
                  >
                    {failureOfProse(n, body.stats.failed)} {failureProse(why)}
                  </p>
                ))}

                {Object.entries(skips).map(([why, n]) => (
                  <p
                    key={why}
                    className="text-label leading-relaxed text-muted-foreground"
                  >
                    {skippedProse(n)} {skipProse(why)}
                  </p>
                ))}

                <Collapse open={body.worker.unhandled > 0} className="min-w-0">
                  <p
                    className="text-label leading-relaxed text-muted-foreground"
                    data-testid="worker-unhandled"
                    // The value is shared with the empty-settle line, and it
                    // holds whichever event happened LAST; it is named only
                    // when it is not that line's, and then only as a title.
                    title={body.worker.last_error && !emptyWhy
                      && echoable(body.worker.last_error)
                      ? body.worker.last_error : undefined}
                  >
                    {CENSUS.unhandled(body.worker.unhandled)}
                  </p>
                </Collapse>

                {/* PAID FOR AND EMPTY. Not a failure and not a refusal: the
                    call went out, came back and was billed, and nothing
                    could be landed from it. `last_error` is one value about
                    the most recent event of any kind, so it is named as the
                    most recent and never as the reason for all of them. */}
                <Collapse open={body.worker.settled_empty > 0} className="min-w-0">
                  <p
                    className="text-label leading-relaxed text-muted-foreground"
                    data-testid="worker-settled-empty"
                  >
                    {CENSUS.settledEmpty(body.worker.settled_empty)}{" "}
                    {emptyWhy
                      ? <>The most recent: {emptyProse(emptyWhy)}.</>
                      : CENSUS.settledEmptyWhy}
                  </p>
                </Collapse>

                {/* WHAT THE MODEL OFFERED AND THE PARSER REFUSED. A note
                    thrown away here was paid for and never shown. */}
                {Object.entries(drops).map(([why, n]) => (
                  <p
                    key={why}
                    className="text-label leading-relaxed text-muted-foreground"
                    data-testid="worker-dropped-note"
                  >
                    {CENSUS.dropped(n)} {dropProse(why)}
                  </p>
                ))}

                <Collapse open={body.worker.refused_by_breaker > 0} className="min-w-0">
                  <p className="text-label leading-relaxed text-muted-foreground">
                    {CENSUS.pausedTurns(body.worker.refused_by_breaker)}
                  </p>
                </Collapse>

                {/* Counted apart from the line above, and said apart: "the
                    provider is down" and "these conversations are broken"
                    are different, and only one means the rest is working. */}
                <Collapse open={body.worker.refused_by_chat_breaker > 0} className="min-w-0">
                  <p className="text-label leading-relaxed text-muted-foreground">
                    {CENSUS.failingChatTurns(body.worker.refused_by_chat_breaker)}{" "}
                    {body.worker.tripped_chats > 0
                      ? `${CENSUS.failingNow(body.worker.tripped_chats)} ` : ""}
                    {CENSUS.otherChatsRan}
                  </p>
                </Collapse>

                <Collapse open={body.worker.dropped_offers > 0} className="min-w-0">
                  <p className="text-label leading-relaxed text-muted-foreground">
                    {CENSUS.busyTurns(body.worker.dropped_offers)}
                  </p>
                </Collapse>
              </Disclosure>
            </div>
          )}
        </Collapse>

        {/* The way back to a chat's earlier messages is not on this card any
            more. One job at a time means one door: the strip at the top of
            the Notebook panel (BackfillStrip.tsx) previews, starts, stops
            and undoes a reading job, and it carries the unread-backlog line
            that used to sit here, with the same sentence. */}

        {/* Only when it means something. A reset button that is always there
            invites pressing it at random; one that appears when the thing is
            actually stuck says what it is for. */}
        {/* `half_open` belongs here and was missing. The panel says the next
            turn sends a BILLED trial call, and then offered the reader no
            way to act on that - the one state where the sentence on screen
            asks for a decision was the one state with no button. */}
        {(state === "stopped" || state === "open" || state === "half_open") && (
          <FadeIn>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={reset.isPending}
              onClick={() => void unstick()}
              data-notes-target={NOTES_TARGET.tryAll}
              className="persona-ghost-action h-7 gap-1.5 px-2 text-ui"
            >
              <RotateCcw size={12} className="size-3" />
              {WORKER_WORDS.tryAll}
            </Button>
          </FadeIn>
        )}

        {/* A SECOND button, because they undo different things. The one
            above lifts the process-wide refusal and every per-chat one with
            it; this one unsticks exactly the conversation in front of the
            reader and leaves the others where they are. */}
        {chatId != null && chatState && chatState !== "closed" && (
          <FadeIn>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={reset.isPending}
              onClick={() => void unstick(chatId)}
              data-notes-target={NOTES_TARGET.tryChat}
              className="persona-ghost-action h-7 gap-1.5 px-2 text-ui"
            >
              <RotateCcw size={12} className="size-3" />
              {WORKER_WORDS.tryChat}
            </Button>
          </FadeIn>
        )}
      </section>
    </SlideIn>
  );
}
