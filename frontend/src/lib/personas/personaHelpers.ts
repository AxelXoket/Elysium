/**
 * personaHelpers.ts — Persona selection/data-flow utilities for FE-3A.
 *
 * Provides:
 *  - Find the active persona from a list
 *  - Extract selected persona ID from persona list or settings
 *  - Validate that a persona ID is safe to include in a completion payload
 *
 * Privacy:
 *  - Never stores personas in browser storage
 *  - Never includes persona description in completion payloads
 *  - Never sends full persona objects — only persona_id
 *  - Only the active/selected persona may be used for generation
 */

import type { Persona } from "../schemas/personas";

/**
 * Find the active persona from a list.
 * Returns `undefined` if no persona has `is_active === true`.
 *
 * `is_active` is derived by the backend from `settings.selected_persona_id`.
 * Frontend does not maintain its own active-persona state.
 */
export function findActivePersona(
  personas: readonly Persona[] | null | undefined,
): Persona | undefined {
  if (!personas) return undefined;
  return personas.find((p) => p.is_active);
}

/**
 * Extract the selected persona ID from a persona list.
 * Returns `undefined` if no persona is active.
 *
 * Use this when building completion/regenerate payloads to include only the
 * selected persona's ID — never the full object or description.
 */
export function getSelectedPersonaId(
  personas: readonly Persona[] | null | undefined,
): number | undefined {
  return findActivePersona(personas)?.id;
}

/**
 * Check whether a persona ID is present and valid for payload inclusion.
 * Returns the ID if it's a positive integer, otherwise `undefined`.
 *
 * This is a safety helper — prevents sending `0`, negative IDs, or non-numeric
 * values in the `persona_id` field. Backend persona IDs are always positive integers.
 */
export function safePersonaId(
  id: number | null | undefined,
): number | undefined {
  if (id == null) return undefined;
  if (!Number.isInteger(id) || id <= 0) return undefined;
  return id;
}
