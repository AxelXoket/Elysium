import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { keys } from "./keys";
import {
  listPersonas,
  createPersona,
  patchPersona,
  deletePersona,
  selectPersona,
} from "../api/personas";
import type { PersonaCreate, PersonaPatch } from "../schemas/personas";

export function usePersonas() {
  return useQuery({
    queryKey: keys.personas(),
    queryFn: listPersonas,
    staleTime: 30_000,
  });
}

export function useCreatePersona() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: PersonaCreate) => createPersona(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.personas() });
    },
  });
}

export function usePatchPersona() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { id: number; payload: PersonaPatch }) =>
      patchPersona(vars.id, vars.payload),
    onSuccess: (persona) => {
      qc.setQueryData(keys.persona(persona.id), persona);
      qc.invalidateQueries({ queryKey: keys.personas() });
      qc.invalidateQueries({ queryKey: keys.settings() });
    },
  });
}

export function useDeletePersona() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => deletePersona(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.personas() });
      qc.invalidateQueries({ queryKey: keys.settings() });
    },
  });
}

export function useSelectPersona() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => selectPersona(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.personas() });
      qc.invalidateQueries({ queryKey: keys.settings() });
    },
  });
}
