/**
 * activeContextPreview.ts — Active Context Preview data foundation for FE-8A.
 *
 * Produces a safe, approximate, display-only data structure describing what
 * Elysium plans to include in the next completion request.
 *
 * This is NOT the exact OpenRouter payload. The backend enforces final privacy
 * routing and request construction.
 *
 * Privacy guarantees:
 *  - Never includes API key, proxy URL, or secrets
 *  - Never includes full message content
 *  - Never includes raw_json, avatar_path, image data
 *  - Never includes persona description
 *  - Never includes character description/personality/system_prompt/first_mes
 *  - Never includes inactive personas or non-current characters
 *  - Never includes unsent drafts or UI state
 *  - Never includes provider privacy fields (zdr, data_collection, allow_fallbacks)
 *  - Never stores preview data in browser persistent storage
 */

import type { Model } from "../schemas/models";
import type { Persona } from "../schemas/personas";
import type { Character } from "../schemas/characters";
import type { GenerationParams } from "../schemas/completions";
import { getModelDisplayName, shouldShowTextOnlyNote, getModelContextLength } from "../models";
import { findActivePersona } from "../personas";
import { filterParamsByModel } from "../generation";

// ── Types ────────────────────────────────────────────────────────

export interface ActiveContextPreview {
  included: IncludedContext;
  notIncluded: readonly string[];
  disclaimer: string;
}

export interface IncludedContext {
  model: ModelPreview | null;
  persona: PersonaPreview | null;
  character: CharacterPreview | null;
  messages: MessagesPreview | null;
  generationParams: Record<string, unknown> | null;
  contextBudget: ContextBudgetPreview | null;
}

export interface ModelPreview {
  id: string;
  displayName: string;
  contextLength: number | null;
  showTextOnlyNote: boolean;
}

export interface PersonaPreview {
  id: number;
  displayName: string;
}

export interface CharacterPreview {
  id: number;
  name: string;
}

export interface MessagesPreview {
  count: number;
  note: string;
}

export interface ContextBudgetPreview {
  tokens: number;
  label: string;
}

// ── Constants ────────────────────────────────────────────────────

/**
 * Items that are never included in the outgoing completion request.
 * Used for privacy clarity in future UI.
 */
export const NOT_INCLUDED_ITEMS: readonly string[] = [
  "Inactive personas",
  "Inactive characters",
  "API key (sent only in backend-to-provider header)",
  "Proxy URL",
  "Unsent drafts",
  "UI state (sidebar, tabs, search text)",
  "Provider privacy fields (zdr, data_collection, allow_fallbacks)",
  "Avatar/image data",
  "raw_json",
  "Model search/filter state",
];

/**
 * Approximation disclaimer for the preview.
 * Must not claim exact token count or exact OpenRouter payload.
 */
export const PREVIEW_DISCLAIMER =
  "This is a local preview of what Elysium plans to include. The backend still enforces final privacy routing and request construction.";

// ── Builder ──────────────────────────────────────────────────────

export interface BuildPreviewInput {
  /** Selected model, or null if none selected. */
  model?: Model | null;
  /** Full persona list — helper extracts active only. */
  personas?: readonly Persona[] | null;
  /** Current chat's character, or null if no chat selected. */
  character?: Character | null;
  /** Current message count in the chat. */
  messageCount?: number | null;
  /** User-set generation params (pre-filtering). */
  generationParams?: GenerationParams | null;
  /** User-set context budget tokens. */
  contextBudgetTokens?: number | null;
}

/**
 * Build a safe, approximate Active Context Preview data structure.
 *
 * Reuses:
 *  - FE-7A: getModelDisplayName, shouldShowTextOnlyNote, getModelContextLength
 *  - FE-3A: findActivePersona
 *  - FE-4A: pruneGenerationParams, filterParamsByModel
 *
 * All data is display-only. No payloads are sent. No secrets are exposed.
 */
export function buildActiveContextPreview(
  input: BuildPreviewInput,
): ActiveContextPreview {
  return {
    included: {
      model: buildModelPreview(input.model),
      persona: buildPersonaPreview(input.personas),
      character: buildCharacterPreview(input.character),
      messages: buildMessagesPreview(input.messageCount),
      generationParams: buildGenParamsPreview(input.generationParams, input.model),
      contextBudget: buildContextBudgetPreview(input.contextBudgetTokens),
    },
    notIncluded: NOT_INCLUDED_ITEMS,
    disclaimer: PREVIEW_DISCLAIMER,
  };
}

// ── Internal builders ────────────────────────────────────────────

function buildModelPreview(
  model: Model | null | undefined,
): ModelPreview | null {
  if (!model) return null;
  return {
    id: model.id,
    displayName: getModelDisplayName(model),
    contextLength: getModelContextLength(model),
    showTextOnlyNote: shouldShowTextOnlyNote(model),
  };
}

function buildPersonaPreview(
  personas: readonly Persona[] | null | undefined,
): PersonaPreview | null {
  const active = findActivePersona(personas);
  if (!active) return null;
  return {
    id: active.id,
    displayName: active.display_name,
  };
}

function buildCharacterPreview(
  character: Character | null | undefined,
): CharacterPreview | null {
  if (!character) return null;
  return {
    id: character.id,
    name: character.name,
  };
}

function buildMessagesPreview(
  messageCount: number | null | undefined,
): MessagesPreview | null {
  if (messageCount == null || messageCount < 0) return null;
  return {
    count: messageCount,
    note:
      messageCount === 0
        ? "No messages yet"
        : `${messageCount} message${messageCount === 1 ? "" : "s"} in chat history`,
  };
}

function buildGenParamsPreview(
  params: GenerationParams | null | undefined,
  model: Model | null | undefined,
): Record<string, unknown> | null {
  const filtered = filterParamsByModel(params, model);
  if (!filtered) return null;
  // pruneGenerationParams is already called inside filterParamsByModel
  return { ...filtered };
}

function buildContextBudgetPreview(
  tokens: number | null | undefined,
): ContextBudgetPreview | null {
  if (tokens == null || tokens <= 0) return null;
  return {
    tokens,
    label: "Chat history inclusion budget (app-level, not forwarded to provider)",
  };
}
