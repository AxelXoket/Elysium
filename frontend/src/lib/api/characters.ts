import { request, rawRequest } from "./client";
import {
  CharacterSchema,
  CharacterListSchema,
} from "../schemas/characters";
import type { Character } from "../schemas/characters";

export function listCharacters(): Promise<Character[]> {
  return request("/characters", CharacterListSchema);
}

export function getCharacter(id: number): Promise<Character> {
  return request(`/characters/${id}`, CharacterSchema);
}

export function createCharacter(
  payload: Omit<Character, "id" | "created_at">,
): Promise<Character> {
  return request("/characters", CharacterSchema, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/**
 * Import a character from raw JSON text.
 * Sends textarea content directly as body — no wrapping, no double-stringify.
 * Backend expects raw JSON body and calls json.loads(raw) directly.
 */
export function importCharacter(rawJsonText: string): Promise<Character> {
  return rawRequest("/characters/import", CharacterSchema, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: rawJsonText, // raw string as-is
  });
}
