/**
 * characterHelpers.ts - Character library logic helpers for FE-6A.
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
 * This is a pure helper - it does NOT call the API or create a chat.
 * It prepares the payload shape expected by `useCreateChat` / `createChat`.
 *
 * Rules:
 *  - Requires a valid character ID (positive integer)
 *  - Returns `undefined` if ID is invalid
 *  - Only includes `character_id` - no description, personality, raw_json, avatar
 *  - Sends the trimmed `title`, empty or not
 *
 * The title used to be DROPPED when it was blank, so the server could not
 * tell "left empty" from "not sent". That distinction never mattered while
 * both meant the same thing, and it stopped being harmless the moment an
 * empty title became a request for a default name: the key is sent as an
 * empty string now and the server decides, which is the one place that can
 * see the other titles it has to be numbered against.
 */
export function buildStartChatInput(
  characterId: number | null | undefined,
  title?: string,
): { character_id: number; title: string } | undefined {
  const safe = safeCharacterId(characterId);
  if (safe === undefined) return undefined;

  return { character_id: safe, title: (title ?? "").trim() };
}

/**
 * Warning text for character delete cascade, for a confirmation dialog.
 */
export const CHARACTER_DELETE_CASCADE_WARNING =
  "Deleting this character will also remove all of its chats and messages. This action cannot be undone.";
