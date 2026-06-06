import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { keys } from "./keys";
import {
  listCharacters,
  createCharacter,
  importCharacter,
} from "../api/characters";
import type { Character } from "../schemas/characters";

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
