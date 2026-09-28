/**
 * notesTabState - what the Notes tab's frame reads: which view is open, the
 * state of this chat's reading, what waits for the reader, and the refresh
 * that keeps the note list honest about background work.
 *
 * Logic only, no components (the component files export components only).
 * The derivations are pure functions so the tests can walk every case; the
 * hooks are thin wires from the queries and stores into them.
 */
import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { create } from "zustand";

import { keys } from "@/lib/query/keys";
import { useProxyHealth, useSettings } from "@/lib/query/settings";
import {
  WORKER_POLL_MS,
  useAutoAccept,
  useChatBoundaries,
  useExtractSettings,
  useGlobalBoundaries,
  useNotebook,
  useSafeword,
  useWorkerStatus,
} from "@/lib/query/notebook";
import { useSeenNotesStore } from "@/lib/chat/seenNotes";
import { useHeldDiscards } from "@/lib/chat/heldDiscards";
import type { NotebookEntry, WorkerStatus } from "@/lib/schemas/notebook";
import {
  CHAT_READ_WORDS,
  NOTES_TARGET,
  type NotReadWhy,
  type NotesTarget,
  type NotesView,
} from "@/components/notebook/notesTabProse";

/** Which view of the Notes tab is open, and a keyboard hand-off waiting to
 *  be served. In memory only, like the tab's other working state: it
 *  outlives a switch to a neighbouring tab (the panels unmount there) and
 *  starts on the Notebook view in every new session. */
interface NotesViewState {
  view: NotesView;
  /** A control to focus (by its `data-notes-target`) once the right
   *  panel's `tab` has drawn, or null. Set by a control that is about to
   *  leave with its view or tab, so the keyboard lands on the thing that
   *  fixes the problem and never on the page. RightPanel serves it. */
  focusTarget: { target: NotesTarget; tab: "notebook" | "secrets" } | null;
  setView: (view: NotesView) => void;
  /** Opens `view` of the Notes tab and asks for `target` there. */
  openAt: (view: NotesView, target: NotesTarget) => void;
  /** Asks for `target` on another tab of the panel (Security). */
  requestFocus: (target: NotesTarget, tab: "notebook" | "secrets") => void;
  clearFocusTarget: () => void;
}

export const useNotesViewStore = create<NotesViewState>((set) => ({
  view: "notes",
  focusTarget: null,
  setView: (view) => set({ view }),
  openAt: (view, target) => set({ view, focusTarget: { target, tab: "notebook" } }),
  requestFocus: (target, tab) => set({ focusTarget: { target, tab } }),
  clearFocusTarget: () => set({ focusTarget: null }),
}));

/** Accepted, written by the model, and never announced: the just-saved
 *  strip's rows. A note the reader already answered is left out: kept by
 *  hand (`hand_accepted_at`), words changed (`edited_at`), or pinned. All
 *  on disk, so this holds after a restart too. */
export function justSavedOf(
  entries: readonly NotebookEntry[],
  seen: readonly number[] | undefined,
): NotebookEntry[] {
  return entries.filter(
    (e) => e.provenance === "model" && e.status === "accepted"
      && !e.retired_at && !e.hand_accepted_at && !e.edited_at && !e.pinned
      && !(seen ?? []).includes(e.id));
}

/** Suggestions still waiting for the reader: proposed, not retired, and not
 *  already discarded (a held discard is the reader's answer). */
export function waitingOf(
  entries: readonly NotebookEntry[],
  discarded: readonly number[],
): NotebookEntry[] {
  return entries.filter(
    (e) => e.status === "proposed" && !e.retired_at && !discarded.includes(e.id));
}

/** Where the fix for an alarm is: a control, and the place it lives. */
export interface ChatReadFix {
  target: NotesTarget;
  /** The Notes tab's Settings view, or the panel's Security tab. */
  where: "settings" | "security";
}

/** What the line at the top of the Notebook view says about this chat. */
export type ChatRead =
  /** Not known yet, or the check itself failed: said, never guessed. */
  | { kind: "unknown"; text: string }
  | { kind: "ok"; text: string }
  | { kind: "reading"; text: string }
  /** Paused, but the wait is over: the next reply is read, by a billed
   *  test call. Not "not read". */
  | { kind: "test"; text: string; fix: ChatReadFix }
  | { kind: "not"; why: NotReadWhy; text: string; fix: ChatReadFix };

const inSettings = (target: NotesTarget): ChatReadFix => ({ target, where: "settings" });
const inSecurity = (target: NotesTarget): ChatReadFix => ({ target, where: "security" });

const notRead = (why: NotReadWhy, fix: ChatReadFix): ChatRead => ({
  kind: "not", why, fix, text: `${CHAT_READ_WORDS.notRead} ${CHAT_READ_WORDS.why[why]}`,
});

/** What the line is read from. `undefined` in any answer means "not known
 *  yet" (not loaded, or it could not be read): an unknown is never a
 *  reason, so it can neither raise nor clear an alarm on its own. */
export interface ChatReadInput {
  status: WorkerStatus | undefined;
  /** The saved note model; null when none is picked. */
  modelId: string | null | undefined;
  chatId: number | null;
  /** The worker poll, the note-model setting, the app's settings or a
   *  required proxy's health could not be read. */
  failed?: boolean;
  /** An answer the line depends on is still on its way (the note model,
   *  the app's settings, a required proxy's health): a known alarm is
   *  still said, but nothing healthy is claimed. */
  pending?: boolean;
  /** The app's settings: is a key stored. */
  apiKeySet?: boolean;
  /** A required proxy is missing or unhealthy (the reader's `proxy_gate`). */
  proxyDown?: boolean;
}

/** Whether this chat is being read, and if not, the first reason the reader
 *  can act on, with the control that fixes it. Order: what stops every read
 *  before any request (no key, a required proxy down, no model), then the
 *  reader itself, then this chat, then every chat, the day's cap, and what
 *  is going on. Never healthy while any of those holds. */
export function chatReadState({
  status, modelId, chatId, failed = false, pending = false, apiKeySet, proxyDown,
}: ChatReadInput): ChatRead {
  // Without a key or its required proxy the reader sends nothing at all
  // (`api_key_not_set`, `proxy_gate`), whatever else is healthy.
  if (apiKeySet === false) return notRead("noKey", inSecurity(NOTES_TARGET.apiKey));
  if (proxyDown === true) return notRead("proxy", inSecurity(NOTES_TARGET.proxy));
  if (modelId === null) return notRead("noModel", inSettings(NOTES_TARGET.noteModel));
  // A failed check is not a healthy one: the last answer may be stale, and
  // an unread setting may be the missing model. Said as what it is.
  if (failed) return { kind: "unknown", text: CHAT_READ_WORDS.couldNotCheck };
  if (!status) return { kind: "unknown", text: CHAT_READ_WORDS.checking };
  const w = status.worker;
  if (!w.alive) return notRead("readerStopped", inSettings(NOTES_TARGET.workerCard));
  if (status.chat_refusal === "model_not_extractable") {
    return notRead("modelCannot", inSettings(NOTES_TARGET.noteModel));
  }
  const here = w.chat?.state;
  if (here === "stopped") return notRead("chatStopped", inSettings(NOTES_TARGET.tryChat));
  if (here === "open") return notRead("chatPaused", inSettings(NOTES_TARGET.tryChat));
  if (w.state === "stopped") return notRead("allStopped", inSettings(NOTES_TARGET.tryAll));
  if (w.state === "open") return notRead("allPaused", inSettings(NOTES_TARGET.tryAll));
  // The day's calls are used up: every new reply is skipped until tomorrow
  // (`notebook_daily_cap_reached`). Before the test-call lines below, which
  // would promise a call that the cap refuses.
  if (status.daily_cap > 0 && status.spend.calls >= status.daily_cap) {
    return notRead("capUsed", inSettings(NOTES_TARGET.workerCard));
  }
  // From here on every answer is healthy or a promise of a call, and either
  // would be a guess while the key, the proxy or the model is not known yet.
  if (pending) return { kind: "unknown", text: CHAT_READ_WORDS.checking };
  // half_open: the wait is over and the next reply IS read, by one test
  // call that is billed. Not "not read", and not a cost to keep quiet.
  if (here === "half_open") {
    return { kind: "test", text: CHAT_READ_WORDS.testHere, fix: inSettings(NOTES_TARGET.tryChat) };
  }
  if (w.state === "half_open") {
    return { kind: "test", text: CHAT_READ_WORDS.testAll, fix: inSettings(NOTES_TARGET.tryAll) };
  }
  const job = w.backfill;
  if (job && job.chat_id === chatId && (job.state === "running" || job.state === "planned")) {
    return { kind: "reading", text: CHAT_READ_WORDS.readingNow };
  }
  // "Up to date." only on THIS chat's live count (`chat_unread`, counted on
  // this poll). Not `worker.backlog`: every chat's, counted once at unlock.
  // No count (an older server, or the count failed) is never up to date.
  // Nor is a chat with parts of a very long message still to be read,
  // failed, or given up (68 a): the first part's read covers the message,
  // so `chat_unread` is already 0 while the card says a part is waiting.
  const parts = status.long_message;
  const partsLeft = (parts?.pending ?? 0) + (parts?.failed ?? 0) + (parts?.unread ?? 0);
  if (status.chat_unread === 0 && partsLeft === 0) {
    return { kind: "ok", text: CHAT_READ_WORDS.upToDate };
  }
  return { kind: "ok", text: CHAT_READ_WORDS.reading };
}

/** The line's state, from queries the tab and the composer already ask
 *  (the cache is shared). The proxy's health is asked only while a proxy
 *  is both required and set up, on the worker poll's clock. */
export function useChatRead(chatId: number | null): ChatRead {
  const status = useWorkerStatus(chatId);
  const settings = useExtractSettings();
  const app = useSettings();
  const gated = app.data != null && app.data.proxy_required && app.data.proxy_configured;
  const health = useProxyHealth(gated, WORKER_POLL_MS);
  const modelId = settings.isSuccess ? settings.data.model_id : undefined;
  return chatReadState({
    status: status.data,
    modelId,
    chatId,
    // The key and the proxy unknown is not "fine": a line that said "Up to
    // date." over settings that could not be read would be the healthy
    // header this line exists to replace.
    failed: status.isError || settings.isError || app.isError || (gated && health.isError),
    pending: settings.isPending || app.isPending || (gated && health.isPending),
    apiKeySet: app.data?.api_key_set,
    proxyDown: app.data
      ? app.data.proxy_required
        && (!app.data.proxy_configured || health.data?.healthy === false)
      : undefined,
  });
}

/** Which views carry something the reader must act on while another view
 *  is open. Settings: this chat is not read (its reason and Try again live
 *  there), or one of its settings could not be loaded. Limits: the limits
 *  or the safeword could not be loaded. */
export function useViewsNeedingLook(chatId: number | null): Record<NotesView, boolean> {
  const read = useChatRead(chatId);
  const settings = useExtractSettings();
  const auto = useAutoAccept(chatId);
  const safeword = useSafeword();
  const globalLimits = useGlobalBoundaries();
  const chatLimits = useChatBoundaries(chatId);
  const limitsFailed = chatId == null ? globalLimits.isError : chatLimits.isError;
  const notebook = useNotebook(chatId);
  return {
    // Waiting suggestions and unseen notes are COUNTED on the panel's Notes
    // tab (RightPanel); a list that could not be loaded is the one thing in
    // the Notebook view that is not a count.
    notes: notebook.isError,
    limits: safeword.isError || limitsFailed,
    // An alarm whose fix is in this view; the key and the proxy are fixed
    // on the Security tab, which the line's own button opens.
    settings: ((read.kind === "not" || read.kind === "test") && read.fix.where === "settings")
      || settings.isError || auto.isError,
  };
}

/** How many things wait for the reader in this chat: model notes not yet
 *  announced plus suggestions waiting. The count on the Notes tab button. */
export function useNotesToReview(chatId: number | null): number {
  const { data } = useNotebook(chatId);
  const seen = useSeenNotesStore((s) => (chatId == null ? undefined : s.byChat[chatId]));
  const discarded = useHeldDiscards((s) => (chatId == null ? undefined : s.byChat[chatId]));
  const all = data?.entries ?? [];
  return justSavedOf(all, seen).length + waitingOf(all, discarded ?? []).length;
}

/**
 * WHAT THE POLL IS FOR, whichever tab or view is open.
 *
 * The note list has no clock of its own. The only thing that ever
 * invalidated it was a mutation the READER made, so notes the background
 * extractor wrote while they sat reading stayed off the screen. The worker
 * status poll is already the one signal that a run happened, so the refresh
 * rides on it: no second timer, no shorter interval.
 *
 * It lived on the Background notes card, which was fine while that card
 * shared one scroll with the note list. The card is now in the Settings
 * view, and the count on the Notes tab button has to move while the reader
 * is somewhere else entirely, so the refresh is owned by the panel frame,
 * which is mounted whatever is open.
 *
 * The whole `notebook` prefix: a settled run moves the entries AND
 * `notebook_chars`. CHANGED, not increased: `done` is a count over rows that
 * deletes remove, so it falls as readily as it rises. The first count to
 * arrive is the starting point, not news. The COUNT is the dependency, not
 * the response object, which is new on every poll.
 */
export function useNotesFollowRuns(chatId: number | null): void {
  const qc = useQueryClient();
  const status = useWorkerStatus(chatId);
  const doneCount = status.data?.stats.done;
  const lastDoneCount = useRef(doneCount);
  useEffect(() => {
    if (doneCount === undefined) return;
    const seenBefore = lastDoneCount.current !== undefined;
    lastDoneCount.current = doneCount;
    if (!seenBefore) return;
    void qc.invalidateQueries({ queryKey: keys.notebook() });
  }, [doneCount, qc]);
}
