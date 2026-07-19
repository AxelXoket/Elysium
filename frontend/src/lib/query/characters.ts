import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
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
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: keys.characters() });
      // Backend cascades: all chats/messages of the character are deleted.
      qc.invalidateQueries({ queryKey: keys.chats() });
      qc.removeQueries({ queryKey: keys.character(id) });
    },
  });
}
