/**
 * GenerationParams.test.ts — FE-4A: Generation parameter utility tests.
 *
 * Covers:
 *  - pruneGenerationParams (known/unknown params, null/undefined pruning, falsy preservation)
 *  - filterParamsByModel (supported_parameters filtering, missing metadata fallback)
 *  - isParamSupportedByModel
 *  - clampMaxTokens
 *  - clampContextBudget
 *  - buildCompletionPayload (privacy, structure, optional field inclusion)
 *  - buildRegeneratePayload (privacy, structure, optional field inclusion)
 */

import { describe, it, expect } from "vitest";
import {
  ALLOWED_GEN_PARAM_KEYS,
  pruneGenerationParams,
  filterParamsByModel,
  isParamSupportedByModel,
  clampMaxTokens,
  clampContextBudget,
  buildCompletionPayload,
  buildRegeneratePayload,
} from "@/lib/generation";

// ── Helper: minimal model metadata ──────────────────────────────

function makeModel(overrides: {
  supported_parameters?: string[];
  context_length?: number | null;
  max_completion_tokens?: number | null;
} = {}) {
  return {
    supported_parameters: overrides.supported_parameters ?? [],
    context_length: overrides.context_length ?? null,
    max_completion_tokens: overrides.max_completion_tokens ?? null,
  };
}

// ═════════════════════════════════════════════════════════════════
// pruneGenerationParams
// ═════════════════════════════════════════════════════════════════

describe("pruneGenerationParams", () => {
  it("returns undefined for null input", () => {
    expect(pruneGenerationParams(null)).toBeUndefined();
  });

  it("returns undefined for undefined input", () => {
    expect(pruneGenerationParams(undefined)).toBeUndefined();
  });

  it("returns undefined for empty object", () => {
    expect(pruneGenerationParams({})).toBeUndefined();
  });

  it("returns undefined when all values are null", () => {
    expect(
      pruneGenerationParams({
        temperature: null,
        top_p: null,
        max_tokens: null,
      }),
    ).toBeUndefined();
  });

  it("returns undefined when all values are undefined", () => {
    expect(
      pruneGenerationParams({
        temperature: undefined,
        top_p: undefined,
      }),
    ).toBeUndefined();
  });

  it("keeps known params with valid values", () => {
    const result = pruneGenerationParams({
      temperature: 0.8,
      max_tokens: 1024,
    });
    expect(result).toEqual({ temperature: 0.8, max_tokens: 1024 });
  });

  it("preserves valid falsy value: temperature=0", () => {
    const result = pruneGenerationParams({ temperature: 0 });
    expect(result).toEqual({ temperature: 0 });
  });

  it("preserves valid falsy value: top_p=0", () => {
    const result = pruneGenerationParams({ top_p: 0 });
    expect(result).toEqual({ top_p: 0 });
  });

  it("preserves valid falsy value: top_k=0", () => {
    const result = pruneGenerationParams({ top_k: 0 });
    expect(result).toEqual({ top_k: 0 });
  });

  it("removes null values while keeping valid ones", () => {
    const result = pruneGenerationParams({
      temperature: 0.5,
      top_p: null,
      max_tokens: 512,
      seed: null,
    });
    expect(result).toEqual({ temperature: 0.5, max_tokens: 512 });
  });

  it("removes unknown/forbidden keys", () => {
    const input = {
      temperature: 0.8,
      provider: { order: ["openai"] },
      zdr: true,
      data_collection: "deny",
      allow_fallbacks: false,
      unknown_field: 42,
    } as Record<string, unknown>;
    const result = pruneGenerationParams(input as any);
    expect(result).toEqual({ temperature: 0.8 });
  });

  it("all six allowed keys are accepted", () => {
    const result = pruneGenerationParams({
      temperature: 1.0,
      top_p: 0.9,
      top_k: 50,
      repetition_penalty: 1.1,
      max_tokens: 2048,
      seed: 42,
    });
    expect(result).toEqual({
      temperature: 1.0,
      top_p: 0.9,
      top_k: 50,
      repetition_penalty: 1.1,
      max_tokens: 2048,
      seed: 42,
    });
  });
});

// ═════════════════════════════════════════════════════════════════
// filterParamsByModel
// ═════════════════════════════════════════════════════════════════

describe("filterParamsByModel", () => {
  it("returns undefined for null params", () => {
    expect(filterParamsByModel(null, makeModel())).toBeUndefined();
  });

  it("passes through all params when model is null (permissive fallback)", () => {
    const result = filterParamsByModel({ temperature: 0.8, top_p: 0.9 }, null);
    expect(result).toEqual({ temperature: 0.8, top_p: 0.9 });
  });

  it("passes through all params when supported_parameters is empty (permissive fallback)", () => {
    const result = filterParamsByModel(
      { temperature: 0.8, max_tokens: 1024 },
      makeModel({ supported_parameters: [] }),
    );
    expect(result).toEqual({ temperature: 0.8, max_tokens: 1024 });
  });

  it("filters to only supported params", () => {
    const result = filterParamsByModel(
      { temperature: 0.8, top_p: 0.9, max_tokens: 1024, seed: 42 },
      makeModel({ supported_parameters: ["temperature", "max_tokens"] }),
    );
    expect(result).toEqual({ temperature: 0.8, max_tokens: 1024 });
  });

  it("returns undefined when no params are supported", () => {
    const result = filterParamsByModel(
      { temperature: 0.8 },
      makeModel({ supported_parameters: ["top_p", "max_tokens"] }),
    );
    expect(result).toBeUndefined();
  });

  it("preserves falsy 0 for supported params", () => {
    const result = filterParamsByModel(
      { temperature: 0, top_k: 0 },
      makeModel({ supported_parameters: ["temperature", "top_k"] }),
    );
    expect(result).toEqual({ temperature: 0, top_k: 0 });
  });
});

// ═════════════════════════════════════════════════════════════════
// isParamSupportedByModel
// ═════════════════════════════════════════════════════════════════

describe("isParamSupportedByModel", () => {
  it("returns true when model is null (permissive fallback)", () => {
    expect(isParamSupportedByModel("temperature", null)).toBe(true);
  });

  it("returns true when supported_parameters is empty (permissive fallback)", () => {
    expect(
      isParamSupportedByModel("temperature", makeModel({ supported_parameters: [] })),
    ).toBe(true);
  });

  it("returns true when param is in supported list", () => {
    expect(
      isParamSupportedByModel(
        "temperature",
        makeModel({ supported_parameters: ["temperature", "max_tokens"] }),
      ),
    ).toBe(true);
  });

  it("returns false when param is not in supported list", () => {
    expect(
      isParamSupportedByModel(
        "seed",
        makeModel({ supported_parameters: ["temperature", "max_tokens"] }),
      ),
    ).toBe(false);
  });
});

// ═════════════════════════════════════════════════════════════════
// clampMaxTokens
// ═════════════════════════════════════════════════════════════════

describe("clampMaxTokens", () => {
  it("returns undefined for null input", () => {
    expect(clampMaxTokens(null, null)).toBeUndefined();
  });

  it("returns undefined for undefined input", () => {
    expect(clampMaxTokens(undefined, null)).toBeUndefined();
  });

  it("returns original value when model is null", () => {
    expect(clampMaxTokens(4096, null)).toBe(4096);
  });

  it("returns original value when max_completion_tokens is null", () => {
    expect(clampMaxTokens(4096, makeModel({ max_completion_tokens: null }))).toBe(4096);
  });

  it("clamps to max_completion_tokens when exceeded", () => {
    expect(clampMaxTokens(8192, makeModel({ max_completion_tokens: 4096 }))).toBe(4096);
  });

  it("preserves value when under max_completion_tokens", () => {
    expect(clampMaxTokens(2048, makeModel({ max_completion_tokens: 4096 }))).toBe(2048);
  });

  it("preserves value when equal to max_completion_tokens", () => {
    expect(clampMaxTokens(4096, makeModel({ max_completion_tokens: 4096 }))).toBe(4096);
  });
});

// ═════════════════════════════════════════════════════════════════
// clampContextBudget
// ═════════════════════════════════════════════════════════════════

describe("clampContextBudget", () => {
  it("returns undefined for null input", () => {
    expect(clampContextBudget(null, null)).toBeUndefined();
  });

  it("returns undefined for undefined input", () => {
    expect(clampContextBudget(undefined, null)).toBeUndefined();
  });

  it("clamps to minimum 512 when value is below", () => {
    expect(clampContextBudget(100, null)).toBe(512);
  });

  it("preserves value at minimum boundary (512)", () => {
    expect(clampContextBudget(512, null)).toBe(512);
  });

  it("preserves value above minimum when no model context", () => {
    expect(clampContextBudget(8192, null)).toBe(8192);
  });

  it("clamps to model context_length when exceeded", () => {
    expect(
      clampContextBudget(200000, makeModel({ context_length: 128000 })),
    ).toBe(128000);
  });

  it("preserves value within range", () => {
    expect(
      clampContextBudget(32000, makeModel({ context_length: 128000 })),
    ).toBe(32000);
  });

  it("clamps to 512 when model context_length is smaller than 512", () => {
    // Edge case: if model reports very small context, min wins
    expect(
      clampContextBudget(100, makeModel({ context_length: 256 })),
    ).toBe(256);
    // But if value is between 256 and 512, max(value, 512) then min(512, 256) = 256
    expect(
      clampContextBudget(400, makeModel({ context_length: 256 })),
    ).toBe(256);
  });

  it("does not place context_budget_tokens inside generation_params", () => {
    // This is a structural test — buildCompletionPayload must put it top-level
    const payload = buildCompletionPayload({
      message: "test",
      modelId: "test-model",
      contextBudgetTokens: 4096,
    });
    expect(payload).toHaveProperty("context_budget_tokens", 4096);
    expect(payload).not.toHaveProperty("generation_params.context_budget_tokens");
  });
});

// ═════════════════════════════════════════════════════════════════
// buildCompletionPayload
// ═════════════════════════════════════════════════════════════════

describe("buildCompletionPayload", () => {
  it("builds minimal payload with just message and model_id", () => {
    const payload = buildCompletionPayload({
      message: "Hello",
      modelId: "openai/gpt-4",
    });
    expect(payload).toEqual({
      message: "Hello",
      model_id: "openai/gpt-4",
    });
  });

  it("includes generation_params when provided", () => {
    const payload = buildCompletionPayload({
      message: "Hello",
      modelId: "openai/gpt-4",
      generationParams: { temperature: 0.8 },
    });
    expect(payload).toEqual({
      message: "Hello",
      model_id: "openai/gpt-4",
      generation_params: { temperature: 0.8 },
    });
  });

  it("omits generation_params when all null", () => {
    const payload = buildCompletionPayload({
      message: "Hello",
      modelId: "openai/gpt-4",
      generationParams: { temperature: null, top_p: null },
    });
    expect(payload).not.toHaveProperty("generation_params");
  });

  it("includes persona_id when provided", () => {
    const payload = buildCompletionPayload({
      message: "Hello",
      modelId: "openai/gpt-4",
      personaId: 5,
    });
    expect(payload.persona_id).toBe(5);
  });

  it("omits persona_id when null", () => {
    const payload = buildCompletionPayload({
      message: "Hello",
      modelId: "openai/gpt-4",
      personaId: null,
    });
    expect(payload).not.toHaveProperty("persona_id");
  });

  it("includes clamped context_budget_tokens", () => {
    const payload = buildCompletionPayload({
      message: "Hello",
      modelId: "openai/gpt-4",
      contextBudgetTokens: 100,
    });
    expect(payload.context_budget_tokens).toBe(512); // clamped to min
  });

  it("clamps max_tokens by model max_completion_tokens", () => {
    const payload = buildCompletionPayload({
      message: "Hello",
      modelId: "openai/gpt-4",
      generationParams: { max_tokens: 10000 },
      model: makeModel({ max_completion_tokens: 4096, supported_parameters: ["max_tokens"] }),
    });
    expect((payload.generation_params as any).max_tokens).toBe(4096);
  });

  it("filters unsupported params by model", () => {
    const payload = buildCompletionPayload({
      message: "Hello",
      modelId: "openai/gpt-4",
      generationParams: { temperature: 0.8, seed: 42 },
      model: makeModel({ supported_parameters: ["temperature"] }),
    });
    expect(payload.generation_params).toEqual({ temperature: 0.8 });
  });

  it("never includes provider privacy fields", () => {
    const payload = buildCompletionPayload({
      message: "Hello",
      modelId: "openai/gpt-4",
      generationParams: {
        temperature: 0.8,
        provider: { order: ["openai"] },
        zdr: true,
        data_collection: "deny",
        allow_fallbacks: false,
      } as any,
    });
    expect(payload).not.toHaveProperty("provider");
    expect(payload).not.toHaveProperty("zdr");
    expect(payload).not.toHaveProperty("data_collection");
    expect(payload).not.toHaveProperty("allow_fallbacks");
    const gp = payload.generation_params as Record<string, unknown> | undefined;
    if (gp) {
      expect(gp).not.toHaveProperty("provider");
      expect(gp).not.toHaveProperty("zdr");
      expect(gp).not.toHaveProperty("data_collection");
      expect(gp).not.toHaveProperty("allow_fallbacks");
    }
  });

  it("never includes model metadata in payload", () => {
    const payload = buildCompletionPayload({
      message: "Hello",
      modelId: "openai/gpt-4",
      model: makeModel({ context_length: 128000, max_completion_tokens: 4096 }),
    });
    expect(payload).not.toHaveProperty("context_length");
    expect(payload).not.toHaveProperty("max_completion_tokens");
    expect(payload).not.toHaveProperty("supported_parameters");
    expect(payload).not.toHaveProperty("model");
  });
});

// ═════════════════════════════════════════════════════════════════
// buildRegeneratePayload
// ═════════════════════════════════════════════════════════════════

describe("buildRegeneratePayload", () => {
  it("builds minimal payload with just model_id", () => {
    const payload = buildRegeneratePayload({ modelId: "openai/gpt-4" });
    expect(payload).toEqual({ model_id: "openai/gpt-4" });
  });

  it("does not include message field", () => {
    const payload = buildRegeneratePayload({ modelId: "openai/gpt-4" });
    expect(payload).not.toHaveProperty("message");
  });

  it("includes generation_params when provided", () => {
    const payload = buildRegeneratePayload({
      modelId: "openai/gpt-4",
      generationParams: { temperature: 0.5 },
    });
    expect(payload.generation_params).toEqual({ temperature: 0.5 });
  });

  it("includes persona_id when provided", () => {
    const payload = buildRegeneratePayload({
      modelId: "openai/gpt-4",
      personaId: 3,
    });
    expect(payload.persona_id).toBe(3);
  });

  it("includes clamped context_budget_tokens", () => {
    const payload = buildRegeneratePayload({
      modelId: "openai/gpt-4",
      contextBudgetTokens: 200000,
      model: makeModel({ context_length: 128000 }),
    });
    expect(payload.context_budget_tokens).toBe(128000);
  });

  it("never includes provider privacy fields", () => {
    const payload = buildRegeneratePayload({
      modelId: "openai/gpt-4",
      generationParams: {
        temperature: 0.8,
        provider: "bad",
        zdr: true,
        data_collection: "deny",
        allow_fallbacks: false,
      } as any,
    });
    expect(payload).not.toHaveProperty("provider");
    expect(payload).not.toHaveProperty("zdr");
    expect(payload).not.toHaveProperty("data_collection");
    expect(payload).not.toHaveProperty("allow_fallbacks");
  });

  it("clamps max_tokens by model max_completion_tokens", () => {
    const payload = buildRegeneratePayload({
      modelId: "openai/gpt-4",
      generationParams: { max_tokens: 50000 },
      model: makeModel({
        max_completion_tokens: 16384,
        supported_parameters: ["max_tokens"],
      }),
    });
    expect((payload.generation_params as any).max_tokens).toBe(16384);
  });
});

// ═════════════════════════════════════════════════════════════════
// ALLOWED_GEN_PARAM_KEYS constant
// ═════════════════════════════════════════════════════════════════

describe("ALLOWED_GEN_PARAM_KEYS", () => {
  it("contains exactly the 6 allowed keys", () => {
    expect(ALLOWED_GEN_PARAM_KEYS.size).toBe(6);
    expect(ALLOWED_GEN_PARAM_KEYS.has("temperature")).toBe(true);
    expect(ALLOWED_GEN_PARAM_KEYS.has("top_p")).toBe(true);
    expect(ALLOWED_GEN_PARAM_KEYS.has("top_k")).toBe(true);
    expect(ALLOWED_GEN_PARAM_KEYS.has("repetition_penalty")).toBe(true);
    expect(ALLOWED_GEN_PARAM_KEYS.has("max_tokens")).toBe(true);
    expect(ALLOWED_GEN_PARAM_KEYS.has("seed")).toBe(true);
  });

  it("does not contain forbidden fields", () => {
    expect(ALLOWED_GEN_PARAM_KEYS.has("provider")).toBe(false);
    expect(ALLOWED_GEN_PARAM_KEYS.has("zdr")).toBe(false);
    expect(ALLOWED_GEN_PARAM_KEYS.has("data_collection")).toBe(false);
    expect(ALLOWED_GEN_PARAM_KEYS.has("allow_fallbacks")).toBe(false);
  });

  it("does not contain context_budget_tokens (it's a top-level field)", () => {
    expect(ALLOWED_GEN_PARAM_KEYS.has("context_budget_tokens")).toBe(false);
  });
});
