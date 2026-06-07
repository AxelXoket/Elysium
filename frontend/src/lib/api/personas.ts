import { request } from "./client";
import { OkResponseSchema } from "../schemas/settings";
import {
  PersonaSchema,
  PersonaListSchema,
  PersonaSelectResponseSchema,
} from "../schemas/personas";
import type { OkResponse } from "../schemas/settings";
import type {
  Persona,
  PersonaCreate,
  PersonaPatch,
  PersonaSelectResponse,
} from "../schemas/personas";

export function listPersonas(): Promise<Persona[]> {
  return request("/personas", PersonaListSchema);
}

export function createPersona(payload: PersonaCreate): Promise<Persona> {
  return request("/personas", PersonaSchema, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function patchPersona(
  id: number,
  payload: PersonaPatch,
): Promise<Persona> {
  return request(`/personas/${id}`, PersonaSchema, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deletePersona(id: number): Promise<OkResponse> {
  return request(`/personas/${id}`, OkResponseSchema, {
    method: "DELETE",
  });
}

export function selectPersona(id: number): Promise<PersonaSelectResponse> {
  return request(`/personas/${id}/select`, PersonaSelectResponseSchema, {
    method: "POST",
  });
}
