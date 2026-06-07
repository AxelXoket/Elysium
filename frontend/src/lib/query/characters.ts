import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { keys } from "./keys";
import { useErrorStore } from "../errors";
import {
  listCharacters,
  createCharacter,
  importCharacter,
  patchCharacter,
  deleteCharacter,
} from "../api/characters";
import type { Character, CharacterPatch } from "../schemas/characters";

export function useCharacters() {
  return useQuery({
    queryKey: keys.characters(),
    queryFn: listCharacters,
    staleTime: 60_000,
  });
}

export function useCreateCharacter() {
  const qc = useQueryClient();
  const pushError = useErrorStore((s) => s.pushError);
  return useMutation({
    mutationFn: (payload: Omit<Character, "id" | "created_at">) =>
      createCharacter(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.characters() });
    },
    onError: (err) => {
      pushError(err);
    },
  });
}

export function useImportCharacter() {
  const qc = useQueryClient();
  const pushError = useErrorStore((s) => s.pushError);
  return useMutation({
    mutationFn: (rawJsonText: string) => importCharacter(rawJsonText),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.characters() });
    },
    onError: (err) => {
      pushError(err);
    },
  });
}

export function usePatchCharacter() {
  const qc = useQueryClient();
  const pushError = useErrorStore((s) => s.pushError);
  return useMutation({
    mutationFn: (vars: { id: number; payload: CharacterPatch }) =>
      patchCharacter(vars.id, vars.payload),
    onSuccess: (character) => {
      qc.setQueryData(keys.character(character.id), character);
      qc.invalidateQueries({ queryKey: keys.characters() });
      qc.invalidateQueries({ queryKey: keys.chats() });
    },
    onError: (err) => {
      pushError(err);
    },
  });
}

export function useDeleteCharacter() {
  const qc = useQueryClient();
  const pushError = useErrorStore((s) => s.pushError);
  return useMutation({
    mutationFn: (id: number) => deleteCharacter(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.characters() });
      qc.invalidateQueries({ queryKey: keys.chats() });
    },
    onError: (err) => {
      pushError(err);
    },
  });
}

