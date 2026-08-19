import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import { stopChat } from "../chat/streamRegistry";
import { keys } from "./keys";
import {
  listCharacters,
  createCharacter,
  importCharacter,
  patchCharacter,
  deleteCharacter,
} from "../api/characters";
import type { Character, CharacterPatch } from "../schemas/characters";

// One-surface rule: all character mutations are consumed by dialogs
// (CharacterCreateDialog, CharacterImportDialog, CharacterEditDialog) that
// render errors inline - so no onError toasts here.

export function useCharacters() {
  return useQuery({
    queryKey: keys.characters(),
    queryFn: listCharacters,
    staleTime: 60_000,
  });
}

export function useCreateCharacter() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: Omit<Character, "id" | "created_at">) =>
      createCharacter(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.characters() });
    },
  });
}

export function useImportCharacter() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (rawJsonText: string) => importCharacter(rawJsonText),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.characters() });
    },
  });
}

export function usePatchCharacter() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: number; payload: CharacterPatch }) =>
      patchCharacter(vars.id, vars.payload),
    onSuccess: (character) => {
      qc.setQueryData(keys.character(character.id), character);
      qc.invalidateQueries({ queryKey: keys.characters() });
      qc.invalidateQueries({ queryKey: keys.chats() });
    },
  });
}

export function useDeleteCharacter() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => deleteCharacter(id),
    // The cascade reaches chats, so it has to reach their STREAMS too (KÖK 15).
    // useDeleteChat and useClearChat both do this (v1.1 FF1/H7) and this path
    // did not: a reply for a chat the user had just deleted along with its
    // character went on generating and being billed, and then announced itself
    // with "This chat was cleared or deleted while streaming. Please refresh."
    // about a deletion the user performed on purpose.
    onMutate: (id) => {
      const doomed = chatIdsOfCharacter(qc, id);
      for (const chatId of doomed) stopChat(chatId);
      return { doomed };
    },
    onSuccess: (_data, id, context) => {
      qc.invalidateQueries({ queryKey: keys.characters() });
      qc.invalidateQueries({ queryKey: keys.chats() });
      qc.removeQueries({ queryKey: keys.character(id) });
      // Their message caches are stale the moment the rows are gone; leaving
      // them behind is what lets a deleted conversation reappear on a revisit.
      for (const chatId of context?.doomed ?? []) {
        qc.removeQueries({ queryKey: keys.messages(chatId) });
        // Inside the same loop: deleting a character takes every chat it ever
        // had, which is the path that leaves the most behind.
        qc.removeQueries({ queryKey: keys.notebookEntries(chatId) });
        qc.removeQueries({ queryKey: keys.notebookBoundaries(chatId) });
      }
    },
  });
}

/**
 * The character's chats, as far as the cache knows.
 *
 * Read BEFORE the delete, because afterwards there is nothing left to ask.
 * Best-effort by nature: a chat the client has never listed cannot be stopped
 * from here, and the server-side cascade is what actually ends it.
 */
function chatIdsOfCharacter(qc: QueryClient, characterId: number): number[] {
  const chats = qc.getQueryData<{ id: number; character_id: number }[]>(
    keys.chats(),
  );
  return (chats ?? [])
    .filter((chat) => chat.character_id === characterId)
    .map((chat) => chat.id);
}
