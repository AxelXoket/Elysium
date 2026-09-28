/**
 * BackfillStrip - reading a chat's earlier messages on request, with the
 * price said before the button acts.
 *
 * The worker only ever moves forward, and the first read of a chat that
 * already had a long history starts at the PRESENT on purpose - so the
 * history under that point is unreachable by any automatic route. This is
 * the way back, and there is exactly one of it: one job at a time, through
 * the worker's own door, against the same daily counter as an ordinary turn
 * but with a lower ceiling so the live reader keeps a reserve.
 *
 * Three things are shown in this order because that is the order a person
 * needs them: what is unread (the backlog line, and what reads it without
 * being asked), what a read would cost and whether today's calls allow it (the
 * preview, from a route that spends nothing), and only then the button.
 * While a job runs the strip shows its progress and a Stop; when it is
 * finished it says how it ended and offers "undo this run", which removes
 * exactly the notes that run wrote and leaves alone the ones the reader
 * has since pinned or edited.
 *
 * Vocabulary: `persona-card`, `persona-ghost-action`, `persona-field`,
 * lucide at 12px, `Collapse` for blocks that change the height and `FadeIn`
 * for controls inside a row - the same language as the rest of the panel.
 * A refusal (`started: false`) is an ordinary answer and is rendered as a
 * sentence from WorkerPanel's SWEEP_PROSE, never as a toast and never as
 * the raw token.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { BookOpen, RotateCcw, Square, Trash2, Undo2 } from "lucide-react";
import { PendingButton } from "@/components/notebook/PendingButton";
import { focusHandoff } from "@/components/notebook/focusHandoff";
import { useHeldDiscards } from "@/lib/chat/heldDiscards";

import { settledAt, useRetryPress } from "@/components/notebook/useRetryPress";
import { SETTING_RETRY } from "@/components/notebook/noteProse";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Collapse } from "@/components/motion/Collapse";
import { CrossFade } from "@/components/motion/CrossFade";
import { FadeIn } from "@/components/motion/FadeIn";
import { SWEEP_PROSE, sweepProse } from "@/components/notebook/WorkerPanel";
import {
  PREVIEW_FAILED,
  PREVIEW_FAILED_TAIL,
  UNDO_RUN_ASK,
  UNDO_RUN_CONFIRM,
  RANGE_WORDS,
  SCOPE_LABELS,
  SCOPE_ORDER,
  blockedProse,
  capProse,
  noSuchMessage,
  outcomeProse,
  outcomeToken,
  pickWaitingProse,
  planProse,
  rangeSummary,
  stopProse,
  undoProse,
} from "@/components/notebook/backfillProse";
import { useReducedMotion } from "@/components/motion/ReducedMotion";
import { Disclosure } from "@/components/notebook/Disclosure";
import { DISCLOSURE_WORDS } from "@/components/notebook/limitsProse";
import { backlogHowProse, backlogProse } from "@/components/notebook/workerProse";
import { REASON_WHY } from "@/components/notebook/reasonProse";
import { durationSeconds } from "@/lib/theme/tokens";
import { useMessages } from "@/lib/query/chats";
import {
  EMPTY_RANGE,
  chatPositions,
  excerpt,
  positionOf,
  serverRange,
  useNoteRangeStore,
  type RangeEnd,
} from "@/lib/chat/noteRange";
import { getErrorMessage, isKnownErrorCode } from "@/lib/errors/errorMessages";
import { useErrorStore } from "@/lib/errors/errorStore";
import { isApiError } from "@/lib/api/client";
import type { BackfillRange, BackfillScope } from "@/lib/api/notebook";
import type { BackfillUndo } from "@/lib/schemas/notebook";
import {
  sendHeldDiscards,
  useBackfillPlan,
  useRunBackfill,
  useStopBackfill,
  useUndoBackfill,
  useWorkerStatus,
} from "@/lib/query/notebook";

/** A typed place in the chat, 1 to `total`, or null. Digits only: the field
 *  is a number field, but a browser still hands over "" mid-edit. */
function parsePlace(raw: string, total: number): number | null {
  if (!/^\d+$/.test(raw.trim())) return null;
  const n = Number.parseInt(raw, 10);
  return n >= 1 && n <= total ? n : null;
}

/** The four sentences the Start button can point at with
 *  `aria-describedby`. A disabled control on this panel is never silent, so
 *  every reason it is off has a sentence on screen AND a name the button
 *  can carry to a reader who never sees the sentence. */
const WHY_BLOCKED = "backfill-why-blocked";
const WHY_CAP = "backfill-why-cap";
const WHY_BUSY = "backfill-why-busy";
const WHY_RANGE = "backfill-why-range";
/** The answer to a started run, until that run has finished. */
const READING_NOW = "Reading this chat now.";
/** The undo question the Remove button points at. */
const UNDO_ASK_ID = "backfill-undo-ask";

export function BackfillStrip({ chatId }: { chatId: number }) {
  const pushError = useErrorStore((s) => s.pushError);
  // The same query WorkerPanel polls: one key, one request, two readers.
  const status = useWorkerStatus(chatId);
  const [open, setOpen] = useState(false);
  // THE MARKS AND THE STRIP AGREE. A chat holding a whole pick (both ends,
  // drawn as marks on two messages) opens the strip on that range, after a
  // tab switch or a trip to another chat too; choosing another scope lets
  // go of the pick, marks and all. Start never reads "all" while the chat
  // shows a range.
  const [scope, setScope] = useState<BackfillScope>(() => {
    const r = useNoteRangeStore.getState().byChat[chatId];
    return r?.from != null && r?.to != null ? "range" : "all";
  });
  // THE RANGE lives in the chat-keyed store the message buttons write to
  // (lib/chat/noteRange.ts), as message anchors. What a field SHOWS is the
  // message's place in this chat, derived from the chat's own list - the
  // same list, under the same query key, the chat is drawn from, so it is
  // normally already here. `typed` holds only what a reader typed that is
  // not (yet) a place in this chat: an empty field mid-edit, a "15" in a
  // twelve-message chat. A valid number goes straight to the store.
  const picked = useNoteRangeStore((s) => s.byChat[chatId]) ?? EMPTY_RANGE;
  const setEnd = useNoteRangeStore((s) => s.setEnd);
  const clearPick = useNoteRangeStore((s) => s.clear);
  const [typed, setTyped] = useState<Partial<Record<RangeEnd, string>>>({});
  const messages = useMessages(chatId);
  const positions = useMemo(
    () => chatPositions(messages.data ?? []), [messages.data]);
  const total = positions.length;
  const reduced = useReducedMotion();
  const [said, setSaid] = useState<string | null>(null);
  const [saidJob, setSaidJob] = useState<number | null>(null);
  const [handoff] = useState(focusHandoff);
  const queryClient = useQueryClient();
  const outcomeRef = useRef<HTMLParagraphElement>(null);
  // The messages' Try again stays while its own retry is out (the query
  // reads `pending` meanwhile, see `useRetryPress`), and once they load the
  // First message field, which it was standing in for, takes the keyboard.
  const messagesRetry = useRetryPress(settledAt(messages), messages.isError);
  const rangeFieldsRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (messagesRetry.worked === 0) return;
    rangeFieldsRef.current?.querySelector<HTMLInputElement>("input")?.focus();
  }, [messagesRetry.worked]);
  // BOTH ARE ABOUT A JOB, so both carry the job they are about. The poll
  // can replace the block under the reader - a second read of the same
  // chat arrives as a new `job_id` without anything remounting - and a
  // "removed 14 notes" line or an armed confirm left over from the job
  // before it would then describe the wrong run. Derived rather than
  // reset in an effect: there is no moment where the stale one renders.
  const [undone, setUndone] = useState<
    { jobId: number; result: BackfillUndo } | null>(null);
  const [confirmFor, setConfirmFor] = useState<number | null>(null);

  // WHERE THE KEYBOARD GOES when the preview opens and when it closes
  // (the same fault as ledger Q-114). Pressing "Read the
  // earlier messages" unmounts that button, and a closed preview is
  // `inert`, so the focused element left the page and focus fell to the
  // body: a screen reader user lost their place. Opening moves focus to
  // the preview's first control, the scope field. Closing with focus still
  // inside the preview hands it back to the button that opened it, the way
  // NotebookPanel's delete confirm returns it to its trigger: that covers
  // Cancel and another chat opening under a reader who was in the preview.
  // A reader whose focus is elsewhere (the chat list, the composer) is left
  // there, because taking it would be this fault the other way round.
  //
  // Checked in a LAYOUT effect, in the same task as the commit that made
  // the preview inert, so the check runs before the browser drops focus out
  // of the inert subtree and `document.activeElement` reads the body. In
  // Edge a passive effect also got there first (measured, both motion
  // settings), but only because React flushed it early; this does not
  // depend on that.
  // `cancelled` keeps Cancel's promise for a press that did not focus it (a
  // pointer press on an engine that does not focus buttons).
  //
  // The scope field is focused without the browser's own scroll.
  // That scroll centred the field, and under reduced motion (the preview is
  // full size at once) it jumped the panel 145 px under the reader. Nothing
  // needs scrolling here: the field opens in the place of the button that
  // was just pressed, and that button was on screen to be pressed.
  const readButtonRef = useRef<HTMLButtonElement>(null);
  const scopeFieldRef = useRef<HTMLButtonElement>(null);
  const previewRef = useRef<HTMLDivElement>(null);
  const cancelled = useRef(false);
  // "Start reading" pressed from the keyboard (Q-114 again).
  // While the request is out the button stays focusable (`aria-disabled`,
  // not `disabled`, below), and once the answer is in, focus moves to the
  // line that says it when it would otherwise be lost: when it was still on
  // the button (a refusal can refetch a preview that disables it), or when
  // the run started and focus was anywhere in the preview, which closes
  // and goes inert. A reader who moved on to a place that survives the
  // answer (outside the preview, or Cancel under a refusal) is left there.
  //
  // This effect is declared BEFORE the open/close one on purpose: layout
  // effects run in order, so a started run's focus is on the answer line
  // by the time the close check runs, and the close check then finds
  // nothing inside the preview to hand back.
  const startButtonRef = useRef<HTMLButtonElement>(null);
  const saidRef = useRef<HTMLParagraphElement>(null);
  const [answerFocus, setAnswerFocus] = useState(0);
  useLayoutEffect(() => {
    if (answerFocus > 0) saidRef.current?.focus({ preventScroll: true });
  }, [answerFocus]);
  // A preview opened by a pick in the CHAT leaves focus where it is: the
  // reader is on a message's button, and the next thing they are likely to
  // press is the same button on the other end of the range.
  const [openedBy, setOpenedBy] = useState<"button" | "pick">("button");
  useLayoutEffect(() => {
    if (open) {
      if (openedBy === "button") {
        scopeFieldRef.current?.focus({ preventScroll: true });
      }
      return;
    }
    const inside = previewRef.current?.contains(document.activeElement) ?? false;
    if (cancelled.current || inside) {
      // The button was mounted again by the render this effect follows,
      // so the ref points at a live element by the time this runs.
      readButtonRef.current?.focus();
    }
    cancelled.current = false;
  }, [open, openedBy]);

  // EVERYTHING ELSE IS ABOUT A CHAT, so it goes when the chat does. This
  // component is rendered without a `key`, so nothing remounts when the
  // reader opens another conversation: without this, chat A's answer sits
  // under chat B's name, and - the one that spends money - chat A's two
  // message ids stay in the range fields and are submitted against chat B,
  // where they are either a different stretch or not there at all.
  //
  // Adjusted during render rather than in an effect, the same way
  // NotebookPanel clears its filter and its armed undo: an effect runs
  // after paint, so the reader gets one frame of the new chat carrying the
  // old chat's sentence and the old chat's numbers.
  //
  // `undone` and `confirmFor` are not in here because they do not need to
  // be: both are derived against the current job id a few lines down, so a
  // leftover one has nothing to attach to and never renders.
  const [stripChatId, setStripChatId] = useState(chatId);
  if (chatId !== stripChatId) {
    setStripChatId(chatId);
    setSaid(null);
    setOpen(false);
    setScope(picked.from != null && picked.to != null ? "range" : "all");
    setTyped({});
  }
  // THE RANGE A CHAT LEFT BEHIND STAYS WITH THAT CHAT. The store is keyed
  // by chat, so nothing of chat A's can show in chat B's strip; coming back
  // to A finds its half-picked range where it was (marks and all), and the
  // next press of the book finishes it.

  // A PICK IN THE CHAT opens the preview on the range scope and brings the
  // strip into view: a choice made where the reader cannot see its result
  // would be a silent one. Adjusted during render, keyed on the pick's
  // number, so the frame that shows the new range also shows it open. A
  // pick the strip already opened for (`acked`, kept in the store because
  // this component unmounts with its tab) does not open it again.
  const ack = useNoteRangeStore((s) => s.ack);
  const [seenPick, setSeenPick] = useState(0);
  const [scrollFor, setScrollFor] = useState(0);
  useEffect(() => {
    if (picked.seq !== 0 && picked.seq !== picked.acked) ack(chatId, picked.seq);
  }, [picked.seq, picked.acked, chatId, ack]);
  if (picked.seq !== 0 && picked.seq !== seenPick && picked.seq !== picked.acked) {
    setSeenPick(picked.seq);
    setScrollFor(picked.seq);
    setTyped({});
    setSaid(null);
    setScope("range");
    if (!open) {
      setOpenedBy("pick");
      setOpen(true);
    }
  }
  const stripRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (scrollFor === 0) return;
    const el = stripRef.current;
    // jsdom has no scrollIntoView; a browser always does.
    if (!el || typeof el.scrollIntoView !== "function") return;
    const bring = () => el.scrollIntoView({
      block: "nearest", behavior: reduced ? "auto" : "smooth" });
    bring();
    // Once more when the preview has finished growing, so its bottom
    // (the Start button) is in view too, not only the top it had at first.
    const later = window.setTimeout(bring, reduced ? 0 : durationSeconds.normal * 1000);
    return () => window.clearTimeout(later);
  }, [scrollFor, reduced]);

  // Where each end sits in THIS chat, 1 to N, or null.
  const fromAt = positionOf(positions, picked.from);
  const toAt = positionOf(positions, picked.to);
  const range: BackfillRange | null =
    fromAt != null && toAt != null ? serverRange(positions, fromAt, toAt) : null;

  /** A number typed into one of the two fields. */
  function typePlace(end: RangeEnd, raw: string) {
    const place = parsePlace(raw, total);
    if (place == null) {
      setTyped((t) => ({ ...t, [end]: raw }));
      setEnd(chatId, end, null);
      return;
    }
    setTyped((t) => {
      const rest = { ...t };
      delete rest[end];
      return rest;
    });
    setEnd(chatId, end, positions[place - 1].anchor);
    // A first message alone already names a range, the same way a pick
    // does: from there to the end of the chat.
    if (end === "from" && picked.to == null && typed.to == null && total > 0) {
      setEnd(chatId, "to", positions[total - 1].anchor);
    }
  }
  /** What a field shows: what was typed, else the stored end's place. */
  const shown = (end: RangeEnd, at: number | null) =>
    typed[end] ?? (at != null ? String(at) : "");
  // WHY THE RANGE IS NOT READY, in the order a reader fills it in. Only
  // ever asked while the range scope is chosen.
  const rangeWhy = (end: RangeEnd, at: number | null): string | null => {
    const raw = typed[end];
    if (raw != null && raw.trim() !== "") return noSuchMessage(total);
    if (at != null) return null;
    // One end set: say which end is still missing, not "pick messages".
    const other = end === "from" ? toAt : fromAt;
    if (other == null) return RANGE_WORDS.pick;
    return end === "from" ? RANGE_WORDS.pickFirst : RANGE_WORDS.pickLast;
  };
  // Nothing is said about a number before the chat's messages are known:
  // with none loaded, every number read as "not in this chat" and the line
  // said "Type a number from 1 to 0."
  const rangeProblem = scope !== "range" ? null
    : messages.isError || messagesRetry.out ? RANGE_WORDS.unread
    : !messages.isSuccess ? RANGE_WORDS.loading
    : total === 0 ? RANGE_WORDS.noMessages
    : rangeWhy("from", fromAt) ?? rangeWhy("to", toAt)
      ?? (fromAt != null && toAt != null && fromAt > toAt ? RANGE_WORDS.order : null);

  const body = status.data;
  // Only THIS chat's job. The block is the chat's newest when a chat was
  // asked about, so a job on another chat cannot show up here.
  const job = body?.worker.backfill?.chat_id === chatId
    ? body.worker.backfill : null;
  const running = job != null && (job.state === "running" || job.state === "planned");
  const backlog = body?.worker.backlog;
  // The worker's own threshold, off the same poll. Zero only when the answer
  // predates the field (the schema's default), and then the line says it
  // without a number rather than printing "every 0".
  const threshold = body?.worker.trigger_threshold ?? 0;
  const undoneHere = job != null && undone?.jobId === job.job_id
    ? undone.result : null;
  const confirmUndo = job != null && confirmFor === job.job_id;
  // "Reading this chat now." belongs to the job it started. Once that job
  // has finished, the outcome line above says how it ended, and the two
  // side by side read as "reading" and "finished" at once.
  const saidShown = said === READING_NOW && saidJob != null
    && job?.job_id === saidJob && !running ? null : said;

  // Asked for only once the strip is open: the preview is what the reader
  // sees before pressing Start, and nothing is fetched for a strip nobody
  // has opened.
  const plan = useBackfillPlan(chatId, scope, range, open && !running);
  const run = useRunBackfill();
  const stop = useStopBackfill();
  const undo = useUndoBackfill();

  const preview = plan.data;
  // PROCESS-WIDE, not this chat's. One job at a time is the rule for the
  // whole worker, so a job someone started on another chat holds this
  // button down while this chat's own `running` is false. Only a job sets
  // it: the automatic whole-chat read that used to hold it too is gone
  // (decisions 30 a and 31 a).
  const sweeping = body?.worker.sweeping ?? false;
  const nothingFitsToday = preview != null && !preview.fits_today
    && preview.calls_spent_today >= preview.backfill_cap;
  // `startable` is what the preview allows; `canStart` also waits out a
  // request already sent. Only the first disables the button: a focused
  // button that turns `disabled` for the length of the request drops the
  // keyboard to the body, so the second is `aria-disabled` and
  // `start` refuses a second press itself.
  const startable = rangeProblem == null
    && preview != null && preview.ready && !nothingFitsToday && !sweeping;
  const canStart = startable && !run.isPending;
  // WHICH sentence is the reason, as the id the button carries. A disabled
  // button with nothing next to it is this panel refusing without saying
  // so, and `sweeping` was the silent one: a full, affordable preview and
  // a dead control, with no line anywhere explaining it. First reason wins,
  // in the order they are read down the block. `run.isPending` is not one
  // of them - it lasts one request and says itself by moving.
  // An unfinished range comes first: with no range there is no preview at
  // all, and "Start reading" was off with no word anywhere saying why.
  const whyOff = rangeProblem != null ? WHY_RANGE
    : startable || preview == null ? undefined
    : preview.blocked_by.length > 0 ? WHY_BLOCKED
    : nothingFitsToday ? WHY_CAP
    : sweeping ? WHY_BUSY
    : undefined;

  async function start() {
    if (!canStart) return;
    setSaid(null);
    setUndone(null);
    setConfirmFor(null);
    try {
      const out = await run.mutateAsync([chatId, scope, range]);
      const focused = document.activeElement;
      const onStart = focused === startButtonRef.current;
      const inPreview = previewRef.current?.contains(focused) ?? false;
      if (onStart || (out.started && inPreview)) {
        setAnswerFocus((n) => n + 1);
      }
      setSaid(out.started ? READING_NOW : sweepProse(out.reason));
      setSaidJob(out.started ? out.job_id ?? null : null);
      if (out.started) {
        setOpen(false);
        // The range is spent: its marks leave the two messages.
        clearPick(chatId);
      }
    } catch (err) {
      pushError(err, "error", { chatId });
    }
  }

  async function halt() {
    setSaid(null);
    try {
      const out = await stop.mutateAsync([chatId]);
      // `stopProse`, not `sweepProse`: the start route's fallback reads
      // "It did not start", and answering a press of "Stop reading" with
      // that is the panel saying the opposite of what just happened.
      setSaid(out.stopped
        ? "Stopping after the call that is out now."
        : stopProse(out.reason));
    } catch (err) {
      pushError(err, "error", { chatId });
    }
  }

  async function takeBack() {
    if (job == null || undo.isPending) return;
    const jobId = job.job_id;
    try {
      // Suggestions the reader discarded are gone for them already: their
      // held deletes go first, so the answer does not count them as
      // removed by this undo.
      await sendHeldDiscards(queryClient, useHeldDiscards.getState().take(chatId), chatId);
      const result = await undo.mutateAsync([jobId, chatId]);
      // The answer line replaces the confirm and takes the keyboard: the
      // button that was pressed is gone.
      handoff.want("undone", outcomeRef.current);
      setConfirmFor(null);
      setUndone({ jobId, result });
    } catch (err) {
      pushError(err, "error", { chatId });
    }
  }

  /** Back out of the undo question: the trigger comes back and takes the
   *  keyboard, from a click or from Escape. */
  function cancelUndo() {
    handoff.want("undo-trigger", outcomeRef.current);
    setConfirmFor(null);
  }

  return (
    <div ref={stripRef} className="persona-card space-y-2" data-testid="backfill-strip">
      {/* WHAT IS UNREAD, and who reads it. The line the worker card used
          to carry, moved here with its meaning intact (KARAR-J) and its
          one false half corrected: it is the offer this whole strip
          answers, so it belongs beside the button. Rendered only when
          there is something to say: a line that is always there is not an
          offer.

          WHAT READS WITHOUT BEING ASKED IS SAID, because it spends money
          (3.31). This line used to say nothing is read unless a reader
          asks, "except a chat that has no notes yet: that one is read once
          on its own, the first time it comes up". Both halves were wrong:
          the background reader reads every chat by itself at the
          threshold, and the automatic whole-chat read is gone (decisions
          30 a and 31 a). What is left is the ordinary reader, which starts
          a new chat from its first message only while it is short, and
          this strip, which is the only way the rest of a history is read.
          The number is the worker's own threshold off the same poll, not
          a figure typed here. */}
      <Collapse open={(backlog?.messages ?? 0) > 0} className="min-w-0">
        {backlog && (
          // ONE LINE AND A DOOR. The count and the fact that
          // reading costs calls stay on screen (3.31: spend that happens
          // without asking is said); what reads without being asked, and
          // when, is one press away.
          <div className="space-y-1" data-testid="worker-backlog">
            <p className="text-label leading-relaxed text-muted-foreground">
              {backlogProse(backlog.chats, backlog.messages)}
            </p>
            <Disclosure label={DISCLOSURE_WORDS.whenRead} testId="worker-backlog-details">
              <p className="text-label leading-relaxed text-muted-foreground">
                {backlogHowProse(threshold).join(" ")}
              </p>
            </Disclosure>
          </div>
        )}
      </Collapse>

      {/* IN FLIGHT: progress from the poll, and the one control that
          makes sense. NOT a live region: the poll rewrites this whole
          sentence every twenty seconds, so an `aria-live` here reads
          "Reading this chat: 8 of 21 calls..." over the reader three times
          a minute for as long as the job lasts. The panel's live regions
          are for things that were ANSWERED - what a press just did - and a
          counter ticking on its own is not one of them.

          The children are not gated on `running` as well as on `job`:
          Collapse animates its height with the content mounted, and a
          child that disappears in the same render animates an empty box
          shut. Only the null narrowing is left. */}
      <Collapse open={running} className="min-w-0">
        {job && (
          <div className="space-y-1" data-testid="backfill-progress">
            <p className="text-label leading-relaxed text-muted-foreground">
              Reading this chat: {job.calls_spent} of {job.calls_planned}{" "}
              call{job.calls_planned === 1 ? "" : "s"},{" "}
              {job.notes_written} note{job.notes_written === 1 ? "" : "s"} written,{" "}
              {job.messages_left} message{job.messages_left === 1 ? "" : "s"} left.
            </p>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={stop.isPending}
              onClick={() => void halt()}
              className="persona-ghost-action h-7 gap-1.5 px-2 text-ui"
            >
              <Square size={12} className="size-3" />
              Stop reading
            </Button>
          </div>
        )}
      </Collapse>

      {/* FINISHED: how it ended, and the way to take it back. Undo is the
          house confirm pattern - the trigger is replaced in place - because
          it deletes notes, and every other delete on this panel asks. */}
      <Collapse open={job != null && !running} className="min-w-0">
        {job && (
          <div className="space-y-1" data-testid="backfill-outcome">
            {/* Also the undo's focus holder (tabIndex -1): it keeps the
                keyboard while the undo's buttons cross-fade. */}
            <p
              ref={outcomeRef}
              tabIndex={-1}
              title={outcomeToken(job)}
              className="text-label leading-relaxed text-muted-foreground outline-none"
            >
              {outcomeProse(job)}
            </p>
            {/* THE SWAP, and it is a swap in both directions: the trigger
                is REPLACED by its confirmation in place, and the whole pair
                is replaced by the "removed 14 notes" line once the undo
                answers. A replacement is what `CrossFade` is for, the same
                primitive the just-saved strip four hundred lines down uses
                for the identical gesture. The Collapse around it is the
                other half: a job that wrote nothing has no undo to offer,
                and that is a show/hide rather than a swap. */}
            <Collapse open={undoneHere != null || job.notes_written > 0} className="min-w-0">
              <CrossFade
                pane={undoneHere ? "undone" : confirmUndo ? "confirm" : "undo"}
              >
                {undoneHere ? (
                  // A live region, and it takes focus: the Remove button the
                  // keyboard was on is gone, and a reader who pressed it
                  // hears what happened.
                  <p
                    ref={handoff.take("undone")}
                    tabIndex={-1}
                    role="status"
                    className="text-label leading-relaxed text-muted-foreground"
                    data-testid="backfill-undone"
                  >
                    {undoProse(undoneHere)}
                  </p>
                ) : (
                  <div
                    className="flex flex-wrap items-center gap-2"
                    onKeyDown={(e) => {
                      if (!confirmUndo || e.key !== "Escape") return;
                      // Stopped here: Composer binds Escape at the window.
                      e.stopPropagation();
                      e.preventDefault();
                      cancelUndo();
                    }}
                  >
                    {confirmUndo ? (
                      <>
                        {/* WHAT WILL HAPPEN, said before it happens. The
                            server keeps every note the reader kept, pinned
                            or edited (`delete_job_notes`), so "Remove all
                            N" promised more than it did. The button's words
                            are its name, and they say the same thing. */}
                        <p
                          id={UNDO_ASK_ID}
                          className="w-full text-label leading-relaxed text-muted-foreground"
                          data-testid="backfill-undo-ask"
                        >
                          {UNDO_RUN_ASK}
                        </p>
                        <PendingButton
                          type="button"
                          size="sm"
                          variant="ghost"
                          pending={undo.isPending}
                          onClick={() => void takeBack()}
                          aria-describedby={UNDO_ASK_ID}
                          className="persona-danger-action h-7 gap-1.5 px-2 text-ui"
                        >
                          <Trash2 size={12} className="size-3" />
                          {UNDO_RUN_CONFIRM}
                        </PendingButton>
                        {/* The safe choice takes the keyboard when the
                            question opens, as the note row's delete does. */}
                        <PendingButton
                          ref={handoff.take("undo-cancel")}
                          type="button"
                          size="sm"
                          variant="ghost"
                          pending={undo.isPending}
                          onClick={cancelUndo}
                          className="persona-ghost-action h-7 px-2 text-ui"
                        >
                          Cancel
                        </PendingButton>
                      </>
                    ) : (
                      <PendingButton
                        ref={handoff.take("undo-trigger")}
                        type="button"
                        size="sm"
                        variant="ghost"
                        pending={undo.isPending}
                        onClick={() => {
                          handoff.want("undo-cancel", outcomeRef.current);
                          setConfirmFor(job.job_id);
                        }}
                        className="persona-danger-action h-7 gap-1.5 px-2 text-ui"
                      >
                        <Undo2 size={12} className="size-3" />
                        Undo this run
                      </PendingButton>
                    )}
                  </div>
                )}
              </CrossFade>
            </Collapse>
          </div>
        )}
      </Collapse>

      {/* THE BUTTON, behind the preview. Pressing "Read" opens the preview
          and spends nothing; "Start reading" is what spends, and it is
          disabled until the preview has arrived and says it can. */}
      {/* A PICK THAT WAITS IS SAID. A reader who comes back to a chat with
          a whole range picked meets the strip closed (the pick was already
          answered), and the marks in the chat were the only sign of it. */}
      <Collapse open={!running && !open && fromAt != null && toAt != null} className="min-w-0">
        <p
          className="text-label leading-relaxed text-muted-foreground"
          data-testid="backfill-pick-waiting"
        >
          {fromAt != null && toAt != null ? pickWaitingProse(fromAt, toAt) : ""}
        </p>
      </Collapse>
      {!running && !open && (
        <FadeIn>
          <Button
            ref={readButtonRef}
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => { setSaid(null); setOpenedBy("button"); setOpen(true); }}
            className="persona-ghost-action h-7 gap-1.5 px-2 text-ui"
          >
            <BookOpen size={12} className="size-3" />
            Read the earlier messages
          </Button>
        </FadeIn>
      )}
      {/* No `{open && ...}` around the block: Collapse keeps its content
          mounted on purpose, and the two range fields are exactly the form
          state that mounting buys. Unmounting them in the render that
          closes the box also animates an empty box shut.

          `min-w-0` lands on the Collapse's inner, the grid item, so the
          scope field's nowrap words cannot set the width of the row they
          sit in; see the scope field below. */}
      <Collapse open={!running && open} className="min-w-0">
        <div ref={previewRef} className="space-y-2" data-testid="backfill-preview">
          <div className="space-y-1">
            <span
              id="backfill-scope-label"
              className="block text-ui leading-relaxed text-muted-foreground"
            >
              What to read
            </span>
            {/* THE HOUSE SELECT, not a native one. KURAL 7.2 says a native
                `select` is not written; its sequencing note gates CONVERTING
                the ones that already exist and says nothing that permits a
                new one. The label is a sibling with `aria-labelledby` rather
                than a wrapping `<label>`, because the trigger is a button and
                a label wrapping a button names it twice.

                NOTHING ABOUT THE PRIMITIVE IS PATCHED HERE. KURAL 7.4's two
                traps (the popup's own duration literal and
                `alignItemWithTrigger` defaulting to true, which binds
                `data-[align-trigger=true]:animate-none` and opens the popup
                with no animation at all) and KURAL 7.5's popup width
                contract are answered in `ui/select.tsx` itself, so the
                trigger takes the default `size` (32px,
                `data-[size=default]:h-8`, the same height as the boundary
                panel's pickers) and the popup takes no className.

                THE ROW WIDTH IS THE CALL SITE'S. `SelectTrigger`'s base
                string is `w-fit`, which sized this field by the value it
                showed: narrower than the two range fields under it (205.7 px
                in a 274 px row, measured at a 350 px panel). `w-full` fills
                the row, as the boundary and extraction pickers do.
                `min-w-0` is the other half of their pair and does nothing
                here: the trigger's parent is a block box, whose minimum is
                already 0 (measured: a 456 px name leaves the row at the
                card's width with the class and without it).

                What keeps a long name inside the panel is two things, both
                measured at the 300 px floor. The preview's Collapse, around
                this block, hands its inner `min-w-0`: the inner is a grid
                item, and a grid item's automatic minimum is its content's
                own width, so the nowrap trigger pushed the row 2.23 px past
                the card. And the words sit in a span that truncates: the
                base string makes the value itself a flex box, and text
                inside a flex box cannot end in an ellipsis. The span carries
                the whole name as its `title`, so a name cut short at the
                floor can still be read in full (KURAL 4.4). */}
            <Select
              // WHAT THE CLOSED FIELD READS AS, and the base primitive's
              // own way of being told. `Select.Value` with no children
              // resolves its text through the ROOT's `items`, never
              // through the mounted options, and with none it serialises
              // the value - so this field showed `all`, then `next`: the
              // wire token on the face of the one control this strip
              // ships, in every state including its default, and that is
              // what a screen reader announced as the combobox's value.
              // Handing it the record is not a work-around of the house
              // component (`ui/select.tsx` is untouched and takes nothing
              // new): it is the prop the library documents for exactly
              // this, and the mapping has to come from the caller. The
              // value below reads the same record through a function, only
              // so its words can sit in a span that truncates.
              items={SCOPE_LABELS}
              value={scope}
              onValueChange={(next) => {
                setScope(next as BackfillScope);
                // Reading something else: the marks in the chat go too.
                if (next !== "range") clearPick(chatId);
              }}
            >
              <SelectTrigger
                ref={scopeFieldRef}
                aria-labelledby="backfill-scope-label"
                className="persona-field is-strong-focus w-full min-w-0 rounded-md px-2 text-ui"
              >
                <SelectValue>
                  {(value: BackfillScope) => (
                    <span className="truncate" title={SCOPE_LABELS[value]}>
                      {SCOPE_LABELS[value]}
                    </span>
                  )}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {SCOPE_ORDER.map((option) => (
                  <SelectItem key={option} value={option}>
                    {SCOPE_LABELS[option]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {/* THE RANGE, as places in this chat. Each end: its name, the
              number (typeable, 1 to N, filled by a pick in the chat), "of
              N", and under it the message's first words, so the reader can
              see WHICH message a number is without counting bubbles. Then
              one line that says the whole range, or why it is not ready -
              that line is what "Start reading" points at while it is off.
              Polite, not a status: it changes only when the reader picks or
              types, and a pick made in the chat is otherwise heard nowhere. */}
          <Collapse open={scope === "range"} className="min-w-0">
            <div ref={rangeFieldsRef} className="space-y-2" data-testid="backfill-range">
              {(["from", "to"] as const).map((end) => {
                const at = end === "from" ? fromAt : toAt;
                const id = `backfill-range-${end}`;
                const row = at != null && typed[end] == null
                  ? positions[at - 1] : null;
                const words = row ? excerpt(row.display.content) : "";
                return (
                  <div key={end} className="min-w-0 space-y-0.5">
                    <div className="flex items-center gap-2">
                      <label
                        htmlFor={id}
                        className="w-24 shrink-0 text-ui text-muted-foreground"
                      >
                        {end === "from" ? RANGE_WORDS.firstLabel : RANGE_WORDS.lastLabel}
                      </label>
                      <Input
                        id={id}
                        autoComplete="off"
                        type="number"
                        inputMode="numeric"
                        min={1}
                        max={total > 0 ? total : undefined}
                        value={shown(end, at)}
                        onChange={(e) => typePlace(end, e.target.value)}
                        aria-describedby={words ? `${id}-words` : undefined}
                        className="persona-field is-strong-focus w-16 text-label"
                      />
                      {messages.isSuccess && (
                        <span className="text-label text-muted-foreground">
                          of {total}
                        </span>
                      )}
                    </div>
                    {words && (
                      // The panel's own ink, not the muted one: these are
                      // the reader's own words from the chat, the thing
                      // they check the number against (8.6:1 on this panel).
                      <p
                        id={`${id}-words`}
                        className="truncate text-label leading-relaxed"
                        style={{ color: "var(--color-es-text-light)" }}
                        title={words}
                        data-testid={`backfill-range-${end}-words`}
                      >
                        {`"${words}"`}
                      </p>
                    )}
                  </div>
                );
              })}
              <p
                id={WHY_RANGE}
                aria-live="polite"
                className="text-label leading-relaxed text-muted-foreground"
                data-testid="backfill-range-said"
              >
                {rangeProblem
                  ?? (fromAt != null && toAt != null
                    ? rangeSummary(fromAt, toAt, total, picked.swapped)
                    : "")}
              </p>
              {(messages.isError || messagesRetry.out) && (
                <PendingButton
                  type="button"
                  size="sm"
                  variant="ghost"
                  pending={messages.isFetching || messagesRetry.out}
                  onClick={() => {
                    messagesRetry.press();
                    void messages.refetch();
                  }}
                  className="persona-ghost-action h-7 gap-1.5 px-2 text-ui"
                >
                  <RotateCcw size={12} className="size-3" />
                  {SETTING_RETRY}
                </PendingButton>
              )}
            </div>
          </Collapse>

          {/* The preview itself. Three states, each said: not yet known,
              could not be asked, and known. "Could not be asked" is not
              "free" and not "ready": the button stays off. */}
          <Collapse open={plan.isPending && plan.fetchStatus !== "idle"} className="min-w-0">
            <p
              className="text-label leading-relaxed text-muted-foreground"
              data-testid="backfill-pending"
            >
              Working out what this would read and cost...
            </p>
          </Collapse>
          {/* WHICH failure, when the server said. A range whose two
              numbers are not in this chat is the one preview failure a
              reader can fix, and `notebook_range_invalid` is in the
              catalogue for exactly that - so the code's own sentence is
              what goes on screen, and the generic line is kept for a
              failure that carried no code (offline, a proxy, a 500).
              Either way the tail is the same: nothing was started.

              The generic line is `PREVIEW_FAILED` rather than a literal
              here, because it has to stay distinguishable from the
              Notebook panel's own "Notes could not be loaded"; the reason
              is written beside the sentence in backfillProse.ts. */}
          <Collapse open={plan.isError} className="min-w-0">
            {/* The refusal class paints the island's danger ink, which clears
                AA on its own tint here with the mist on and off (index.css,
                `.persona-local-error`). */}
            <p
              className="persona-local-error"
              role="alert"
              data-testid="backfill-preview-error"
            >
              {isApiError(plan.error) && isKnownErrorCode(plan.error.detail)
                ? getErrorMessage(plan.error.detail)
                : PREVIEW_FAILED}{" "}
              {PREVIEW_FAILED_TAIL}
            </p>
          </Collapse>
          <Collapse open={preview != null} className="min-w-0">
            {preview && (
              <div className="space-y-1">
                <p
                  className="text-label leading-relaxed text-muted-foreground"
                  data-testid="backfill-plan"
                >
                  {planProse(preview)}
                </p>
                <Collapse open={!preview.fits_today} className="min-w-0">
                  <p
                    id={WHY_CAP}
                    className="text-label leading-relaxed text-muted-foreground"
                    data-testid="backfill-cap"
                  >
                    {capProse(preview)}
                  </p>
                </Collapse>
                <Collapse open={preview.blocked_by.length > 0} className="min-w-0">
                  <p
                    id={WHY_BLOCKED}
                    className="text-label leading-relaxed text-muted-foreground"
                    data-testid="backfill-blocked"
                  >
                    {blockedProse(preview.blocked_by)}
                  </p>
                  {preview.blocked_by.includes("model_not_extractable") && (
                    <Disclosure label={DISCLOSURE_WORDS.whyModel} testId="backfill-blocked-why" className="mt-1">
                      <p className="text-label leading-relaxed text-muted-foreground">
                        {REASON_WHY.modelNotExtractable.join(" ")}
                      </p>
                    </Disclosure>
                  )}
                </Collapse>
              </div>
            )}
          </Collapse>

          {/* THE SILENT REFUSAL, said out loud. `worker.sweeping` is
              process-wide: a job someone started on another chat holds
              this button down while THIS chat's preview is full,
              affordable and ready. Without this line the
              reader gets a dead control and no cause anywhere on screen.
              The sentence is the one the run route would have answered
              with, taken from the table rather than written twice. */}
          <Collapse open={sweeping} className="min-w-0">
            <p
              id={WHY_BUSY}
              className="text-label leading-relaxed text-muted-foreground"
              data-testid="backfill-busy"
            >
              {SWEEP_PROSE.already_running}
            </p>
          </Collapse>

          <div className="flex items-center gap-2">
            <Button
              ref={startButtonRef}
              type="button"
              size="sm"
              variant="ghost"
              disabled={!startable}
              aria-disabled={run.isPending || undefined}
              aria-describedby={whyOff}
              onClick={() => void start()}
              className="persona-ghost-action h-7 gap-1.5 px-2 text-ui aria-disabled:*:opacity-50"
            >
              {/* BUSY DIMS THE CONTENT, NOT THE BUTTON (SC 1.4.11).
                  `opacity` on the button itself faded its focus border with
                  it, to 1.52-1.97:1 on this panel, and focus stays on this
                  button for the whole request. The same 50% the
                  house `disabled:opacity-50` uses, on the children only:
                  the icon and the words dim, the border and fill keep full
                  strength. The words sit in a span because opacity cannot
                  reach a bare text node. */}
              <BookOpen size={12} className="size-3" />
              <span>Start reading</span>
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => {
                cancelled.current = true;
                setOpen(false);
                // And back to the default scope: reopening after Cancel
                // used to show "range" with two empty fields.
                setScope("all");
                // Cancel lets go of the range too, so the two messages stop
                // carrying marks for a read nobody is going to start.
                setTyped({});
                clearPick(chatId);
              }}
              className="persona-ghost-action h-7 px-2 text-ui"
            >
              Cancel
            </Button>
          </div>
        </div>
      </Collapse>

      {/* What the last press answered. A refusal is a sentence here and
          never a toast: "nothing is unread" is the answer most presses
          get, and a toast would make it look like a fault.

          `role="status"` because this node IS the answer. It carries the
          job the paragraph this strip replaced on the worker card carried,
          and that one was a live region: a reader who pressed the button
          and is not watching this corner of the panel otherwise learns
          nothing at all about what their press did. One live region for
          the strip, and this is it. */}
      <Collapse open={!!saidShown} className="min-w-0">
        <p
          ref={saidRef}
          tabIndex={-1}
          className="text-label leading-relaxed text-muted-foreground"
          role="status"
          // A status takes no name from its content: focused, it
          // read as an unnamed status. Its sentence is its name.
          aria-label={saidShown ?? undefined}
          data-testid="backfill-said"
        >
          {saidShown}
        </p>
      </Collapse>
    </div>
  );
}
