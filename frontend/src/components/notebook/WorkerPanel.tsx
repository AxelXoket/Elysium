import { useState } from "react";
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

import { Button } from "@/components/ui/button";
import { useUiStore } from "@/lib/store/uiStore";
import { Switch } from "@/components/ui/switch";
import { SlideIn } from "@/components/motion/SlideIn";
import { useErrorStore } from "@/lib/errors/errorStore";
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
 *  What follows from that is NOT cosmetic and is an open question for the
 *  owner rather than something this comment settles: because nothing rolls
 *  the notebook back on a regenerate, an extraction in flight over a reply
 *  you then threw away still lands, still gets written, and - with automatic
 *  acceptance on by default - still goes into the prompt. The panel used to
 *  tell you that case was thrown away. It is not. */
// This table must stay IN this file: a backend test (test_notebook_worker.py)
// reads WorkerPanel.tsx to prove every declared skip reason has a sentence
// here, and moving it to a sibling module would blind that gate.
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

/** The three states, said plainly. "closed" is engineering vocabulary for
 *  "working", and a panel that prints it is asking the reader to learn the
 *  implementation before they can read their own status. */
const STATE_PROSE: Record<string, string> = {
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

export function WorkerPanel() {
  const chatId = useUiStore((s) => s.selectedChatId);
  const status = useWorkerStatus();
  const reset = useResetWorker();
  const sweep = useSweepChat();
  const [swept, setSwept] = useState<string | null>(null);
  const auto = useAutoAccept(chatId);
  const setAuto = useSetAutoAccept();
  const setChatAuto = useSetChatAutoAccept();
  const pushError = useErrorStore((s) => s.pushError);

  const body = status.data;
  const state = body?.worker.state ?? "closed";
  const skips = body?.stats.skip_reasons ?? {};

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
          : out.reason === "nothing_unread"
            ? "Nothing here is unread - it has all been looked at."
            : "It is already reading. Give it a moment.",
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

  async function unstick() {
    try {
      await reset.mutateAsync();
    } catch (err) {
      pushError(err, "error");
    }
  }

  return (
    <SlideIn>
      <section className="space-y-3 p-4" aria-label="Background notes">
        <h4
          className="text-xs font-semibold"
          style={{ color: "var(--color-es-text-light)" }}
        >
          Background notes
        </h4>

        <label className="flex items-center gap-2">
          <Switch
            checked={auto.data?.enabled ?? true}
            // Not operable until the stored value is known: a switch that
            // shows a guess and accepts a click is the "you believe it is in
            // force and it is not" failure this feature warns about.
            disabled={setAuto.isPending || !auto.isSuccess}
            onCheckedChange={(v) => void toggle(v)}
          />
          {/* No aria-label. The wrapping <label> already names this control
              with the words on screen; an aria-label OVERRIDES those, so
              voice control could not address the switch by what it says -
              and here it also duplicated the name, which is worse. */}
          <span className="text-xs leading-relaxed text-muted-foreground">
            Keep suggestions without asking
          </span>
        </label>

        <p className="text-xs leading-relaxed text-muted-foreground">
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
        {chatId != null && auto.isSuccess && (
          <div className="space-y-1" data-testid="chat-auto-accept">
            <p className="text-xs leading-relaxed text-muted-foreground">
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
              className="persona-ghost-action h-7 px-2 text-xs"
            >
              {auto.data.overridden
                ? "Follow the setting above"
                : ((auto.data.effective ?? auto.data.enabled)
                    ? "Hold this chat's suggestions for review"
                    : "Keep this chat's suggestions without asking")}
            </Button>
          </div>
        )}

        {body && (
          <div className="persona-card space-y-1" data-testid="worker-status">
            <p className="text-xs leading-relaxed text-muted-foreground">
              {STATE_PROSE[state] ?? state}
            </p>
            <p className="text-xs leading-relaxed text-muted-foreground">
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
            {body.spend.cost_unknown > 0 && (
              <p
                className="text-xs leading-relaxed text-muted-foreground"
                data-testid="worker-cost-unknown"
              >
                {body.spend.cost_unknown} of the calls made today came back with
                no price - the credit figure above leaves them out rather than
                counting them as free.
              </p>
            )}

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
            {body.stats.failed - body.stats.paid_and_lost > 0 && (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {body.stats.failed - body.stats.paid_and_lost} runs failed.
                Nothing was lost - the messages they could not read stay
                unread, not skipped.
              </p>
            )}

            {body.stats.abandoned > 0 && (
              <p
                className="text-xs leading-relaxed text-muted-foreground"
                data-testid="worker-abandoned"
              >
                {body.stats.abandoned} runs were cut off after the request had
                already been sent, so they were paid for. Elysium does not
                send those stretches again, which is why the notes for them
                are missing rather than duplicated.
              </p>
            )}

            {Object.entries(skips).map(([why, n]) => (
              <p
                key={why}
                className="text-xs leading-relaxed text-muted-foreground"
              >
                {n} skipped: {skipProse(why)}.
              </p>
            ))}

            {/* The worker died. Everything else on this card describes a
                worker that is running; without this line the card describes
                one that is not, in the same words. */}
            {!body.worker.alive && (
              <p className="persona-local-error" role="alert">
                The background reader has stopped
                {body.worker.died ? ` (${body.worker.died})` : ""}. Restart
                Elysium to bring it back. Nothing was lost - unread messages
                stay unread.
              </p>
            )}

            {body.worker.unhandled > 0 && (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {body.worker.unhandled} runs ended in an unexpected error
                {body.worker.last_error ? ` (${body.worker.last_error})` : ""}.
                Those messages stay unread.
              </p>
            )}

            {body.worker.refused_by_breaker > 0 && (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {body.worker.refused_by_breaker} turns were passed over while
                it was cooling down. Their messages are still unread.
              </p>
            )}

            {body.worker.dropped_offers > 0 && (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {body.worker.dropped_offers} turns went unqueued while it was
                behind. Their messages are still unread, so a later run picks
                them up.
              </p>
            )}
          </div>
        )}

        {/* THE WAY BACK.
            The worker only ever moves forward: its cursor is a maximum, and
            the first read of a chat that already had a long history starts
            at the PRESENT on purpose - a notebook describing a conversation
            four hundred messages ago is worse than an empty one. That was a
            decision with no undo, and this is the undo. One call per press,
            against the same daily limit as an ordinary turn.

            Only with a chat open, because there is nothing to read
            otherwise. */}
        {chatId != null && (
          <div className="space-y-1">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={sweep.isPending || (body?.worker.sweeping ?? false)}
              onClick={() => void readTheRest()}
              data-testid="worker-sweep"
              className="persona-ghost-action h-7 gap-1.5 px-2 text-xs"
            >
              <BookOpen size={12} className="size-3" />
              Read the earlier messages here
            </Button>
            {(body?.worker.backlog.messages ?? 0) > 0 && (
              <p
                className="text-xs leading-relaxed text-muted-foreground"
                data-testid="worker-backlog"
              >
                {body!.worker.backlog.chats} chat
                {body!.worker.backlog.chats === 1 ? "" : "s"} have{" "}
                {body!.worker.backlog.messages} unread messages. Nothing is
                read without you asking - reading them costs calls.
              </p>
            )}

            {swept && (
              <p
                className="text-xs leading-relaxed text-muted-foreground"
                role="status"
                data-testid="worker-sweep-said"
              >
                {swept}
              </p>
            )}
          </div>
        )}

        {/* Only when it means something. A reset button that is always there
            invites pressing it at random; one that appears when the thing is
            actually stuck says what it is for. */}
        {(state === "stopped" || state === "open") && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={reset.isPending}
            onClick={() => void unstick()}
            className="persona-ghost-action h-7 gap-1.5 px-2 text-xs"
          >
            <RotateCcw size={12} className="size-3" />
            Try again now
          </Button>
        )}
      </section>
    </SlideIn>
  );
}
