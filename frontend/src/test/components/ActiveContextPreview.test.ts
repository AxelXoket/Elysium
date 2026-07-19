/**
 * ActiveContextPreview.test.ts - FE-8A: Active Context Preview logic tests.
 *
 * Covers:
 *  - buildActiveContextPreview: full preview data shape
 *  - Model preview: id, display name, context length, text-only note
 *  - Persona preview: active only, id + name, no description
 *  - Character preview: current only, id + name, no raw_json/avatar/description
 *  - Messages preview: count only, no full content, no drafts
 *  - Generation params preview: sanitized only, no forbidden fields
 *  - Context budget preview: app-level label, no context_length_override
 *  - Not-included items: API key, proxy, drafts, UI state, provider fields, avatar, raw_json
 *  - Approximation disclaimer: exists, does not claim exact payload
 *  - Privacy: no secrets, no storage, no image_url, no provider fields
 */

import { describe, it, expect } from "vitest";
import {
  buildActiveContextPreview,
  NOT_INCLUDED_ITEMS,
  PREVIEW_DISCLAIMER,
} from "@/lib/preview";
import type { Model } from "@/lib/schemas/models";
import type { Persona } from "@/lib/schemas/personas";
import type { Character } from "@/lib/schemas/characters";

// ── Fixtures ─────────────────────────────────────────────────────

const model: Model = {
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

const visionModel: Model = {
  ...model,
  id: "openai/gpt-4-vision",
  name: "GPT-4 Vision",
  input_modalities: ["text", "image"],
};

const activePersona: Persona = {
  id: 1,
  display_name: "Sarcastic",
  description: "Always respond sarcastically.",
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const inactivePersona: Persona = {
  id: 2,
  display_name: "Formal",
  description: "Be very formal.",
  is_active: false,
  created_at: "2026-01-02T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
};

const character: Character = {
  id: 1,
  name: "Aria",
  description: "A helpful assistant.",
  personality: "Friendly and knowledgeable.",
  scenario: "",
  first_mes: "Hello! How can I help?",
  mes_example: "",
  system_prompt: "You are Aria.",
  post_history_instruction: "",
  tags: ["assistant"],
  created_at: "2026-01-01T00:00:00Z",
};

// ═════════════════════════════════════════════════════════════════
// Full preview shape
// ═════════════════════════════════════════════════════════════════

describe("buildActiveContextPreview", () => {
  it("returns a complete preview with all sections", () => {
    const preview = buildActiveContextPreview({
      model,
      personas: [activePersona, inactivePersona],
      character,
      messageCount: 10,
      generationParams: { temperature: 0.7 },
      contextBudgetTokens: 4096,
    });

    expect(preview.included).toBeDefined();
    expect(preview.notIncluded).toBeDefined();
    expect(preview.disclaimer).toBeDefined();
  });

  it("handles completely empty input", () => {
    const preview = buildActiveContextPreview({});
    expect(preview.included.model).toBeNull();
    expect(preview.included.persona).toBeNull();
    expect(preview.included.character).toBeNull();
    expect(preview.included.messages).toBeNull();
    expect(preview.included.generationParams).toBeNull();
    expect(preview.included.contextBudget).toBeNull();
    expect(preview.notIncluded.length).toBeGreaterThan(0);
    expect(preview.disclaimer).toBe(PREVIEW_DISCLAIMER);
  });
});

// ═════════════════════════════════════════════════════════════════
// Model preview
// ═════════════════════════════════════════════════════════════════

describe("Model preview", () => {
  it("includes model id and display name", () => {
    const preview = buildActiveContextPreview({ model });
    expect(preview.included.model?.id).toBe("openai/gpt-4");
    expect(preview.included.model?.displayName).toBe("GPT-4");
  });

  it("includes context length when known", () => {
    const preview = buildActiveContextPreview({ model });
    expect(preview.included.model?.contextLength).toBe(128000);
  });

  it("returns null context length when unknown", () => {
    const noCtx: Model = { ...model, context_length: null };
    const preview = buildActiveContextPreview({ model: noCtx });
    expect(preview.included.model?.contextLength).toBeNull();
  });

  it("does not show text-only note for vision model (images are sent)", () => {
    const preview = buildActiveContextPreview({ model: visionModel });
    expect(preview.included.model?.showTextOnlyNote).toBe(false);
  });

  it("does not show text-only note for text model", () => {
    const preview = buildActiveContextPreview({ model });
    expect(preview.included.model?.showTextOnlyNote).toBe(false);
  });

  it("handles null model safely", () => {
    const preview = buildActiveContextPreview({ model: null });
    expect(preview.included.model).toBeNull();
  });

  it("handles missing model safely", () => {
    const preview = buildActiveContextPreview({});
    expect(preview.included.model).toBeNull();
  });
});

// ═════════════════════════════════════════════════════════════════
// Persona preview
// ═════════════════════════════════════════════════════════════════

describe("Persona preview", () => {
  it("includes only active persona id and name", () => {
    const preview = buildActiveContextPreview({
      personas: [activePersona, inactivePersona],
    });
    expect(preview.included.persona?.id).toBe(1);
    expect(preview.included.persona?.displayName).toBe("Sarcastic");
  });

  it("excludes inactive personas", () => {
    const preview = buildActiveContextPreview({
      personas: [inactivePersona],
    });
    expect(preview.included.persona).toBeNull();
  });

  it("does NOT include persona description", () => {
    const preview = buildActiveContextPreview({
      personas: [activePersona],
    });
    expect(preview.included.persona).not.toHaveProperty("description");
  });

  it("handles null persona list", () => {
    const preview = buildActiveContextPreview({ personas: null });
    expect(preview.included.persona).toBeNull();
  });

  it("handles empty persona list", () => {
    const preview = buildActiveContextPreview({ personas: [] });
    expect(preview.included.persona).toBeNull();
  });
});

// ═════════════════════════════════════════════════════════════════
// Character preview
// ═════════════════════════════════════════════════════════════════

describe("Character preview", () => {
  it("includes only character id and name", () => {
    const preview = buildActiveContextPreview({ character });
    expect(preview.included.character?.id).toBe(1);
    expect(preview.included.character?.name).toBe("Aria");
  });

  it("does NOT include raw_json", () => {
    const preview = buildActiveContextPreview({ character });
    expect(preview.included.character).not.toHaveProperty("raw_json");
  });

  it("does NOT include avatar_path", () => {
    const preview = buildActiveContextPreview({ character });
    expect(preview.included.character).not.toHaveProperty("avatar_path");
  });

  it("does NOT include description", () => {
    const preview = buildActiveContextPreview({ character });
    expect(preview.included.character).not.toHaveProperty("description");
  });

  it("does NOT include personality", () => {
    const preview = buildActiveContextPreview({ character });
    expect(preview.included.character).not.toHaveProperty("personality");
  });

  it("does NOT include system_prompt", () => {
    const preview = buildActiveContextPreview({ character });
    expect(preview.included.character).not.toHaveProperty("system_prompt");
  });

  it("does NOT include first_mes", () => {
    const preview = buildActiveContextPreview({ character });
    expect(preview.included.character).not.toHaveProperty("first_mes");
  });

  it("handles null character", () => {
    const preview = buildActiveContextPreview({ character: null });
    expect(preview.included.character).toBeNull();
  });
});

// ═════════════════════════════════════════════════════════════════
// Messages preview
// ═════════════════════════════════════════════════════════════════

describe("Messages preview", () => {
  it("includes message count", () => {
    const preview = buildActiveContextPreview({ messageCount: 10 });
    expect(preview.included.messages?.count).toBe(10);
  });

  it("formats note for multiple messages", () => {
    const preview = buildActiveContextPreview({ messageCount: 10 });
    expect(preview.included.messages?.note).toContain("10 messages");
  });

  it("formats note for single message", () => {
    const preview = buildActiveContextPreview({ messageCount: 1 });
    expect(preview.included.messages?.note).toContain("1 message");
    expect(preview.included.messages?.note).not.toContain("messages");
  });

  it("formats note for zero messages", () => {
    const preview = buildActiveContextPreview({ messageCount: 0 });
    expect(preview.included.messages?.count).toBe(0);
    expect(preview.included.messages?.note).toContain("No messages");
  });

  it("does NOT include full message content", () => {
    const preview = buildActiveContextPreview({ messageCount: 5 });
    expect(preview.included.messages).not.toHaveProperty("content");
    expect(preview.included.messages).not.toHaveProperty("messages");
  });

  it("does NOT include unsent draft", () => {
    const preview = buildActiveContextPreview({ messageCount: 5 });
    expect(preview.included.messages).not.toHaveProperty("draft");
  });

  it("handles null message count", () => {
    const preview = buildActiveContextPreview({ messageCount: null });
    expect(preview.included.messages).toBeNull();
  });

  it("handles negative message count", () => {
    const preview = buildActiveContextPreview({ messageCount: -1 });
    expect(preview.included.messages).toBeNull();
  });
});

// ═════════════════════════════════════════════════════════════════
// Generation params preview
// ═════════════════════════════════════════════════════════════════

describe("Generation params preview", () => {
  it("includes sanitized params only", () => {
    const preview = buildActiveContextPreview({
      model,
      generationParams: { temperature: 0.7, top_p: 0.9 },
    });
    expect(preview.included.generationParams).toEqual({
      temperature: 0.7,
      top_p: 0.9,
    });
  });

  it("filters unsupported params by model", () => {
    // Model only supports temperature, top_p, max_tokens, seed
    const preview = buildActiveContextPreview({
      model,
      generationParams: { temperature: 0.7, repetition_penalty: 1.2 },
    });
    expect(preview.included.generationParams).toEqual({ temperature: 0.7 });
    expect(preview.included.generationParams).not.toHaveProperty(
      "repetition_penalty",
    );
  });

  it("omits forbidden provider fields", () => {
    const preview = buildActiveContextPreview({
      generationParams: { temperature: 0.7 },
    });
    expect(preview.included.generationParams).not.toHaveProperty("provider");
    expect(preview.included.generationParams).not.toHaveProperty("zdr");
    expect(preview.included.generationParams).not.toHaveProperty(
      "data_collection",
    );
    expect(preview.included.generationParams).not.toHaveProperty(
      "allow_fallbacks",
    );
  });

  it("handles null generation params", () => {
    const preview = buildActiveContextPreview({ generationParams: null });
    expect(preview.included.generationParams).toBeNull();
  });

  it("handles empty generation params (all null values)", () => {
    const preview = buildActiveContextPreview({
      generationParams: { temperature: null },
    });
    expect(preview.included.generationParams).toBeNull();
  });
});

// ═════════════════════════════════════════════════════════════════
// Context budget preview
// ═════════════════════════════════════════════════════════════════

describe("Context budget preview", () => {
  it("includes context budget tokens", () => {
    const preview = buildActiveContextPreview({ contextBudgetTokens: 4096 });
    expect(preview.included.contextBudget?.tokens).toBe(4096);
  });

  it("labels budget as app-level, not provider", () => {
    const preview = buildActiveContextPreview({ contextBudgetTokens: 4096 });
    const label = preview.included.contextBudget?.label ?? "";
    expect(label.toLowerCase()).toContain("app-level");
    expect(label.toLowerCase()).not.toContain("openrouter");
  });

  it("does NOT include context_length_override", () => {
    const preview = buildActiveContextPreview({ contextBudgetTokens: 4096 });
    expect(preview.included.contextBudget).not.toHaveProperty(
      "context_length_override",
    );
  });

  it("handles null context budget", () => {
    const preview = buildActiveContextPreview({ contextBudgetTokens: null });
    expect(preview.included.contextBudget).toBeNull();
  });

  it("handles zero context budget", () => {
    const preview = buildActiveContextPreview({ contextBudgetTokens: 0 });
    expect(preview.included.contextBudget).toBeNull();
  });
});

// ═════════════════════════════════════════════════════════════════
// Not-included items
// ═════════════════════════════════════════════════════════════════

describe("Not-included items", () => {
  it("contains API key", () => {
    expect(NOT_INCLUDED_ITEMS.some((i) => i.toLowerCase().includes("api key"))).toBe(true);
  });

  it("contains proxy URL", () => {
    expect(NOT_INCLUDED_ITEMS.some((i) => i.toLowerCase().includes("proxy"))).toBe(true);
  });

  it("contains drafts", () => {
    expect(NOT_INCLUDED_ITEMS.some((i) => i.toLowerCase().includes("draft"))).toBe(true);
  });

  it("contains UI state", () => {
    expect(NOT_INCLUDED_ITEMS.some((i) => i.toLowerCase().includes("ui state"))).toBe(true);
  });

  it("contains provider privacy fields", () => {
    expect(
      NOT_INCLUDED_ITEMS.some((i) => i.toLowerCase().includes("provider privacy")),
    ).toBe(true);
  });

  it("contains avatar/image data", () => {
    expect(NOT_INCLUDED_ITEMS.some((i) => i.toLowerCase().includes("avatar"))).toBe(true);
  });

  it("contains raw_json", () => {
    expect(NOT_INCLUDED_ITEMS.some((i) => i.toLowerCase().includes("raw_json"))).toBe(true);
  });

  it("contains inactive personas", () => {
    expect(
      NOT_INCLUDED_ITEMS.some((i) => i.toLowerCase().includes("inactive persona")),
    ).toBe(true);
  });

  it("contains inactive characters", () => {
    expect(
      NOT_INCLUDED_ITEMS.some((i) => i.toLowerCase().includes("inactive character")),
    ).toBe(true);
  });

  it("is always present in preview output", () => {
    const preview = buildActiveContextPreview({});
    expect(preview.notIncluded.length).toBeGreaterThan(0);
    expect(preview.notIncluded).toBe(NOT_INCLUDED_ITEMS);
  });
});

// ═════════════════════════════════════════════════════════════════
// Approximation disclaimer
// ═════════════════════════════════════════════════════════════════

describe("PREVIEW_DISCLAIMER", () => {
  it("is a non-empty string", () => {
    expect(typeof PREVIEW_DISCLAIMER).toBe("string");
    expect(PREVIEW_DISCLAIMER.length).toBeGreaterThan(0);
  });

  it("does NOT claim exact OpenRouter payload", () => {
    expect(PREVIEW_DISCLAIMER.toLowerCase()).not.toContain("exact");
  });

  it("mentions approximate/local nature", () => {
    expect(PREVIEW_DISCLAIMER.toLowerCase()).toContain("local");
  });

  it("mentions backend enforcement", () => {
    expect(PREVIEW_DISCLAIMER.toLowerCase()).toContain("backend");
  });

  it("is included in preview output", () => {
    const preview = buildActiveContextPreview({});
    expect(preview.disclaimer).toBe(PREVIEW_DISCLAIMER);
  });
});

// ═════════════════════════════════════════════════════════════════
// Privacy / safety checks
// ═════════════════════════════════════════════════════════════════

describe("Preview privacy checks", () => {
  const fullPreview = buildActiveContextPreview({
    model,
    personas: [activePersona, inactivePersona],
    character,
    messageCount: 10,
    generationParams: { temperature: 0.7 },
    contextBudgetTokens: 4096,
  });

  it("no image_url in preview", () => {
    const str = JSON.stringify(fullPreview);
    expect(str).not.toContain("image_url");
  });

  it("no provider privacy fields in preview", () => {
    const str = JSON.stringify(fullPreview);
    // zdr, data_collection, allow_fallbacks should not appear as keys
    const parsed = JSON.parse(str);
    expect(parsed.included).not.toHaveProperty("zdr");
    expect(parsed.included).not.toHaveProperty("data_collection");
    expect(parsed.included).not.toHaveProperty("allow_fallbacks");
  });

  it("no persona description in preview", () => {
    expect(fullPreview.included.persona).not.toHaveProperty("description");
  });

  it("no character description/personality/system_prompt in preview", () => {
    expect(fullPreview.included.character).not.toHaveProperty("description");
    expect(fullPreview.included.character).not.toHaveProperty("personality");
    expect(fullPreview.included.character).not.toHaveProperty("system_prompt");
    expect(fullPreview.included.character).not.toHaveProperty("first_mes");
  });

  it("no raw_json in character preview", () => {
    expect(fullPreview.included.character).not.toHaveProperty("raw_json");
  });

  it("no avatar_path in character preview", () => {
    expect(fullPreview.included.character).not.toHaveProperty("avatar_path");
  });

  it("no full message content in preview", () => {
    expect(fullPreview.included.messages).not.toHaveProperty("content");
    expect(fullPreview.included.messages).not.toHaveProperty("messages");
  });

  it("preview module exports are available from barrel", async () => {
    const mod = await import("@/lib/preview");
    expect(typeof mod.buildActiveContextPreview).toBe("function");
    expect(Array.isArray(mod.NOT_INCLUDED_ITEMS)).toBe(true);
    expect(typeof mod.PREVIEW_DISCLAIMER).toBe("string");
  });
});
