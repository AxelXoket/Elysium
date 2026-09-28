import { useEffect, useRef, useState } from "react";
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
import { BookOpen, RotateCcw } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { useUiStore } from "@/lib/store/uiStore";
import { SwitchRow } from "@/components/settings/SwitchRow";
import { SlideIn } from "@/components/motion/SlideIn";
import { Collapse } from "@/components/motion/Collapse";
import { FadeIn } from "@/components/motion/FadeIn";
import { useErrorStore } from "@/lib/errors/errorStore";
// The sentences for this app's own reason codes, borrowed rather than
// rewritten: a failed call's error_type carries one of those codes after a
// colon, and the file below is where every code the backend can put in front
// of a person keeps its English.
import { getErrorMessage, isKnownErrorCode } from "@/lib/errors/errorMessages";
import { keys } from "@/lib/query/keys";
import {
  useWorkerStatus,
  useResetWorker,
  useAutoAccept,
  useSetAutoAccept,
  useSweepChat,
  useSetChatAutoAccept,
} from "@/lib/query/notebook";

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
  notebook_daily_cap_reached: "today's call limit was already used",
  // Named rather than counted as a failure, because nothing was sent. The
  // old order claimed the day's quota first and only then discovered there
  // was no key, so twenty of sixty calls burned without a byte leaving the
  // machine and the panel said "stopped" with nothing attached.
  api_key_not_set:
    "no API key is set, so nothing could be sent. This costs you nothing "
    + "and no call was made - add a key in Settings and it picks up again",
  proxy_gate: "your proxy was required and not healthy",
  // Written by a different path from the others - commit_extraction's
  // require_trace branch, not the worker's own SKIP_REASONS - so the gate
  // that keeps this table complete never covered it, and the panel printed
  // the raw token at the reader. Which is the one thing this table exists
  // to stop.
  plan_invalidated:
    "you rewrote the last message it was reading, so the reply came back "
    + "describing wording you had already replaced. It was thrown away and "
    + "still paid for, and that stretch is read again later",
  // Not "the chat was cleared". Anything that removes the last message of
  // the range while the reply is out lands here: a deleted message, a
  // cleared chat, an aborted send being tidied up, or an edit whose swept
  // tail happened to be where the range ended. NOT a regenerated reply -
  // that deletes nothing, so nothing is skipped and the note is written from
  // the reply you discarded. See the correction note at the top of this file.
  range_cleared:
    "the last message it was reading was gone by the time the reply came "
    + "back, removed by a delete or a cleared chat. It was thrown away and "
    + "still paid for; whatever survives of that stretch is read again, the "
    + "removed messages never are",
};

/** What a reason with no sentence reads as. A snake_case token is the one
 *  thing this whole table exists to keep off the screen, and the fallback
 *  was printing exactly that for anything the backend adds ahead of this
 *  file. The code rides along in brackets so a bug report can still carry
 *  it; the sentence is what a reader gets. */
// eslint-disable-next-line react-refresh/only-export-components -- reads the table above; same reason, same boundary
export function skipProse(reason: string): string {
  return (
    SKIP_PROSE[reason]
    ?? `it was refused for a reason this version has no words for (${reason})`
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
  truncated:
    "The reply was cut off before it finished, so nothing in it could be "
    + "relied on.",
  refused:
    "The model declined to answer for that stretch of the conversation.",
  empty_content: "The reply came back empty.",
  unparseable: "The reply was not readable as the form this feature asks for.",
  not_an_object: "The reply came back in a shape this feature cannot read.",
  // Named apart from the two around it because it is the one that could be
  // mistaken for a quiet stretch. An empty list is how a model says it found
  // nothing worth writing down, and it is not an error; this is the answer
  // that left the list out altogether.
  no_facts_key:
    "The reply left out the part the notes are read from. That is not the "
    + "same as finding nothing, which the model says with an empty list.",
  facts_not_a_list:
    "The reply put something other than a list of notes where the notes go.",
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
    return "The reply came back and was paid for, but the notes it carried "
      + `could not be written down${bracket(errorType)}.`;
  }

  // The mirror image, and worth its own sentence for exactly that reason:
  // this one happens BEFORE the request, so no call was made and nothing was
  // spent. Rolled into the generic answer it would read like a failure the
  // reader had paid for.
  if (errorType.startsWith("prompt_")) {
    return "The request could not be assembled, so nothing was sent and "
      + `nothing was spent${bracket(errorType)}.`;
  }

  const reason = errorType.slice(errorType.indexOf(":") + 1);
  if (errorType.includes(":") && isKnownErrorCode(reason)) {
    return getErrorMessage(reason);
  }

  return "The call failed for a reason this version has no words for"
    + `${bracket(errorType)}.`;
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
  // Subjectless, or as near as English allows, because the sentence follows
  // a count: "4 suggestions were thrown away: nothing in IT" reads as a
  // sentence about one of them.
  over_cap: "one run may keep only so many notes, and these were over that",
  off_schema: "the note did not have the shape a note has to have",
  too_long: "the note was longer than a note is allowed to be",
  empty_text: "the note had no text at all",
  ungrounded:
    "the quote the note was built on was not in the messages being read, so "
    + "there was nothing behind it",
  // The honest half of a gate that is not free. Measured over a thirty case
  // set it catches nine invented notes in ten and drops four true notes in
  // twenty, and that second number only ever reaches a person here - so it
  // is said, rather than left to look like the model having nothing to say.
  unanchored:
    "no name and no number from the quote appeared in the note itself. That "
    + "check also throws away roughly one true note in five, so a note you "
    + "expected may be missing rather than wrong",
  not_worth_a_slot:
    "the note was not worth a place in a notebook that is sent with every "
    + "message",
};

/** As `skipProse`, for the parser's refusals. Same fallback, same reason:
 *  the backend can grow a reason ahead of this file, and the token itself is
 *  the one thing that must not be what a reader gets. */
// eslint-disable-next-line react-refresh/only-export-components -- reads the table above; same reason, same boundary
export function dropProse(reason: string): string {
  return (
    DROP_PROSE[reason]
    ?? "it was thrown out for a reason this version has no words for"
      + bracket(reason)
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
  settled_empty: "every note it offered was one this chat already had",
  settled_duplicate:
    "those messages had already been read, so the answer was thrown away",
  settled_stale_attempt:
    "a second attempt at the same messages had already taken it over",
  settled_chat_gone:
    "the chat it was reading was deleted while the reply was on its way",
  settled_nothing: "the write came back with no answer at all",
};

/** As `skipProse`, for the empty settles. */
// eslint-disable-next-line react-refresh/only-export-components -- reads the table above; same reason, same boundary
export function emptyProse(reason: string): string {
  return (
    EMPTY_PROSE[reason]
    ?? "it landed nothing for a reason this version has no words for"
      + bracket(reason)
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
  open: "Paused after repeated failures. It will try again by itself.",
  // The state that was missing, and the omission was on the screen rather
  // than in the breaker. Once the cooldown elapses exactly one call is let
  // through as a trial - a real, billed request - while `opened_at` stays
  // set, so the panel went on saying "Paused" over a call that was going
  // out. The distinction matters because one of these two costs money.
  half_open: "Paused, but the cooldown is over: the next turn sends one trial"
    + " call, and that call is billed like any other.",
  stopped: "Stopped after too many failures. It will not try again until you say so.",
};

/** The same states for ONE chat, and every sentence has to say that the rest
 *  are fine. A reader who sees "Paused after repeated failures" with no such
 *  clause reads it as the feature being down and goes looking for a fault
 *  that is not there - which is the whole reason the breaker was split. */
// eslint-disable-next-line react-refresh/only-export-components -- same boundary as STATE_PROSE
export const CHAT_STATE_PROSE: Record<string, string> = {
  open: "This chat is paused after repeated failures here. Other chats are"
    + " unaffected, and it will try this one again by itself.",
  half_open: "This chat is paused, but the cooldown is over: its next turn"
    + " sends one trial call, and that call is billed. Other chats are"
    + " unaffected.",
  stopped: "This chat is stopped after too many failures here. Other chats"
    + " keep running; this one will not try again until you say so.",
};

/** Why the "read the earlier part" button refused, said plainly.
 *
 *  This was three arms of an inline ternary whose last one was a catch-all
 *  reading "It is already reading. Give it a moment." The moment the worker
 *  learned to refuse a paused chat, that arm told the reader the exact
 *  opposite of what was happening. A table with a named fallback cannot do
 *  that quietly, and a test can walk it against the backend's own set. */
// eslint-disable-next-line react-refresh/only-export-components -- same boundary as STATE_PROSE
export const SWEEP_PROSE: Record<string, string> = {
  nothing_unread: "Nothing here is unread - it has all been looked at.",
  already_running: "It is already reading. Give it a moment.",
  already_in_flight: "It is already reading this chat. Give it a moment.",
  chat_paused: "This chat is paused after repeated failures here, so nothing"
    + " was sent. Other chats are unaffected.",
  chat_stopped: "This chat is stopped after too many failures here, so"
    + " nothing was sent. Use the button below to try this chat again.",
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
  const qc = useQueryClient();
  // The chat is part of the QUESTION, not just of the answer: the worker's
  // per-chat breaker is only reported for the chat that was asked about.
  const status = useWorkerStatus(chatId);
  const reset = useResetWorker();
  const sweep = useSweepChat();
  const [swept, setSwept] = useState<string | null>(null);
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
  const skips = body?.stats.skip_reasons ?? {};
  const failures = body?.stats.error_types ?? {};
  const drops = body?.worker.dropped ?? {};
  // Only when that value is describing THIS line's event. See isEmptySettle.
  const emptyWhy = isEmptySettle(body?.worker.last_error)
    ? body!.worker.last_error!
    : "";

  /* WHAT THE POLL IS FOR ON THE OTHER SIDE OF THIS TAB.
   *
   * The note list has no clock of its own. The only thing that ever
   * invalidated it was a mutation the READER made, so notes the background
   * extractor wrote while they sat reading stayed off the screen until they
   * clicked away to another tab and back - money spent on notes the person
   * who paid for them could not see. The status poll above is already the one
   * signal that a run happened, so the refresh rides on it: no second timer,
   * no shorter interval, and nothing that refetches the list on a clock.
   *
   * The whole `notebook` prefix, like every mutation in query/notebook.ts:
   * a settled run moves the entries AND `notebook_chars`, and naming one key
   * is how the other goes stale. The direction is one way, status -> notes,
   * so the rule the worker key exists for is untouched: a note mutation still
   * cannot drag a status poll along with it.
   *
   * CHANGED, not increased. `done` is a COUNT over `notebook_extractions`
   * across every chat, and nothing keeps those rows: deleting or clearing a
   * chat, deleting a character, and deleting or editing a message all remove
   * them, so the number goes DOWN as readily as up. A trigger written as
   * "higher than last time" would go blind for every run that lands after a
   * delete, which is exactly the quiet miss this record is about. Any
   * movement means the extractor settled something.
   *
   * It can also move when nothing was written - a run that found nothing, or
   * one whose every proposal deduplicated - so this can cost one local
   * request that changes no pixel. That is the price of the only signal there
   * is, it goes to this machine rather than to a provider, and the opposite
   * mistake is the defect.
   *
   * The COUNT is the dependency, not the response. `status.data` is a fresh
   * object on every poll, so depending on it would run this on every tick
   * and put the note list back on a twenty-second timer under another name.
   * The ref is here for the other half: the first count to arrive is not a
   * change, it is the starting point.
   */
  const doneCount = status.data?.stats.done;
  const lastDoneCount = useRef(doneCount);
  useEffect(() => {
    if (doneCount === undefined) return;
    const seenBefore = lastDoneCount.current !== undefined;
    lastDoneCount.current = doneCount;
    // The first status to arrive is not news. The note list was fetched on
    // the same mount and is already as fresh as this number, so treating it
    // as a change would buy a duplicate round trip every time the tab opens.
    if (!seenBefore) return;
    void qc.invalidateQueries({ queryKey: keys.notebook() });
  }, [doneCount, qc]);

  async function toggle(next: boolean) {
    try {
      await setAuto.mutateAsync([next]);
    } catch (err) {
      pushError(err, "error");
    }
  }

  async function readTheRest() {
    if (chatId == null) return;
    setSwept(null);
    try {
      const out = await sweep.mutateAsync([chatId]);
      // The two ordinary refusals are not errors and must not read as one.
      // "There is nothing unread" is the answer somebody pressing this
      // button most often gets, and a toast would make it look like a fault.
      setSwept(
        out.started
          ? "Reading the earlier part of this chat now."
          : sweepProse(out.reason),
      );
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
        <SwitchRow
          checked={auto.data?.enabled ?? true}
          // Not operable until the stored value is known: a switch that
          // shows a guess and accepts a click is the "you believe it is in
          // force and it is not" failure this feature warns about.
          disabled={setAuto.isPending || !auto.isSuccess}
          onCheckedChange={(v) => void toggle(v)}
        >
          Keep suggestions without asking
        </SwitchRow>

        <p className="text-label leading-relaxed text-muted-foreground">
          Off, a suggestion waits in the list until you keep it, and is not
          sent to the model meanwhile. A chat opened from an imported card
          always waits, whatever this says.
        </p>

        {/* WHAT THIS CHAT WILL ACTUALLY DO.
            The switch above is the GLOBAL setting; a chat can carry its own
            answer, and one opened from an imported card does. The panel used
            to render the global value alone, so that chat showed "on" while
            the extractor was correctly forcing review - the indicator and the
            decision disagreeing about the one case the override exists for.

            And the reason it is a control rather than a label: the import
            signal cannot see somebody pasting a downloaded card's fields
            into the form by hand. Nothing in the application can. The reader
            can, so this is where they say so. */}
        <Collapse open={chatId != null && auto.isSuccess}>
          {auto.data && (
            <div className="space-y-1" data-testid="chat-auto-accept">
              <p className="text-label leading-relaxed text-muted-foreground">
                In this chat, suggestions are{" "}
                {auto.data.effective ?? auto.data.enabled
                  ? "kept without asking"
                  : "held for review"}
                {auto.data.overridden
                  ? " - this chat decides, not the switch above."
                  : "."}
              </p>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={setChatAuto.isPending}
                data-testid="chat-auto-accept-toggle"
                onClick={() => void overrideForThisChat(
                  auto.data.overridden
                    ? null
                    : !(auto.data.effective ?? auto.data.enabled))}
                className="persona-ghost-action h-7 px-2 text-label"
              >
                {auto.data.overridden
                  ? "Follow the setting above"
                  : ((auto.data.effective ?? auto.data.enabled)
                      ? "Hold this chat's suggestions for review"
                      : "Keep this chat's suggestions without asking")}
              </Button>
            </div>
          )}
        </Collapse>

        <Collapse open={body != null}>
          {body && (
            <div className="persona-card space-y-1" data-testid="worker-status">
              <p className="text-label leading-relaxed text-muted-foreground">
                {STATE_PROSE[state] ?? state}
              </p>
              {/* A SECOND line, never a replacement for the first. The
                  sentence above is about the whole feature and must not
                  change because one conversation broke; this one is about the
                  chat in front of the reader, and it exists because a chat
                  stopped on its own is otherwise a panel reading "Running."
                  over a conversation nothing will ever read. */}
              <Collapse open={!!chatState && chatState !== "closed"}>
                <p
                  className="text-label leading-relaxed text-muted-foreground"
                  data-testid="worker-chat-state"
                >
                  {CHAT_STATE_PROSE[chatState ?? ""] ?? chatState}
                </p>
              </Collapse>
              <p className="text-label leading-relaxed text-muted-foreground">
                {body.stats.done} runs · {body.spend.calls} of {body.daily_cap}{" "}
                calls today · {body.spend.cost.toFixed(5)} credits{" "}
                {/* Same line as today's count - "beside", not below - but
                    dimmed with the app's existing secondary-text idiom
                    (opacity-70, as ModelPanel's "Selected:" label and
                    ImageOutputSetting's hint text already use) and named
                    "lifetime" rather than positioned next to "today" with no
                    label of its own. Today's number is the one that governs
                    the cap, so it keeps full weight; this one does not.

                    The docstring on spend_lifetime (notebook_store.py) argues
                    that filtering the lifetime SUM would hide "real spend that
                    happened" from "the one screen that is supposed to be
                    honest about every credit spent" - an argument about MONEY,
                    not about calls. A parenthetical that named only
                    `.calls` made that argument and then answered a different
                    question: today's money sat beside a lifetime call count,
                    so the one figure the docstring is about never reached the
                    screen. Both numbers now use the same "N unit" words the
                    line above already uses for today ("calls" / "credits"),
                    not a bare number - the units are what mark this as a
                    different KIND of figure, not just its dimmer position. */}
                <span className="opacity-70">
                  ({body.spend_lifetime.calls} calls,{" "}
                  {body.spend_lifetime.cost.toFixed(5)} credits lifetime)
                </span>
              </p>

              {/* "We do not know" is not "it was free".

                  The credit figure above is a sum over a NOT NULL column, so a
                  call the provider declined to price landed in it as zero - and
                  the line then reads as a call that cost nothing. It is the
                  one number on this card the reader might act on, so the card
                  has to say when part of it is missing rather than quietly
                  rounding an unknown down to the cheapest possible answer.

                  Only when there is something to say: a provider that prices
                  everything never sees this line. */}
              <Collapse open={body.spend.cost_unknown > 0}>
                <p
                  className="text-label leading-relaxed text-muted-foreground"
                  data-testid="worker-cost-unknown"
                >
                  {body.spend.cost_unknown} of the calls made today came back with
                  no price - the credit figure above leaves them out rather than
                  counting them as free.
                </p>
              </Collapse>

              {/* Failures and refusals are separate lines because they call for
                  different things: one is the provider's problem, the other is
                  a limit doing its job. */}
              {/* Rows that cost money carry status 'failed' too, so they are
                  a SUBSET of this count. Reporting the whole of it under
                  "nothing was lost" was the lie: for those the money was spent
                  and those messages are never read. Split, so each line is
                  true of the runs it describes.

                  The subset is `paid_and_lost`, not `abandoned`. `abandoned`
                  is only the calls the app was killed in the middle of; a
                  `write_*` failure happens AFTER the reply has been sent,
                  generated and billed, and it was landing on the "nothing was
                  lost" side of this subtraction. */}
              <Collapse open={body.stats.failed - body.stats.paid_and_lost > 0}>
                <p className="text-label leading-relaxed text-muted-foreground">
                  {body.stats.failed - body.stats.paid_and_lost} runs failed.
                  Nothing was lost - the messages they could not read stay
                  unread, not skipped.
                </p>
              </Collapse>

              <Collapse open={body.stats.abandoned > 0}>
                <p
                  className="text-label leading-relaxed text-muted-foreground"
                  data-testid="worker-abandoned"
                >
                  {body.stats.abandoned} runs were cut off after the request had
                  already been sent, so they were paid for. Elysium does not
                  send those stretches again, which is why the notes for them
                  are missing rather than duplicated.
                </p>
              </Collapse>

              {/* WHY they failed, which the two lines above do not say.
                  Those two answer "how many" and "did it cost money"; a reader
                  looking at a notebook that wrote nothing needs the third
                  question, and the answer to it was stored on every failed row
                  from the start and read by nothing.

                  Subordinate wording on purpose - "of the failures", not "runs
                  failed" - because these numbers add up to `failed`, which is
                  already on screen above. Two lines that each look like a total
                  are how a reader ends up adding them.

                  It carries the abandoned rows too, and that is not the same
                  as folding the abandoned COUNTER into this dict: the counter
                  stays its own number above precisely because it is nested
                  inside `failed` and must never be summed with it. Here every
                  key is nested in the same way, so nothing is misread by
                  sitting beside its neighbour. */}
              {Object.entries(failures).map(([why, n]) => (
                <p
                  key={why}
                  className="text-label leading-relaxed text-muted-foreground"
                  data-testid="worker-failure-reason"
                >
                  {/* THE DENOMINATOR IS PART OF THE SENTENCE.
                      Read cold, "2 of the failures" beside "3 runs failed"
                      invites the one sum that must not be made: the two lines
                      above are SUBSETS of `failed` (`failed` minus the paid
                      ones, and the abandoned ones), so the census adds up to a
                      total that appears nowhere else on the card. A reader
                      checking the arithmetic found six against three and had
                      no way to tell which number was wrong. Naming the total
                      here makes the lines reconcile in front of them, and it
                      is the only place `failed` itself is legible. */}
                  {n} of {body.stats.failed} failures: {failureProse(why)}
                </p>
              ))}

              {Object.entries(skips).map(([why, n]) => (
                <p
                  key={why}
                  className="text-label leading-relaxed text-muted-foreground"
                >
                  {n} skipped: {skipProse(why)}.
                </p>
              ))}

              {/* The worker died. Everything else on this card describes a
                  worker that is running; without this line the card describes
                  one that is not, in the same words. */}
              <Collapse open={!body.worker.alive}>
                <p className="persona-local-error" role="alert">
                  The background reader has stopped
                  {body.worker.died ? ` (${body.worker.died})` : ""}. Restart
                  Elysium to bring it back. Nothing was lost - unread messages
                  stay unread.
                </p>
              </Collapse>

              <Collapse open={body.worker.unhandled > 0}>
                <p
                  className="text-label leading-relaxed text-muted-foreground"
                  data-testid="worker-unhandled"
                >
                  {body.worker.unhandled} runs ended in an unexpected error
                  {/* The value is shared with the empty-settle line above, and
                      it holds whichever event happened LAST. Printed here
                      unconditionally it put that line's token in this line's
                      parentheses, so "an unexpected error (settled_chat_gone)"
                      named the wrong event with a word for the other one. The
                      counter still speaks for itself; only the attribution is
                      withheld. */}
                  {body.worker.last_error && !emptyWhy
                    ? ` (${body.worker.last_error})`
                    : ""}.
                  Those messages stay unread.
                </p>
              </Collapse>

              {/* PAID FOR AND EMPTY. Not a failure and not a refusal: the call
                  went out, came back and was billed, and nothing could be
                  landed from it. It was reported by a counter and a code that
                  reach no screen, so on the panel it looked exactly like a
                  quiet week - which is the one thing this card exists not to
                  do. The three ways in are named because they call for
                  different things: two of them are races that fix themselves,
                  the third is the reader's own edit. */}
              <Collapse open={body.worker.settled_empty > 0}>
                <p
                  className="text-label leading-relaxed text-muted-foreground"
                  data-testid="worker-settled-empty"
                >
                  {body.worker.settled_empty}{" "}
                  {body.worker.settled_empty === 1
                    ? "reply was"
                    : "replies were"}{" "}
                  paid for and produced no notes
                  {/* The reason, when the panel is entitled to it. `last_error`
                      is one value describing the most recent event of any
                      kind, so it is named as the most recent rather than as
                      the reason all of them landed nothing - which it is not,
                      and a count per reason is not on the wire. Without a
                      reason the line keeps the three ways in, which is still
                      more than the number alone said. */}
                  {emptyWhy
                    ? <>. The most recent: {emptyProse(emptyWhy)}.</>
                    : <>
                        {" "}- those messages had already been read, a retry
                        arrived first, or they changed while the reply was out.
                      </>}
                </p>
              </Collapse>

              {/* WHAT THE MODEL OFFERED AND THE PARSER REFUSED.
                  A note thrown away here was paid for and never shown, and the
                  counters were declared on the wire and rendered nowhere - so
                  a gate quietly discarding good notes and a model with nothing
                  to say produced the same empty list. */}
              {Object.entries(drops).map(([why, n]) => (
                <p
                  key={why}
                  className="text-label leading-relaxed text-muted-foreground"
                  data-testid="worker-dropped-note"
                >
                  {/* Counted in words, in the idiom the backlog line above
                      already uses: these are small numbers and "1 suggestions"
                      is the reading a reader trips over. */}
                  {n} suggestion{n === 1 ? " was" : "s were"} thrown away:{" "}
                  {dropProse(why)}.
                </p>
              ))}

              <Collapse open={body.worker.refused_by_breaker > 0}>
                <p className="text-label leading-relaxed text-muted-foreground">
                  {body.worker.refused_by_breaker} turns were passed over while
                  it was cooling down. Their messages are still unread.
                </p>
              </Collapse>

              {/* Counted apart from the line above, and said apart. Folded
                  together, "the provider is down" and "these conversations are
                  broken" read as one number, and only one of them means the
                  rest of the notebook is working. */}
              <Collapse open={body.worker.refused_by_chat_breaker > 0}>
                <p className="text-label leading-relaxed text-muted-foreground">
                  {body.worker.refused_by_chat_breaker} turns were passed over
                  in chats that keep failing on their own
                  {body.worker.tripped_chats > 0
                    ? ` (${body.worker.tripped_chats} of them right now)`
                    : ""}
                  . Every other chat kept running.
                </p>
              </Collapse>

              <Collapse open={body.worker.dropped_offers > 0}>
                <p className="text-label leading-relaxed text-muted-foreground">
                  {body.worker.dropped_offers} turns went unqueued while it was
                  behind. Their messages are still unread, so a later run picks
                  them up.
                </p>
              </Collapse>
            </div>
          )}
        </Collapse>

        {/* THE WAY BACK.
            The worker only ever moves forward: its cursor is a maximum, and
            the first read of a chat that already had a long history starts
            at the PRESENT on purpose - a notebook describing a conversation
            four hundred messages ago is worse than an empty one. That was a
            decision with no undo, and this is the undo. One call per press,
            against the same daily limit as an ordinary turn.

            Only with a chat open, because there is nothing to read
            otherwise. */}
        <Collapse open={chatId != null}>
          <div className="space-y-1">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={sweep.isPending || (body?.worker.sweeping ?? false)}
              onClick={() => void readTheRest()}
              data-testid="worker-sweep"
              className="persona-ghost-action h-7 gap-1.5 px-2 text-label"
            >
              <BookOpen size={12} className="size-3" />
              Read the earlier messages here
            </Button>
            {/* The data gate inside the Collapse is not decoration and not a
                second condition on the same thing. Collapse keeps its
                content mounted, so without it the paragraph is in the tree
                before the query resolves and `body!` throws; with the reads
                written as optional instead, the tree carries a sentence with
                two blanks in it from first paint, and the case that waits
                for this paragraph to ARRIVE (WorkerPanel.test.tsx, "offers
                the backlog rather than reading it") resolves on the blank
                one. Measured both ways; this is the one that keeps the
                paragraph a thing that appears when it has something to say. */}
            <Collapse open={(body?.worker.backlog.messages ?? 0) > 0}>
              {body && (
                <p
                  className="text-label leading-relaxed text-muted-foreground"
                  data-testid="worker-backlog"
                >
                  {body!.worker.backlog.chats} chat
                  {body!.worker.backlog.chats === 1 ? "" : "s"} have{" "}
                  {body!.worker.backlog.messages} unread messages. Nothing is
                  read without you asking - reading them costs calls.
                </p>
              )}
            </Collapse>

            <Collapse open={!!swept}>
              <p
                className="text-label leading-relaxed text-muted-foreground"
                role="status"
                data-testid="worker-sweep-said"
              >
                {swept}
              </p>
            </Collapse>
          </div>
        </Collapse>

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
              className="persona-ghost-action h-7 gap-1.5 px-2 text-label"
            >
              <RotateCcw size={12} className="size-3" />
              Try again now
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
              className="persona-ghost-action h-7 gap-1.5 px-2 text-label"
            >
              <RotateCcw size={12} className="size-3" />
              Try this chat again
            </Button>
          </FadeIn>
        )}
      </section>
    </SlideIn>
  );
}
