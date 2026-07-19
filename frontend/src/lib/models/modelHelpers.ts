/**
 * modelHelpers.ts - Model metadata helpers for FE-7A.
 *
 * Provides:
 *  - findModelById: lookup from model list
 *  - getModelDisplayName: safe display name
 *  - getModelContextLength: context length with unknown-safe state
 *  - getModelMaxCompletionTokens: max tokens with unknown-safe state
 *  - getModelSupportedParameters: safe string array of supported params
 *  - getModelModalities: input/output modality arrays
 *  - hasInputModality / hasOutputModality: modality check
 *  - shouldShowTextOnlyNote: whether model advertises non-text input
 *  - getContextBudgetBounds: min/max for context budget UI control
 *  - TEXT_ONLY_NOTE: display copy for future UI
 *
 * Privacy:
 *  - Never stores models in browser storage
 *  - Never sends model metadata in completion payload
 *  - Never sends image_url or multimodal payloads
 *  - Never exposes API key/proxy data
 *  - Display-only: helpers produce data for UI rendering, not for requests
 */

import type { Model } from "../schemas/models";

// ── Constants ────────────────────────────────────────────────────

/** Minimum context budget tokens (matches FE-4A / backend contract). */
export const CONTEXT_BUDGET_MIN = 512;

/**
 * Display copy for text-only limitation.
 * Future Codex UI will show this when model advertises non-text modalities.
 */
export const TEXT_ONLY_NOTE =
  "This model may support other modalities, but Elysium currently sends text-only requests.";

// ── Model lookup ─────────────────────────────────────────────────

/**
 * Find a model by ID from a model list.
 * Returns `undefined` if not found, list is empty/null/undefined.
 */
export function findModelById(
  models: readonly Model[] | null | undefined,
  id: string | null | undefined,
): Model | undefined {
  if (!models || !id) return undefined;
  return models.find((m) => m.id === id);
}

// ── Display helpers ──────────────────────────────────────────────

/**
 * Return a safe display name for a model.
 * Falls back to model ID if name is empty, and to "Unknown model" if both missing.
 */
export function getModelDisplayName(
  model: Pick<Model, "name" | "id"> | null | undefined,
): string {
  if (!model) return "Unknown model";
  if (model.name && model.name.trim().length > 0) return model.name;
  if (model.id && model.id.trim().length > 0) return model.id;
  return "Unknown model";
}

/**
 * Return the context length for display.
 * Returns `null` when unknown or not positive.
 */
export function getModelContextLength(
  model: Pick<Model, "context_length"> | null | undefined,
): number | null {
  if (!model || model.context_length == null || model.context_length <= 0) {
    return null;
  }
  return model.context_length;
}

/**
 * Return the max completion tokens for display.
 * Returns `null` when unknown or not positive.
 */
export function getModelMaxCompletionTokens(
  model: Pick<Model, "max_completion_tokens"> | null | undefined,
): number | null {
  if (
    !model ||
    model.max_completion_tokens == null ||
    model.max_completion_tokens <= 0
  ) {
    return null;
  }
  return model.max_completion_tokens;
}

/**
 * Return the supported parameters as a safe readonly string array.
 * Returns empty array if model or supported_parameters is missing/null.
 * Compatible with FE-4A `filterParamsByModel` which reads `supported_parameters`.
 */
export function getModelSupportedParameters(
  model: Pick<Model, "supported_parameters"> | null | undefined,
): readonly string[] {
  if (!model?.supported_parameters) return [];
  return model.supported_parameters;
}

// ── Modality helpers ─────────────────────────────────────────────

/**
 * Return input and output modalities.
 * Returns empty arrays when missing.
 */
export function getModelModalities(
  model: Pick<Model, "input_modalities" | "output_modalities"> | null | undefined,
): { input: readonly string[]; output: readonly string[] } {
  return {
    input: model?.input_modalities ?? [],
    output: model?.output_modalities ?? [],
  };
}

/**
 * Check whether the model advertises a specific input modality.
 */
export function hasInputModality(
  model: Pick<Model, "input_modalities"> | null | undefined,
  modality: string,
): boolean {
  if (!model?.input_modalities) return false;
  return model.input_modalities.includes(modality);
}

/**
 * Check whether the model advertises a specific output modality.
 */
export function hasOutputModality(
  model: Pick<Model, "output_modalities"> | null | undefined,
  modality: string,
): boolean {
  if (!model?.output_modalities) return false;
  return model.output_modalities.includes(modality);
}

// ── Text-only note ───────────────────────────────────────────────

/**
 * Determine whether the "unsent input modality" note should be shown.
 *
 * Elysium sends both text and images, so this fires only when the model
 * advertises some OTHER input modality it will not send (e.g. "audio",
 * "video"). "image" no longer triggers it - images ARE sent to vision models.
 *
 * Returns `false` when:
 *  - model is null/undefined (don't show note when metadata unknown)
 *  - model only accepts text and/or image input
 *  - model has empty input modalities
 */
export function shouldShowTextOnlyNote(
  model: Pick<Model, "input_modalities"> | null | undefined,
): boolean {
  if (!model?.input_modalities || model.input_modalities.length === 0) {
    return false;
  }
  return model.input_modalities.some((m) => m !== "text" && m !== "image");
}

// ── Context budget bounds ────────────────────────────────────────

/**
 * Return min/max bounds for context budget UI control.
 *
 * - min: always 512 (matches backend contract and FE-4A CONTEXT_BUDGET_MIN)
 * - max: model's context_length when known and > 512, otherwise `null`
 * - When model context_length <= 512, max is set to 512 (clamped to min)
 *
 * This is for UI slider/input bounds only. It does NOT send payloads.
 * Actual clamping is done by FE-4A `clampContextBudget`.
 */
export function getContextBudgetBounds(
  model: Pick<Model, "context_length"> | null | undefined,
): { min: number; max: number | null } {
  const min = CONTEXT_BUDGET_MIN;

  if (!model || model.context_length == null || model.context_length <= 0) {
    return { min, max: null };
  }

  // If model context is at or below minimum, clamp max to min
  if (model.context_length <= min) {
    return { min, max: min };
  }

  return { min, max: model.context_length };
}
