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
 *  - hasInputModality: boolean modality check
 *  - hasOutputModality: three-state modality check (yes / no / unknown)
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
 * Display copy for text-only limitation, shown when a model advertises
 * non-text modalities.
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

// ── Reasoning (D3) ───────────────────────────────────────────────

/**
 * What the Thinking switch can do for this model, read the way the backend
 * reads it (openrouter.reasoning_param):
 *  - "none": the model lists no `reasoning` parameter; nothing to switch.
 *  - "optional": reasoning can be turned off, so it is off unless Thinking
 *    is on. The only case where the switch does anything.
 *  - "mandatory": the model always reasons; the app asks for the lowest
 *    effort and does not show the reasoning text.
 *  - "unknown": the model lists `reasoning` but the catalogue does not say
 *    whether it can be turned off, so the app sends nothing either way.
 */
export type ReasoningMode = "none" | "optional" | "mandatory" | "unknown";

export function getReasoningMode(
  model: Pick<Model, "supported_parameters" | "reasoning"> | null | undefined,
): ReasoningMode {
  if (!model?.supported_parameters?.includes("reasoning")) return "none";
  const info = model.reasoning;
  if (!info || Object.keys(info).length === 0) return "unknown";
  return info.mandatory ? "mandatory" : "optional";
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
 * What the catalogue says about one output modality.
 *
 * Three states, and the third one is the entire point. `"unknown"` is not a
 * quiet `"no"`. It is the state the frontend is in when the catalogue carries
 * no output modality list for a model, and it is exactly the state the backend
 * treats as permissive: `_model_emits_images` in backend/routers/completions.py
 * returns True for absent metadata, missing field, and empty list, because the
 * provider is the final arbiter. Folding `"unknown"` into `"no"` let the UI
 * tell the reader a picture would not be asked for while the request went out
 * with `modalities: ["text","image"]`. A privacy-first interface does not get
 * to make a false negative claim about what it just sent.
 */
export type ModalitySupport = "yes" | "no" | "unknown";

/**
 * What the model advertises for a specific output modality.
 *
 * `"unknown"` when there is no model, no field, or an empty list. OpenRouter
 * omits `architecture.output_modalities` for part of the catalogue and the
 * backend normalises that to `[]`, so an empty list is an absence of knowledge
 * rather than a statement of incapability.
 * `"no"` only when the list is populated and the modality is not in it.
 *
 * The sibling `acceptsImages` collapses the same three states into a boolean,
 * and that is right for it: every image INPUT gate asks a yes/no question and
 * unknown has to answer yes there to match the backend. This one has a
 * consumer that renders a sentence to a reader, so it must be able to say "I
 * do not know" instead of being forced to pick a side.
 */
export function hasOutputModality(
  model: Partial<Pick<Model, "output_modalities">> | null | undefined,
  modality: string,
): ModalitySupport {
  const mods = model?.output_modalities;
  if (!mods || mods.length === 0) return "unknown";
  return mods.includes(modality) ? "yes" : "no";
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
/**
 * Whether a payload for this model may carry image parts.
 *
 * The mirror of `_model_accepts_images` in backend/routers/completions.py,
 * and it deliberately does NOT read like `hasInputModality(model, "image")`.
 * The backend refuses only when cached metadata POSITIVELY says the model has
 * no image input; unknown or empty metadata is allowed through, because the
 * provider is the final arbiter. Deriving that rule twice, differently, is
 * what once let the attachment gate accept an image that assembly then
 * silently dropped - no error, no event, no log, and the user billed for a
 * reply about an image the model never received.
 *
 * So: absent or empty modalities mean YES here, exactly as they do there.
 *
 * AND EVERY IMAGE GATE USES IT. This function existed for a while with the
 * three gates a person actually touches - the attach button, the paste and
 * drop path, and payload assembly - still on `hasInputModality`, which is
 * the second derivation this comment warns about. They disagreed on exactly
 * the shape OpenRouter sends when a model's architecture block omits the
 * field: the composer said "does not support image input" and refused an
 * attachment, while the estimator charged for the images already in the
 * chat and the backend would have sent every one of them.
 */
export function acceptsImages(
  model: Pick<Model, "input_modalities"> | null | undefined,
): boolean {
  const mods = model?.input_modalities ?? [];
  return !(model != null && mods.length > 0 && !mods.includes("image"));
}

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
