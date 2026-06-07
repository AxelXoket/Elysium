/**
 * characterHelpers.ts — Character library logic helpers for FE-6A.
 *
 * Provides:
 *  - findCharacterById: lookup from list
 *  - safeCharacterId: validate character ID for payloads
 *  - buildStartChatInput: prepare minimal create-chat payload
 *  - CHARACTER_DELETE_CASCADE_WARNING: constant for future UI
 *
 * Core UX rule:
 *  Clicking a character must NOT auto-create a chat.
 *  Starting a chat is an explicit, separate action.
 *
 * Privacy:
 *  - Never stores characters in browser storage
 *  - Never sends raw_json, avatar data, or image_url
 *  - Never sends character description/personality in completion payload
 *  - Only character_id is passed to create-chat
 */

import type { Character } from "../schemas/characters";

/**
 * Find a character by ID from a list.
 * Returns `undefined` if not found, list is empty/null/undefined.
 */
export function findCharacterById(
  characters: readonly Character[] | null | undefined,
  id: number | null | undefined,
): Character | undefined {
  if (!characters || id == null) return undefined;
  return characters.find((c) => c.id === id);
}

/**
 * Validate a character ID for safe use in payloads.
 * Returns the ID if it's a positive integer, otherwise `undefined`.
 * Backend character IDs are always positive integers (SQLite auto-increment).
 */
export function safeCharacterId(
  id: number | null | undefined,
): number | undefined {
  if (id == null) return undefined;
  if (!Number.isInteger(id) || id <= 0) return undefined;
  return id;
}

/**
 * Build the minimal input for creating a new chat with a character.
 *
 * This is a pure helper — it does NOT call the API or create a chat.
 * It prepares the payload shape expected by `useCreateChat` / `createChat`.
 *
 * Rules:
 *  - Requires a valid character ID (positive integer)
 *  - Returns `undefined` if ID is invalid
 *  - Only includes `character_id` — no description, personality, raw_json, avatar
 *  - Optional `title` for user-provided chat name
 */
export function buildStartChatInput(
  characterId: number | null | undefined,
  title?: string,
): { character_id: number; title?: string } | undefined {
  const safe = safeCharacterId(characterId);
  if (safe === undefined) return undefined;

  const input: { character_id: number; title?: string } = {
    character_id: safe,
  };
  if (title != null && title.trim().length > 0) {
    input.title = title.trim();
  }
  return input;
}

/**
 * Warning text for character delete cascade.
 * Future Codex UI will display this in a confirmation dialog.
 */
export const CHARACTER_DELETE_CASCADE_WARNING =
  "Deleting this character will also remove all of its chats and messages. This action cannot be undone.";
