import {
  useMutation,
  useMutationState,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import type { DryRunResult } from "@/lib/schemas/notebook";
import { keys } from "./keys";
import {
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
  dryRun,
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
    (id: number, payload: Parameters<typeof patchNote>[1]) =>
      patchNote(id, payload),
  );

export const useDeleteNote = () =>
  useNotebookMutation((id: number) => deleteNote(id));

export const useAcceptNote = () =>
  useNotebookMutation((id: number) => acceptNote(id));

export const useReorderNotes = () =>
  useNotebookMutation((chatId: number, ids: number[]) =>
    reorderNotes(chatId, ids),
  );

export const useCreateBoundary = () =>
  useNotebookMutation((payload: Parameters<typeof createBoundary>[0]) =>
    createBoundary(payload),
  );

export const useDeleteBoundary = () =>
  useNotebookMutation((id: number) => deleteBoundary(id));

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

/** The dry run writes NOTHING, so it deliberately does not invalidate the
 *  notebook - a refetch here would suggest something had been stored. */
export const DRY_RUN_KEY = ["extraction", "dry-run"] as const;

/** The dry run's state, read from the CLIENT rather than from the component.
 *
 *  `useMutation` state is observer-local: it dies with the component. The
 *  right panel remounts its panels on every tab switch - deliberately - so a
 *  user who starts a run, switches tabs and comes back finds an enabled
 *  button and an empty panel while the first call, which is not cancellable
 *  and runs to completion server-side, is still being billed. It looks like
 *  nothing happened. Clicking again pays twice, on their own key.
 *
 *  So the in-flight flag comes from the mutation cache and the result from the
 *  query cache: both outlive the mount. */
export function useDryRunState(chatId: number | null) {
  const pending = useMutationState({
    filters: { mutationKey: DRY_RUN_KEY, status: "pending" },
    select: (m) => m.state.status,
  }).length > 0;
  const result = useQuery<DryRunResult | null>({
    queryKey: [...DRY_RUN_KEY, chatId] as const,
    // Never fetched - this cache entry only ever holds what the mutation put
    // in it. A dry run is an action, not a resource: re-requesting it on a
    // remount would spend money to redraw a panel.
    queryFn: () => null,
    enabled: false,
    gcTime: 10 * 60_000,
  });
  return { pending, result: result.data ?? null };
}

export function useDryRun() {
  const qc = useQueryClient();
  return useMutation({
    // A mutationKey, so the in-flight state is SHARED rather than living in
    // whichever component observed it. Without one, switching tabs unmounts
    // the panel - which RightPanel does on purpose - and the button comes
    // back enabled with no result on screen while the first call, which is
    // not cancellable and runs to completion server-side, is still being
    // billed. It looks like nothing happened. Clicking again pays twice.
    mutationKey: DRY_RUN_KEY,
    mutationFn: (args: [number]) => dryRun(...args),
    onSuccess: (data, args) => {
      qc.setQueryData([...DRY_RUN_KEY, args[0]], data);
      // It writes no NOTES, which is what the panel says - but it does claim
      // a call against the daily cap and record what it cost. Leaving the
      // status card stale meant the "N of 60 calls today" line was wrong for
      // twenty seconds after every press of the button that changed it.
      void qc.invalidateQueries({ queryKey: ["extraction", "worker"] });
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

export function useAutoAccept() {
  return useQuery({
    queryKey: ["extraction", "auto-accept"] as const,
    queryFn: getAutoAccept,
  });
}

export function useSetAutoAccept() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: [boolean]) => setAutoAccept(...args),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["extraction", "auto-accept"] });
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
