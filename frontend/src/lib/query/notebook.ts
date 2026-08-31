import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { keys } from "./keys";
import {
  setChatAutoAccept,
  sweepChat,
  listNotebook,
  createNote,
  patchNote,
  deleteNote,
  acceptNote,
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

export function useWorkerStatus() {
  return useQuery({
    queryKey: ["extraction", "worker"] as const,
    queryFn: getWorkerStatus,
    // The worker runs on its own clock. A poll is the only way the panel
    // learns that a run happened while the user was reading something else.
    refetchInterval: 20_000,
  });
}

export function useResetWorker() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => resetWorker(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["extraction", "worker"] });
    },
  });
}

export function useSweepChat() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: [number]) => sweepChat(...args),
    onSuccess: () => {
      // The whole namespace, the same as every other notebook mutation: a
      // sweep writes notes AND moves the worker's counters, and naming one
      // key is how the other goes stale.
      void qc.invalidateQueries({ queryKey: keys.notebook() });
      void qc.invalidateQueries({ queryKey: ["extraction", "worker"] });
    },
  });
}

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
