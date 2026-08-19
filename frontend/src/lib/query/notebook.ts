import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { keys } from "./keys";
import {
  listNotebook,
  createNote,
  patchNote,
  deleteNote,
  reorderNotes,
  listGlobalBoundaries,
  listChatBoundaries,
  createBoundary,
  deleteBoundary,
  setUseGlobalBoundaries,
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
