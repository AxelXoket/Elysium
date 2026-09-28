import { useCallback, useEffect, useRef } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { create } from "zustand";

import { keys } from "./keys";
import {
  setChatAutoAccept,
  getBackfillPlan,
  runBackfill,
  stopBackfill,
  undoBackfill,
  listNotebook,
  createNote,
  patchNote,
  deleteNote,
  acceptNote,
  keepNote,
  getWorkerStatus,
  resetWorker,
  getAutoAccept,
  setAutoAccept,
  getSafeword,
  setSafeword,
  reorderNotes,
  listGlobalBoundaries,
  listChatBoundaries,
  createBoundary,
  deleteBoundary,
  setUseGlobalBoundaries,
  listExtractionModels,
  getExtractSettings,
  saveExtractSettings,
} from "../api/notebook";

/** Notes for one chat, plus what they cost.
 *
 *  The sentinel key mirrors `useMessages`: with no chat selected the query is
 *  disabled and parked under a key that can never collide with a real chat's. */
export function useNotebook(chatId: number | null) {
  return useQuery({
    queryKey:
      chatId == null ? (["notebook", "entries", "__none__"] as const)
                     : keys.notebookEntries(chatId),
    queryFn: () => listNotebook(chatId as number),
    enabled: chatId != null,
  });
}

export function useChatBoundaries(chatId: number | null) {
  return useQuery({
    queryKey:
      chatId == null ? (["notebook", "boundaries", "__none__"] as const)
                     : keys.notebookBoundaries(chatId),
    queryFn: () => listChatBoundaries(chatId as number),
    enabled: chatId != null,
  });
}

export function useGlobalBoundaries() {
  return useQuery({
    queryKey: keys.notebookBoundaries(null),
    queryFn: listGlobalBoundaries,
  });
}

/** Every mutation invalidates the whole `notebook` namespace rather than one
 *  key. A note added here changes the entry list AND `notebook_chars`, and a
 *  boundary changes what a chat sends; naming each affected key separately is
 *  how one of them gets forgotten and the screen quietly shows stale numbers. */
function useNotebookMutation<TArgs extends unknown[], TResult>(
  fn: (...args: TArgs) => Promise<TResult>,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: TArgs) => fn(...args),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.notebook() });
    },
  });
}

export const useCreateNote = () =>
  useNotebookMutation(
    (chatId: number, payload: Parameters<typeof createNote>[1]) =>
      createNote(chatId, payload),
  );

export const usePatchNote = () =>
  useNotebookMutation(
    (id: number, chatId: number, payload: Parameters<typeof patchNote>[2]) =>
      patchNote(id, chatId, payload),
  );

export const useDeleteNote = () =>
  useNotebookMutation((id: number, chatId: number) => deleteNote(id, chatId));

export const useAcceptNote = () =>
  useNotebookMutation((id: number, chatId: number) => acceptNote(id, chatId));

export const useKeepNote = () =>
  useNotebookMutation((id: number, chatId: number) => keepNote(id, chatId));

/** Sends the deletes a Discard held back (the row said "Suggestion
 *  discarded." with an Undo while they waited), ONLY for notes that are
 *  still suggestions in the list on hand: a note that became accepted in
 *  the meantime is not deleted by a late discard. Ordinary requests; a
 *  failed one leaves the suggestion where it was (it shows up again, it is
 *  not lost). Resolves once every delete has settled. */
export async function sendHeldDiscards(
  qc: QueryClient, ids: readonly number[], chatId: number,
): Promise<void> {
  if (ids.length === 0) return;
  const list = qc.getQueryData<{ entries: { id: number; status: string }[] }>(
    keys.notebookEntries(chatId));
  const stillSuggested = ids.filter((id) =>
    list?.entries.find((e) => e.id === id)?.status === "proposed");
  if (stillSuggested.length === 0) return;
  await Promise.allSettled(stillSuggested.map((id) => deleteNote(id, chatId)));
  await qc.invalidateQueries({ queryKey: keys.notebook() });
}

/** `sendHeldDiscards`, bound to the query client, for the panel's
 *  leave-the-chat and unmount paths (a mutation hook of a component that is
 *  going away cannot be used there). */
export function useCommitDiscards() {
  const qc = useQueryClient();
  return useCallback((ids: readonly number[], chatId: number) => {
    void sendHeldDiscards(qc, ids, chatId);
  }, [qc]);
}

export const useReorderNotes = () =>
  useNotebookMutation((chatId: number, ids: number[]) =>
    reorderNotes(chatId, ids),
  );

export const useCreateBoundary = () =>
  useNotebookMutation((payload: Parameters<typeof createBoundary>[0]) =>
    createBoundary(payload),
  );

export const useDeleteBoundary = () =>
  useNotebookMutation((id: number, chatId?: number | null) =>
    deleteBoundary(id, chatId));

export const useSetUseGlobalBoundaries = () =>
  useNotebookMutation((chatId: number, use: boolean) =>
    setUseGlobalBoundaries(chatId, use),
  );

/** The pickable models. Cached like the model catalogue is: the list changes
 *  on OpenRouter's clock, not the user's.
 *
 *  Keyed OUTSIDE the `["notebook"]` prefix on purpose. Every notebook mutation
 *  invalidates that prefix, and invalidateQueries refetches active queries
 *  regardless of staleTime - so under the old key, adding a note, pinning one
 *  or editing a boundary each fired a fresh round trip to OpenRouter and the
 *  five-minute staleTime below meant nothing. */
export function useExtractionModels() {
  return useQuery({
    queryKey: ["extraction", "models"] as const,
    queryFn: listExtractionModels,
    staleTime: 300_000,
  });
}

export function useExtractSettings() {
  return useQuery({
    queryKey: ["extraction", "settings"] as const,
    queryFn: getExtractSettings,
  });
}

/** Saves the choice and refreshes the choice - not the whole notebook. */
export function useSaveExtractSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: [Parameters<typeof saveExtractSettings>[0]]) =>
      saveExtractSettings(...args),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["extraction", "settings"] });
    },
  });
}


// ── FAZ 5: the background extractor ────────────────────────────────────────
//
// Keyed outside the `["notebook"]` prefix, like the model picker and for the
// same reason: a note mutation must not drag a status poll along with it.

/** How often the panel asks what the worker has been doing.
 *
 *  Named rather than written inline because it is the app's ONE background
 *  clock for the notebook: the note list hangs off this tick too (see
 *  WorkerPanel) instead of running a second timer beside it, and a test that
 *  waits for a poll has to wait for the same number the app uses rather than
 *  a copy of it that can drift. */
export const WORKER_POLL_MS = 20_000;

/** `chatId` in the KEY as well as in the request. Without it the two chats'
 *  answers share one cache entry, and selecting a paused chat would show the
 *  previous chat's breaker until the next poll - a status screen saying
 *  something true about the wrong conversation. */
export function useWorkerStatus(chatId?: number | null) {
  return useQuery({
    queryKey: [...keys.extractionWorker(), chatId ?? null] as const,
    queryFn: () => getWorkerStatus(chatId),
    // The worker runs on its own clock. A poll is the only way the panel
    // learns that a run happened while the user was reading something else.
    refetchInterval: WORKER_POLL_MS,
  });
}

// -- Decision 58 a: notes on screen right after the read that wrote them ----
//
// A reply that ends is what starts a read, and the read ends a few seconds
// later. The list used to learn about it only on the twenty-second tick
// above, and the refresh the stream fires on `done` runs BEFORE the read,
// so it fetched the old list. So while the Notes tab is open, a finished
// reply starts a short poll of this chat's status; the first answer that
// says the chat is no longer being read refreshes the list once and ends it.

/** The fast poll's step. Under two seconds so a note is on screen at most
 *  one step after the read that wrote it. */
export const FAST_POLL_MS = 1_500;

/** The fast poll's ceiling: the reply's own timeout (120 s) in steps. */
export const FAST_POLL_MAX = 80;

/** Replies that finished, counted per chat. The stream bumps it on `done`;
 *  only a Notes tab that is open when it moves acts on it. */
export const useReplyFinishedStore = create<{
  finished: Record<number, number>;
  mark: (chatId: number) => void;
}>((set) => ({
  finished: {},
  mark: (chatId) =>
    set((s) => ({
      finished: { ...s.finished, [chatId]: (s.finished[chatId] ?? 0) + 1 },
    })),
}));

/** Mounted with the Notes tab (WorkerPanel), so a closed tab asks nothing
 *  and unmounting ends the poll. A failed ask ends it too: that is the vault
 *  locking, and asking a locked vault again helps nobody. */
export function useNotesAfterReply(chatId?: number | null) {
  const qc = useQueryClient();
  const finished = useReplyFinishedStore((s) =>
    chatId == null ? 0 : (s.finished[chatId] ?? 0));
  // What had already happened when this chat came on screen is not news:
  // the list was fetched on the same mount.
  const seen = useRef({ chatId, finished });
  useEffect(() => {
    if (chatId == null) return;
    const before = seen.current;
    seen.current = { chatId, finished };
    if (before.chatId !== chatId || before.finished === finished) return;

    let stopped = false;
    let asked = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ask = async () => {
      if (stopped) return;
      asked += 1;
      let reading: boolean;
      try {
        reading = (await getWorkerStatus(chatId)).worker.reading === true;
      } catch {
        return;
      }
      if (stopped) return;
      if (!reading) {
        void qc.invalidateQueries({ queryKey: keys.notebook() });
        return;
      }
      if (asked < FAST_POLL_MAX) timer = setTimeout(ask, FAST_POLL_MS);
    };
    timer = setTimeout(ask, FAST_POLL_MS);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [chatId, finished, qc]);
}

export function useResetWorker() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (chatId?: number | null) => resetWorker(chatId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.extractionWorker() });
    },
  });
}

// ── Backfill jobs ──────────────────────────────────────────────────────────

/** The preview for one chat and scope. Keyed under `extraction`, outside
 *  the `notebook` prefix, for the same reason the worker poll is: a note
 *  mutation must not refetch a preview. It IS refetched by the three job
 *  mutations below, because each of them changes what is unread. `enabled`
 *  is the strip's: the preview is asked for when the reader opens the
 *  strip, not on every mount of the notes tab. */
export function useBackfillPlan(
  chatId: number | null,
  scope: Parameters<typeof getBackfillPlan>[1],
  range: Parameters<typeof getBackfillPlan>[2],
  enabled: boolean,
) {
  const from = range?.from_message_id ?? null;
  const to = range?.to_message_id ?? null;
  return useQuery({
    queryKey: keys.extractionBackfillPlan(chatId ?? null, scope, from, to),
    queryFn: () => getBackfillPlan(chatId as number, scope, range),
    enabled: enabled && chatId != null
      && (scope !== "range" || (from != null && to != null)),
  });
}

/** After any of the three, the notebook namespace (notes were written or
 *  removed), the worker poll (its `backfill` block is this job) and every
 *  preview (what is unread changed) are all stale. Named here once. */
function useBackfillMutation<TArgs extends unknown[], TResult>(
  fn: (...args: TArgs) => Promise<TResult>,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: TArgs) => fn(...args),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.notebook() });
      void qc.invalidateQueries({ queryKey: keys.extractionWorker() });
      void qc.invalidateQueries({ queryKey: keys.extractionBackfill() });
    },
  });
}

export const useRunBackfill = () =>
  useBackfillMutation(
    (chatId: number, scope: Parameters<typeof runBackfill>[1],
     range?: Parameters<typeof runBackfill>[2]) =>
      runBackfill(chatId, scope, range),
  );

export const useStopBackfill = () =>
  useBackfillMutation((chatId: number) => stopBackfill(chatId));

export const useUndoBackfill = () =>
  useBackfillMutation((jobId: number, chatId: number) =>
    undoBackfill(jobId, chatId));

export function useAutoAccept(chatId?: number | null) {
  return useQuery({
    // The chat is IN the key. Without it the answer for one chat would be
    // served to the next, and the answer is chat-specific now.
    queryKey: ["extraction", "auto-accept", chatId ?? null] as const,
    queryFn: () => getAutoAccept(chatId),
  });
}

export function useSetChatAutoAccept() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: [number, boolean | null]) => setChatAutoAccept(...args),
    onSuccess: () => {
      // The whole prefix, so every chat's cached answer is refreshed - the
      // key now carries a chat id and naming one of them would leave the
      // others stale.
      void qc.invalidateQueries({ queryKey: ["extraction", "auto-accept"] });
      void qc.invalidateQueries({ queryKey: keys.notebook() });
    },
  });
}

export function useSetAutoAccept() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: [boolean]) => setAutoAccept(...args),
    onSuccess: () => {
      // The prefix, not the exact key: the key carries a chat id now.
      void qc.invalidateQueries({ queryKey: ["extraction", "auto-accept"] });
      // Turning automatic acceptance ON accepts what is already pending, so
      // the switch changes the ENTRIES too - and only its own key was being
      // invalidated. The panel kept every proposal sitting in the pending
      // state the user had just cleared.
      void qc.invalidateQueries({ queryKey: keys.notebook() });
    },
  });
}


export function useSafeword() {
  return useQuery({
    queryKey: ["extraction", "safeword"] as const,
    queryFn: getSafeword,
  });
}

export function useSetSafeword() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: [string]) => setSafeword(...args),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["extraction", "safeword"] });
    },
  });
}
