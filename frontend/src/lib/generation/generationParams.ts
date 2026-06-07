/**
 * generationParams.ts — Generation parameter utilities for FE-4A.
 *
 * Provides:
 *  - Known allowed parameter names (backend allowlist)
 *  - Pruning of undefined/null values while preserving valid falsy (e.g., 0)
 *  - Model-aware filtering using `supported_parameters` from model metadata
 *  - max_tokens clamping against `max_completion_tokens`
 *  - context_budget_tokens clamping (min 512, max model context_length)
 *  - Completion/regenerate payload construction with privacy guarantees
 *
 * Privacy: No provider fields (zdr, data_collection, allow_fallbacks, provider)
 * are ever included in any output of these helpers.
 */

import type { GenerationParams } from "../schemas/completions";
import type { Model } from "../schemas/models";

// ── Known allowed generation parameter names ─────────────────────

/** Backend-allowed generation param keys (from frontend_contract.md). */
export const ALLOWED_GEN_PARAM_KEYS: ReadonlySet<string> = new Set([
  "temperature",
  "top_p",
  "top_k",
  "repetition_penalty",
  "max_tokens",
  "seed",
]);

/** Fields that must NEVER appear in any outgoing request. */
const FORBIDDEN_FIELDS = new Set([
  "provider",
  "zdr",
  "data_collection",
  "allow_fallbacks",
]);

// ── Pruning ──────────────────────────────────────────────────────

/**
 * Remove keys whose values are `undefined` or `null` from generation params.
 * Preserves valid falsy values like `0`.
 * Removes any key not in the allowed set.
 * Returns `undefined` if result would be empty (no keys with values).
 */
export function pruneGenerationParams(
  params: GenerationParams | undefined | null,
): GenerationParams | undefined {
  if (!params) return undefined;

  const result: Record<string, unknown> = {};
  let hasKeys = false;

  for (const [key, value] of Object.entries(params)) {
    // Skip disallowed keys
    if (!ALLOWED_GEN_PARAM_KEYS.has(key)) continue;
    // Skip forbidden fields (defense-in-depth)
    if (FORBIDDEN_FIELDS.has(key)) continue;
    // Skip undefined and null
    if (value === undefined || value === null) continue;
    result[key] = value;
    hasKeys = true;
  }

  return hasKeys ? (result as GenerationParams) : undefined;
}

// ── Model-aware filtering ────────────────────────────────────────

/**
 * Filter generation params to only include keys supported by the given model.
 *
 * Behavior when `model.supported_parameters` is empty:
 *   - All allowed params are included (permissive fallback).
 *   - Assumption: the backend will do its own filtering before forwarding.
 *   - This avoids blocking the user from sending valid params when metadata is incomplete.
 *
 * When `model.supported_parameters` is populated:
 *   - Only params present in both the allowed set AND the model's supported list are kept.
 */
export function filterParamsByModel(
  params: GenerationParams | undefined | null,
  model: Pick<Model, "supported_parameters"> | null | undefined,
): GenerationParams | undefined {
  const pruned = pruneGenerationParams(params);
  if (!pruned) return undefined;

  // If no model or empty supported_parameters → pass through (permissive fallback)
  if (!model?.supported_parameters || model.supported_parameters.length === 0) {
    return pruned;
  }

  const supported = new Set(model.supported_parameters);
  const result: Record<string, unknown> = {};
  let hasKeys = false;

  for (const [key, value] of Object.entries(pruned)) {
    if (supported.has(key)) {
      result[key] = value;
      hasKeys = true;
    }
  }

  return hasKeys ? (result as GenerationParams) : undefined;
}

/**
 * Check whether a specific generation parameter is supported by the model.
 * Returns `true` if supported_parameters is missing/empty (permissive fallback).
 */
export function isParamSupportedByModel(
  paramKey: string,
  model: Pick<Model, "supported_parameters"> | null | undefined,
): boolean {
  if (!model?.supported_parameters || model.supported_parameters.length === 0) {
    return true;
  }
  return model.supported_parameters.includes(paramKey);
}

// ── max_tokens clamping ──────────────────────────────────────────

/**
 * Clamp `max_tokens` to the model's `max_completion_tokens` if known.
 * Returns the original value if model metadata is unavailable.
 */
export function clampMaxTokens(
  maxTokens: number | undefined | null,
  model: Pick<Model, "max_completion_tokens"> | null | undefined,
): number | undefined {
  if (maxTokens == null) return undefined;
  if (model?.max_completion_tokens != null && model.max_completion_tokens > 0) {
    return Math.min(maxTokens, model.max_completion_tokens);
  }
  return maxTokens;
}

// ── Context budget ───────────────────────────────────────────────

const CONTEXT_BUDGET_MIN = 512;

/**
 * Clamp `context_budget_tokens` to valid range.
 * - Minimum: 512
 * - Maximum: model's `context_length` if known, otherwise no upper clamp
 * - Returns `undefined` if input is `undefined` or `null`
 */
export function clampContextBudget(
  budget: number | undefined | null,
  model: Pick<Model, "context_length"> | null | undefined,
): number | undefined {
  if (budget == null) return undefined;

  let clamped = Math.max(budget, CONTEXT_BUDGET_MIN);

  if (model?.context_length != null && model.context_length > 0) {
    clamped = Math.min(clamped, model.context_length);
  }

  return clamped;
}

// ── Payload construction ─────────────────────────────────────────

interface CompletionPayloadInput {
  message: string;
  modelId: string;
  generationParams?: GenerationParams | null;
  personaId?: number | null;
  contextBudgetTokens?: number | null;
  model?: Pick<Model, "supported_parameters" | "max_completion_tokens" | "context_length"> | null;
}

interface RegeneratePayloadInput {
  modelId: string;
  generationParams?: GenerationParams | null;
  personaId?: number | null;
  contextBudgetTokens?: number | null;
  model?: Pick<Model, "supported_parameters" | "max_completion_tokens" | "context_length"> | null;
}

/**
 * Build a safe completion request payload.
 * - Filters and prunes generation params by model support
 * - Clamps max_tokens and context_budget_tokens
 * - Never includes provider/privacy fields
 * - Only includes optional fields when they have values
 */
export function buildCompletionPayload(input: CompletionPayloadInput) {
  let filtered = filterParamsByModel(input.generationParams, input.model);

  // Clamp max_tokens if present
  if (filtered?.max_tokens != null) {
    const clamped = clampMaxTokens(filtered.max_tokens, input.model);
    if (clamped !== filtered.max_tokens) {
      filtered = { ...filtered, max_tokens: clamped };
    }
  }

  const payload: Record<string, unknown> = {
    message: input.message,
    model_id: input.modelId,
  };

  if (filtered) {
    payload.generation_params = filtered;
  }
  if (input.personaId != null) {
    payload.persona_id = input.personaId;
  }

  const clampedBudget = clampContextBudget(input.contextBudgetTokens, input.model);
  if (clampedBudget != null) {
    payload.context_budget_tokens = clampedBudget;
  }

  return payload;
}

/**
 * Build a safe regenerate request payload.
 * Same safety rules as buildCompletionPayload but without `message`.
 */
export function buildRegeneratePayload(input: RegeneratePayloadInput) {
  let filtered = filterParamsByModel(input.generationParams, input.model);

  if (filtered?.max_tokens != null) {
    const clamped = clampMaxTokens(filtered.max_tokens, input.model);
    if (clamped !== filtered.max_tokens) {
      filtered = { ...filtered, max_tokens: clamped };
    }
  }

  const payload: Record<string, unknown> = {
    model_id: input.modelId,
  };

  if (filtered) {
    payload.generation_params = filtered;
  }
  if (input.personaId != null) {
    payload.persona_id = input.personaId;
  }

  const clampedBudget = clampContextBudget(input.contextBudgetTokens, input.model);
  if (clampedBudget != null) {
    payload.context_budget_tokens = clampedBudget;
  }

  return payload;
}
