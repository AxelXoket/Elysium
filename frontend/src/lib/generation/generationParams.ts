/**
 * generationParams.ts - Generation parameter utilities for FE-4A.
 *
 * Provides:
 *  - Known allowed parameter names (backend allowlist)
 *  - Pruning of undefined/null values while preserving valid falsy (e.g., 0)
 *  - Model-aware filtering using `supported_parameters` from model metadata
 *  - max_tokens clamping (contract range 1-131072, capped by `max_completion_tokens`)
 *  - context_budget_tokens clamping (contract range 512-2,000,000, capped by model context_length)
 *  - Completion/regenerate payload construction with privacy guarantees
 *
 * Privacy: No provider fields (zdr, data_collection, allow_fallbacks, provider)
 * are ever included in any output of these helpers.
 */

import type { GenerationParams } from "../schemas/completions";
import type { Model } from "../schemas/models";
import { getContextBudgetBounds } from "../models";

// ── Known allowed generation parameter names ─────────────────────

/** Backend-allowed generation param keys (from frontend_contract.md). */
export const ALLOWED_GEN_PARAM_KEYS: ReadonlySet<string> = new Set([
  "temperature",
  "top_p",
  "top_k",
  "repetition_penalty",
  "max_tokens",
  "seed",
  "stop",
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
 * Sanitize a `stop` value: keep a non-empty string, or an array reduced to
 * its non-empty string items. Empty strings, empty arrays, arrays whose
 * items are all empty, and non-string shapes are pruned (not rejected) -
 * the backend 422s on empty stop values, so the frontend drops upfront
 * what the backend would refuse.
 */
function sanitizeStop(value: unknown): string | string[] | undefined {
  if (typeof value === "string") {
    return value.length > 0 ? value : undefined;
  }
  if (Array.isArray(value)) {
    const items = value.filter(
      (item): item is string => typeof item === "string" && item.length > 0,
    );
    return items.length > 0 ? items : undefined;
  }
  return undefined;
}

/**
 * Remove keys whose values are `undefined` or `null` from generation params.
 * Preserves valid falsy values like `0`.
 * Removes any key not in the allowed set.
 * `stop` gets shape sanitation on top: empty strings/arrays and empty-string
 * items are dropped instead of forwarded (see sanitizeStop).
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
    if (key === "stop") {
      const sanitized = sanitizeStop(value);
      if (sanitized === undefined) continue;
      result[key] = sanitized;
      hasKeys = true;
      continue;
    }
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
 *   - Exception: `stop` always passes through - the backend keeps `stop`
 *     regardless of supported_parameters (`k in supported or k == "stop"`),
 *     so filtering it here would silently diverge from what is sent.
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
    // Mirror the backend allowlist rule `k in supported or k == "stop"`:
    // stop is kept even when the model does not advertise it.
    if (supported.has(key) || key === "stop") {
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

/** Contract bounds for max_tokens (from frontend_contract.md). */
const MAX_TOKENS_MIN = 1;
const MAX_TOKENS_MAX = 131072;

/**
 * Clamp `max_tokens` to the contract range [1, 131072], further capped
 * by the model's `max_completion_tokens` when known.
 * Returns `undefined` if input is `undefined` or `null`.
 */
export function clampMaxTokens(
  maxTokens: number | undefined | null,
  model: Pick<Model, "max_completion_tokens"> | null | undefined,
): number | undefined {
  if (maxTokens == null) return undefined;

  let max = MAX_TOKENS_MAX;
  if (model?.max_completion_tokens != null && model.max_completion_tokens > 0) {
    max = Math.min(model.max_completion_tokens, MAX_TOKENS_MAX);
  }

  return Math.min(Math.max(maxTokens, MAX_TOKENS_MIN), max);
}

// ── Context budget ───────────────────────────────────────────────

/** Contract maximum for context_budget_tokens (from frontend_contract.md). */
const CONTEXT_BUDGET_MAX = 2_000_000;

/**
 * Clamp `context_budget_tokens` to a schema-valid range.
 * - Bounds come from `getContextBudgetBounds` (single source in lib/models):
 *   minimum is always 512; a model context below 512 clamps the maximum
 *   up to 512 so the result never drops below the schema minimum.
 * - Maximum is capped at the contract limit 2,000,000, which also applies
 *   when the model context length is unknown.
 * - Returns `undefined` if input is `undefined` or `null`
 */
export function clampContextBudget(
  budget: number | undefined | null,
  model: Pick<Model, "context_length"> | null | undefined,
): number | undefined {
  if (budget == null) return undefined;

  const bounds = getContextBudgetBounds(model);
  const max = Math.min(bounds.max ?? CONTEXT_BUDGET_MAX, CONTEXT_BUDGET_MAX);

  return Math.min(Math.max(budget, bounds.min), max);
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
