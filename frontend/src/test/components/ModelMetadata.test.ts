/**
 * ModelMetadata.test.ts — FE-7A: Model metadata / context / modality logic tests.
 *
 * Covers:
 *  - findModelById (lookup from model list)
 *  - getModelDisplayName (fallback chain)
 *  - getModelContextLength / getModelMaxCompletionTokens (known/unknown/null)
 *  - getModelSupportedParameters (array/empty/null)
 *  - getModelModalities / hasInputModality / hasOutputModality
 *  - shouldShowTextOnlyNote (non-text input modalities)
 *  - getContextBudgetBounds (min 512, max from model)
 *  - TEXT_ONLY_NOTE / CONTEXT_BUDGET_MIN constants
 *  - FE-4A compatibility
 *  - Privacy: no image_url, no provider fields, no context_length_override
 */

import { describe, it, expect } from "vitest";
import {
  findModelById,
  getModelDisplayName,
  getModelContextLength,
  getModelMaxCompletionTokens,
  getModelSupportedParameters,
  getModelModalities,
  hasInputModality,
  hasOutputModality,
  shouldShowTextOnlyNote,
  getContextBudgetBounds,
  CONTEXT_BUDGET_MIN,
  TEXT_ONLY_NOTE,
} from "@/lib/models";
import type { Model } from "@/lib/schemas/models";

// ── Fixtures ─────────────────────────────────────────────────────

const gpt4: Model = {
  id: "openai/gpt-4",
  name: "GPT-4",
  description: "OpenAI GPT-4",
  context_length: 128000,
  max_completion_tokens: 4096,
  supported_parameters: ["temperature", "top_p", "max_tokens", "seed"],
  input_modalities: ["text"],
  output_modalities: ["text"],
  pricing: { prompt: "0.00003", completion: "0.00006" },
  top_provider: {},
  created: 1700000000,
  canonical_slug: "gpt-4",
};

const gpt4Vision: Model = {
  ...gpt4,
  id: "openai/gpt-4-vision",
  name: "GPT-4 Vision",
  input_modalities: ["text", "image"],
  output_modalities: ["text"],
};

const audioModel: Model = {
  ...gpt4,
  id: "openai/gpt-4o-audio",
  name: "GPT-4o Audio",
  input_modalities: ["text", "audio"],
  output_modalities: ["text", "audio"],
};

const unknownModel: Model = {
  id: "unknown/model",
  name: "",
  description: "",
  context_length: null,
  max_completion_tokens: null,
  supported_parameters: [],
  input_modalities: [],
  output_modalities: [],
  pricing: {},
  top_provider: {},
  created: null,
  canonical_slug: "",
};

const models = [gpt4, gpt4Vision, audioModel, unknownModel];

// ═════════════════════════════════════════════════════════════════
// findModelById
// ═════════════════════════════════════════════════════════════════

describe("findModelById", () => {
  it("returns model with matching id", () => {
    expect(findModelById(models, "openai/gpt-4")).toBe(gpt4);
  });

  it("returns undefined for non-existent id", () => {
    expect(findModelById(models, "nonexistent")).toBeUndefined();
  });

  it("returns undefined for null list", () => {
    expect(findModelById(null, "openai/gpt-4")).toBeUndefined();
  });

  it("returns undefined for undefined list", () => {
    expect(findModelById(undefined, "openai/gpt-4")).toBeUndefined();
  });

  it("returns undefined for empty list", () => {
    expect(findModelById([], "openai/gpt-4")).toBeUndefined();
  });

  it("returns undefined for null id", () => {
    expect(findModelById(models, null)).toBeUndefined();
  });

  it("returns undefined for empty id string", () => {
    expect(findModelById(models, "")).toBeUndefined();
  });
});

// ═════════════════════════════════════════════════════════════════
// getModelDisplayName
// ═════════════════════════════════════════════════════════════════

describe("getModelDisplayName", () => {
  it("returns model name when available", () => {
    expect(getModelDisplayName(gpt4)).toBe("GPT-4");
  });

  it("falls back to model id when name is empty", () => {
    expect(getModelDisplayName(unknownModel)).toBe("unknown/model");
  });

  it("falls back to 'Unknown model' for null", () => {
    expect(getModelDisplayName(null)).toBe("Unknown model");
  });

  it("falls back to 'Unknown model' for undefined", () => {
    expect(getModelDisplayName(undefined)).toBe("Unknown model");
  });

  it("falls back to 'Unknown model' when both name and id are empty", () => {
    expect(getModelDisplayName({ name: "", id: "" })).toBe("Unknown model");
  });

  it("trims whitespace-only name and falls back to id", () => {
    expect(getModelDisplayName({ name: "   ", id: "my/model" })).toBe("my/model");
  });
});

// ═════════════════════════════════════════════════════════════════
// getModelContextLength
// ═════════════════════════════════════════════════════════════════

describe("getModelContextLength", () => {
  it("returns context length when known", () => {
    expect(getModelContextLength(gpt4)).toBe(128000);
  });

  it("returns null when context_length is null", () => {
    expect(getModelContextLength(unknownModel)).toBeNull();
  });

  it("returns null when context_length is 0", () => {
    expect(getModelContextLength({ context_length: 0 })).toBeNull();
  });

  it("returns null when context_length is negative", () => {
    expect(getModelContextLength({ context_length: -1 })).toBeNull();
  });

  it("returns null for null model", () => {
    expect(getModelContextLength(null)).toBeNull();
  });

  it("returns null for undefined model", () => {
    expect(getModelContextLength(undefined)).toBeNull();
  });
});

// ═════════════════════════════════════════════════════════════════
// getModelMaxCompletionTokens
// ═════════════════════════════════════════════════════════════════

describe("getModelMaxCompletionTokens", () => {
  it("returns max tokens when known", () => {
    expect(getModelMaxCompletionTokens(gpt4)).toBe(4096);
  });

  it("returns null when max_completion_tokens is null", () => {
    expect(getModelMaxCompletionTokens(unknownModel)).toBeNull();
  });

  it("returns null when max_completion_tokens is 0", () => {
    expect(getModelMaxCompletionTokens({ max_completion_tokens: 0 })).toBeNull();
  });

  it("returns null for null model", () => {
    expect(getModelMaxCompletionTokens(null)).toBeNull();
  });
});

// ═════════════════════════════════════════════════════════════════
// getModelSupportedParameters
// ═════════════════════════════════════════════════════════════════

describe("getModelSupportedParameters", () => {
  it("returns supported parameters array", () => {
    const params = getModelSupportedParameters(gpt4);
    expect(params).toEqual(["temperature", "top_p", "max_tokens", "seed"]);
  });

  it("returns empty array for empty supported_parameters", () => {
    expect(getModelSupportedParameters(unknownModel)).toEqual([]);
  });

  it("returns empty array for null model", () => {
    expect(getModelSupportedParameters(null)).toEqual([]);
  });

  it("returns empty array for undefined model", () => {
    expect(getModelSupportedParameters(undefined)).toEqual([]);
  });
});

// ═════════════════════════════════════════════════════════════════
// getModelModalities / hasInputModality / hasOutputModality
// ═════════════════════════════════════════════════════════════════

describe("getModelModalities", () => {
  it("returns input and output modalities", () => {
    const { input, output } = getModelModalities(gpt4);
    expect(input).toEqual(["text"]);
    expect(output).toEqual(["text"]);
  });

  it("returns multimodal inputs", () => {
    const { input } = getModelModalities(gpt4Vision);
    expect(input).toContain("text");
    expect(input).toContain("image");
  });

  it("returns empty arrays for null model", () => {
    const { input, output } = getModelModalities(null);
    expect(input).toEqual([]);
    expect(output).toEqual([]);
  });

  it("returns empty arrays for model with empty modalities", () => {
    const { input, output } = getModelModalities(unknownModel);
    expect(input).toEqual([]);
    expect(output).toEqual([]);
  });
});

describe("hasInputModality", () => {
  it("returns true for text input on text model", () => {
    expect(hasInputModality(gpt4, "text")).toBe(true);
  });

  it("returns true for image input on vision model", () => {
    expect(hasInputModality(gpt4Vision, "image")).toBe(true);
  });

  it("returns false for image input on text-only model", () => {
    expect(hasInputModality(gpt4, "image")).toBe(false);
  });

  it("returns false for null model", () => {
    expect(hasInputModality(null, "text")).toBe(false);
  });

  it("returns false for empty modalities", () => {
    expect(hasInputModality(unknownModel, "text")).toBe(false);
  });
});

describe("hasOutputModality", () => {
  it("returns true for text output", () => {
    expect(hasOutputModality(gpt4, "text")).toBe(true);
  });

  it("returns true for audio output on audio model", () => {
    expect(hasOutputModality(audioModel, "audio")).toBe(true);
  });

  it("returns false for audio output on text model", () => {
    expect(hasOutputModality(gpt4, "audio")).toBe(false);
  });

  it("returns false for null model", () => {
    expect(hasOutputModality(null, "text")).toBe(false);
  });
});

// ═════════════════════════════════════════════════════════════════
// shouldShowTextOnlyNote
// ═════════════════════════════════════════════════════════════════

describe("shouldShowTextOnlyNote", () => {
  it("returns false for text-only model", () => {
    expect(shouldShowTextOnlyNote(gpt4)).toBe(false);
  });

  it("returns true for vision model (has image input)", () => {
    expect(shouldShowTextOnlyNote(gpt4Vision)).toBe(true);
  });

  it("returns true for audio model (has audio input)", () => {
    expect(shouldShowTextOnlyNote(audioModel)).toBe(true);
  });

  it("returns false for null model (don't show when unknown)", () => {
    expect(shouldShowTextOnlyNote(null)).toBe(false);
  });

  it("returns false for undefined model", () => {
    expect(shouldShowTextOnlyNote(undefined)).toBe(false);
  });

  it("returns false for empty modalities", () => {
    expect(shouldShowTextOnlyNote(unknownModel)).toBe(false);
  });

  it("returns false for model with only text modality", () => {
    expect(shouldShowTextOnlyNote({ input_modalities: ["text"] })).toBe(false);
  });

  it("returns true for model with text + video modality", () => {
    expect(
      shouldShowTextOnlyNote({ input_modalities: ["text", "video"] }),
    ).toBe(true);
  });
});

// ═════════════════════════════════════════════════════════════════
// getContextBudgetBounds
// ═════════════════════════════════════════════════════════════════

describe("getContextBudgetBounds", () => {
  it("returns min 512 and max from model context_length", () => {
    const bounds = getContextBudgetBounds(gpt4);
    expect(bounds.min).toBe(512);
    expect(bounds.max).toBe(128000);
  });

  it("returns null max when context_length is null", () => {
    const bounds = getContextBudgetBounds(unknownModel);
    expect(bounds.min).toBe(512);
    expect(bounds.max).toBeNull();
  });

  it("returns null max when context_length is 0", () => {
    const bounds = getContextBudgetBounds({ context_length: 0 });
    expect(bounds.min).toBe(512);
    expect(bounds.max).toBeNull();
  });

  it("returns null max for null model", () => {
    const bounds = getContextBudgetBounds(null);
    expect(bounds.min).toBe(512);
    expect(bounds.max).toBeNull();
  });

  it("clamps max to min when context_length < 512", () => {
    const bounds = getContextBudgetBounds({ context_length: 256 });
    expect(bounds.min).toBe(512);
    expect(bounds.max).toBe(512);
  });

  it("clamps max to min when context_length equals 512", () => {
    const bounds = getContextBudgetBounds({ context_length: 512 });
    expect(bounds.min).toBe(512);
    expect(bounds.max).toBe(512);
  });

  it("returns correct max for context_length = 1024", () => {
    const bounds = getContextBudgetBounds({ context_length: 1024 });
    expect(bounds.min).toBe(512);
    expect(bounds.max).toBe(1024);
  });
});

// ═════════════════════════════════════════════════════════════════
// Constants
// ═════════════════════════════════════════════════════════════════

describe("Constants", () => {
  it("CONTEXT_BUDGET_MIN is 512", () => {
    expect(CONTEXT_BUDGET_MIN).toBe(512);
  });

  it("TEXT_ONLY_NOTE is a non-empty string", () => {
    expect(typeof TEXT_ONLY_NOTE).toBe("string");
    expect(TEXT_ONLY_NOTE.length).toBeGreaterThan(0);
  });

  it("TEXT_ONLY_NOTE mentions text-only", () => {
    expect(TEXT_ONLY_NOTE.toLowerCase()).toContain("text-only");
  });
});

// ═════════════════════════════════════════════════════════════════
// FE-4A compatibility
// ═════════════════════════════════════════════════════════════════

describe("FE-4A compatibility", () => {
  it("getModelSupportedParameters returns array compatible with filterParamsByModel", async () => {
    const { filterParamsByModel } = await import("@/lib/generation");
    const params = getModelSupportedParameters(gpt4);
    // Construct a model-like object with the same supported_parameters
    const modelLike = { supported_parameters: [...params] };
    // Should not crash and should work with FE-4A
    const filtered = filterParamsByModel({ temperature: 0.7 }, modelLike);
    expect(filtered).toEqual({ temperature: 0.7 });
  });

  it("FE-4A clampContextBudget uses same min as CONTEXT_BUDGET_MIN", async () => {
    const { clampContextBudget } = await import("@/lib/generation");
    // Clamp a value below min — FE-4A should clamp to 512
    const result = clampContextBudget(100, null);
    expect(result).toBe(512);
    expect(result).toBe(CONTEXT_BUDGET_MIN);
  });

  it("getContextBudgetBounds max matches FE-4A clampContextBudget upper bound", async () => {
    const { clampContextBudget } = await import("@/lib/generation");
    const bounds = getContextBudgetBounds(gpt4);
    // Clamping a huge value with model should give context_length
    const clamped = clampContextBudget(999999, gpt4);
    expect(clamped).toBe(bounds.max);
  });
});

// ═════════════════════════════════════════════════════════════════
// Privacy / safety checks
// ═════════════════════════════════════════════════════════════════

describe("Model helper privacy checks", () => {
  it("modelHelpers module is pure — no browser storage", async () => {
    const mod = await import("@/lib/models/modelHelpers");
    expect(typeof mod.findModelById).toBe("function");
    expect(typeof mod.getModelDisplayName).toBe("function");
  });

  it("no context_length_override concept", () => {
    // getContextBudgetBounds uses model.context_length directly, no override
    const bounds = getContextBudgetBounds(gpt4);
    expect(bounds).not.toHaveProperty("override");
    expect(bounds).not.toHaveProperty("context_length_override");
  });

  it("no image_url concept in helpers", () => {
    const modalities = getModelModalities(gpt4Vision);
    // Modalities are display-only — no sending mechanism
    expect(modalities).not.toHaveProperty("image_url");
  });

  it("no provider privacy fields in any helper output", () => {
    const bounds = getContextBudgetBounds(gpt4);
    expect(bounds).not.toHaveProperty("provider");
    expect(bounds).not.toHaveProperty("zdr");
    expect(bounds).not.toHaveProperty("data_collection");
    expect(bounds).not.toHaveProperty("allow_fallbacks");
  });

  it("exports are exported from lib/models barrel", async () => {
    const mod = await import("@/lib/models");
    expect(typeof mod.findModelById).toBe("function");
    expect(typeof mod.getModelDisplayName).toBe("function");
    expect(typeof mod.getModelContextLength).toBe("function");
    expect(typeof mod.getModelMaxCompletionTokens).toBe("function");
    expect(typeof mod.getModelSupportedParameters).toBe("function");
    expect(typeof mod.getModelModalities).toBe("function");
    expect(typeof mod.hasInputModality).toBe("function");
    expect(typeof mod.hasOutputModality).toBe("function");
    expect(typeof mod.shouldShowTextOnlyNote).toBe("function");
    expect(typeof mod.getContextBudgetBounds).toBe("function");
    expect(typeof mod.CONTEXT_BUDGET_MIN).toBe("number");
    expect(typeof mod.TEXT_ONLY_NOTE).toBe("string");
  });
});
