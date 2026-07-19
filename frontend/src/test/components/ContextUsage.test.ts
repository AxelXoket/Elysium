/**
 * ContextUsage.test.ts - unit tests for the live context usage estimator.
 *
 * Every fixture's arithmetic is hand-computed in comments so the backend
 * parity (routers/completions.py + config.py) can be audited:
 *   CHARS_PER_TOKEN = 3, CONTEXT_SAFETY_MARGIN = 256,
 *   _DEFAULT_CONTEXT_LEN = 32000, _DEFAULT_MAX_TOKENS = 2048,
 *   IMAGE_TOKEN_ESTIMATE = 1100 tokens per attachment.
 */

import { describe, it, expect } from "vitest";
import {
  estimateContextUsage,
  buildSystemBlock,
  getContextUsageState,
  formatTokensCompact,
} from "@/lib/context";
import type { Model } from "@/lib/schemas/models";
import type { Character } from "@/lib/schemas/characters";
import type { Persona } from "@/lib/schemas/personas";
import type { Message } from "@/lib/schemas/chats";

// ── Fixture builders ─────────────────────────────────────────────

function makeModel(overrides: Partial<Model>): Model {
  return {
    id: "test/base",
    name: "Base Model",
    description: "",
    context_length: null,
    max_completion_tokens: null,
    supported_parameters: [], // empty = permissive (params pass through)
    input_modalities: ["text"],
    output_modalities: ["text"],
    pricing: {},
    top_provider: {},
    created: null,
    canonical_slug: "test/base",
    ...overrides,
  };
}

function makeCharacter(overrides: Partial<Character>): Character {
  return {
    id: 1,
    name: "Estimator",
    description: "",
    personality: "",
    scenario: "",
    first_mes: "",
    mes_example: "",
    system_prompt: "",
    post_history_instruction: "",
    tags: [],
    created_at: "2026-01-01T00:00:00",
    ...overrides,
  };
}

function makePersona(overrides: Partial<Persona>): Persona {
  return {
    id: 1,
    display_name: "Test Persona",
    description: "",
    is_active: true,
    created_at: "2026-01-01T00:00:00",
    updated_at: "2026-01-01T00:00:00",
    ...overrides,
  };
}

function msg(id: number, content: string): Message {
  return {
    id,
    chat_id: 1,
    role: id % 2 === 1 ? "user" : "assistant",
    content,
    created_at: "2026-01-01T00:00:00",
  };
}

// Shared fixtures:
// character100: system_prompt = 100 chars
//   system_block = "[System Prompt]\n" (16 chars) + 100 = 116 chars
// persona50: description = 50 chars -> persona_block = 50 chars
// fixed = 116 + 50 + 0 (no post_history_instruction) = 166 chars
const character100 = makeCharacter({ system_prompt: "S".repeat(100) });
const persona50 = [makePersona({ description: "P".repeat(50) })];

// ctx1200 model, meta max_completion 100, permissive params:
//   effective = 1200 (no budget)
//   safety = min(256, floor(1200 / 8) = 150) = 150
//   context_budget_chars = (1200 - 150) * 3 = 3150
//   reservation = meta 100 * 3 = 300 <= 3150 -> kept
//   available = 3150 - 300 = 2850 -> capacity = floor(2850 / 3) = 950
const ctx1200 = makeModel({
  id: "test/ctx-1200",
  context_length: 1200,
  max_completion_tokens: 100,
});

describe("estimateContextUsage", () => {
  it("returns null when model, character, or messages are missing", () => {
    const base = {
      model: ctx1200,
      character: character100,
      personas: persona50,
      messages: [msg(1, "hi")],
    };
    expect(estimateContextUsage({ ...base, model: null })).toBeNull();
    expect(estimateContextUsage({ ...base, model: undefined })).toBeNull();
    expect(estimateContextUsage({ ...base, character: null })).toBeNull();
    expect(estimateContextUsage({ ...base, messages: null })).toBeNull();
    expect(estimateContextUsage({ ...base, messages: undefined })).toBeNull();
    // All present -> estimate exists.
    expect(estimateContextUsage(base)).not.toBeNull();
  });

  it("handles an empty history (fixed cost only)", () => {
    // fixed = 166 -> used = ceil(166 / 3) = 56; capacity = 950 (see ctx1200).
    const result = estimateContextUsage({
      model: ctx1200,
      character: character100,
      personas: persona50,
      messages: [],
    });
    expect(result).toMatchObject({
      usedTokens: 56,
      capacityTokens: 950,
      reservedOutputTokens: 100,
      includedMessages: 0,
      droppedMessages: 0,
      totalMessages: 0,
      isEstimate: true,
    });
    // percent = 56 / 950 * 100 = 5.894736...
    expect(result!.percent).toBeCloseTo((56 / 950) * 100, 9);
  });

  it("drops the oldest messages until the history fits", () => {
    // available = 2850, fixed = 166 -> remaining = 2684.
    // history = 3 x 1000 = 3000 > 2684 -> drop oldest -> 2000 <= 2684.
    // used = ceil((166 + 2000) / 3) = ceil(2166 / 3) = 722 (exact).
    // percent = 722 / 950 * 100 = 76 (exact: 950 * 0.76 = 722).
    const result = estimateContextUsage({
      model: ctx1200,
      character: character100,
      personas: persona50,
      messages: [
        msg(1, "a".repeat(1000)),
        msg(2, "b".repeat(1000)),
        msg(3, "c".repeat(1000)),
      ],
    });
    expect(result).toMatchObject({
      usedTokens: 722,
      capacityTokens: 950,
      reservedOutputTokens: 100,
      includedMessages: 2,
      droppedMessages: 1,
      totalMessages: 3,
    });
    expect(result!.percent).toBeCloseTo(76, 9);
  });

  it("excludes inactive variant siblings, mirroring the backend active filter", () => {
    // Same math as the drop-oldest case, but with an INACTIVE variant row
    // of 1000 chars riding along - the backend never sends inactive rows
    // (history queries filter active = 1), so the meter must ignore it too.
    const result = estimateContextUsage({
      model: ctx1200,
      character: character100,
      personas: persona50,
      messages: [
        msg(1, "a".repeat(1000)),
        msg(2, "b".repeat(1000)),
        { ...msg(3, "x".repeat(1000)), active: false, variant_group: 3 },
        { ...msg(4, "c".repeat(1000)), active: true, variant_group: 3 },
      ],
    });
    expect(result).toMatchObject({
      usedTokens: 722,
      includedMessages: 2,
      droppedMessages: 1,
      totalMessages: 3, // active rows only - the hidden sibling is invisible
    });
  });

  it("charges 1100 tokens per image attachment", () => {
    // ctx4000, meta max 100: safety = min(256, 500) = 256
    //   context_budget_chars = (4000 - 256) * 3 = 11232
    //   available = 11232 - 300 = 10932 -> capacity = 3644
    // fixed = 166 -> remaining = 10766; both messages always fit.
    const ctx4000 = makeModel({
      id: "test/ctx-4000",
      context_length: 4000,
      max_completion_tokens: 100,
    });
    const plain = [msg(1, "a".repeat(1000)), msg(2, "b".repeat(1000))];
    // Without attachments: used = ceil((166 + 2000) / 3) = 722.
    const without = estimateContextUsage({
      model: ctx4000,
      character: character100,
      personas: persona50,
      messages: plain,
    });
    expect(without!.usedTokens).toBe(722);
    expect(without!.capacityTokens).toBe(3644);

    // One attachment adds 1100 * 3 = 3300 chars to that message:
    // used = ceil((166 + 1000 + 1000 + 3300) / 3) = ceil(5466 / 3) = 1822.
    // 1822 - 722 = 1100 tokens, exactly IMAGE_TOKEN_ESTIMATE.
    const withImage = [
      plain[0],
      { ...plain[1], attachments: [{ id: 7 }] } as Message,
    ];
    const withAttachment = estimateContextUsage({
      model: ctx4000,
      character: character100,
      personas: persona50,
      messages: withImage,
    });
    expect(withAttachment!.usedTokens).toBe(1822);
    expect(withAttachment!.usedTokens - without!.usedTokens).toBe(1100);
    expect(withAttachment!.includedMessages).toBe(2);
  });

  it("uses min(budget, model context) when a budget is set", () => {
    // Budget 16384, ctx 128000, meta max null -> default 2048:
    //   effective = min(16384, 128000) = 16384; safety = 256
    //   context_budget_chars = (16384 - 256) * 3 = 48384
    //   reservation = 2048 * 3 = 6144 <= 48384 -> kept
    //   available = 48384 - 6144 = 42240 -> capacity = 14080
    const bigModel = makeModel({ id: "test/big", context_length: 128000 });
    const result = estimateContextUsage({
      model: bigModel,
      character: character100,
      personas: persona50,
      messages: [],
      contextBudgetTokens: 16384,
    });
    expect(result!.capacityTokens).toBe(14080);
    expect(result!.reservedOutputTokens).toBe(2048);
    // fixed = 166 -> used = ceil(166 / 3) = 56.
    expect(result!.usedTokens).toBe(56);

    // Budget larger than the model context clamps down to the context:
    // effective = 1200 -> identical numbers to the no-budget ctx1200 case.
    const oversized = estimateContextUsage({
      model: ctx1200,
      character: character100,
      personas: persona50,
      messages: [],
      contextBudgetTokens: 999999,
    });
    expect(oversized!.capacityTokens).toBe(950);
    expect(oversized!.reservedOutputTokens).toBe(100);
  });

  it("falls back to the 32000-token default when context_length is unknown", () => {
    // ctx null -> model_ctx = 32000; budget 40000 -> effective = min(40000, 32000)
    //   = 32000; safety = 256 -> context_budget_chars = 31744 * 3 = 95232
    //   reservation = default 2048 * 3 = 6144 -> available = 89088
    //   capacity = floor(89088 / 3) = 29696
    const unknownCtx = makeModel({ id: "test/unknown" });
    const result = estimateContextUsage({
      model: unknownCtx,
      character: character100,
      personas: persona50,
      messages: [],
      contextBudgetTokens: 40000,
    });
    expect(result!.capacityTokens).toBe(29696);
    expect(result!.reservedOutputTokens).toBe(2048);
  });

  it("halves the output reservation when it exceeds the whole budget", () => {
    // ctx 600, user max_tokens 1000 (permissive model, meta max null):
    //   effective = 600; safety = min(256, floor(600 / 8) = 75) = 75
    //   context_budget_chars = (600 - 75) * 3 = 1575
    //   reservation = 1000 * 3 = 3000 > 1575 -> floor(1575 / 2) = 787
    //   available = 1575 - 787 = 788 -> capacity = floor(788 / 3) = 262
    //   reserved = floor(787 / 3) = 262
    // personas: none active -> persona block "" -> fixed = 116.
    // history = one 500-char message; remaining = 788 - 116 = 672 >= 500.
    // used = ceil((116 + 500) / 3) = ceil(616 / 3) = 206.
    const tiny = makeModel({ id: "test/ctx-600", context_length: 600 });
    const result = estimateContextUsage({
      model: tiny,
      character: character100,
      personas: [],
      messages: [msg(1, "x".repeat(500))],
      generationParams: { max_tokens: 1000 },
    });
    expect(result).toMatchObject({
      usedTokens: 206,
      capacityTokens: 262,
      reservedOutputTokens: 262,
      includedMessages: 1,
      droppedMessages: 0,
      totalMessages: 1,
    });
  });

  it("ignores a user max_tokens the model does not advertise (request parity)", () => {
    // supported_parameters is non-empty and lacks max_tokens, so
    // buildCompletionPayload never sends it -> the backend reserves the
    // model metadata value (100), not the user's 50.
    const strict = makeModel({
      id: "test/strict",
      context_length: 1200,
      max_completion_tokens: 100,
      supported_parameters: ["temperature"],
    });
    const strictResult = estimateContextUsage({
      model: strict,
      character: character100,
      personas: persona50,
      messages: [],
      generationParams: { temperature: 0.8, max_tokens: 50 },
    });
    // Same numbers as ctx1200 with meta reservation: capacity 950.
    expect(strictResult!.reservedOutputTokens).toBe(100);
    expect(strictResult!.capacityTokens).toBe(950);

    // Permissive model: max_tokens 50 IS sent -> reservation = 150 chars,
    // available = 3150 - 150 = 3000 -> capacity = 1000.
    const permissiveResult = estimateContextUsage({
      model: ctx1200,
      character: character100,
      personas: persona50,
      messages: [],
      generationParams: { temperature: 0.8, max_tokens: 50 },
    });
    expect(permissiveResult!.reservedOutputTokens).toBe(50);
    expect(permissiveResult!.capacityTokens).toBe(1000);
  });

  it("omits the persona block when no persona is active", () => {
    // fixed = 116 (system block only) -> used = ceil(116 / 3) = 39.
    const inactive = [makePersona({ description: "P".repeat(50), is_active: false })];
    const result = estimateContextUsage({
      model: ctx1200,
      character: character100,
      personas: inactive,
      messages: [],
    });
    expect(result!.usedTokens).toBe(39);
  });

  it("clamps percent to 100 when even the fixed cost overflows", () => {
    // ctx 600, meta max 100: safety = 75; budget_chars = 1575;
    // reservation 300 -> available = 1275 -> capacity = 425.
    // fixed = 2016 (system block 16 + 2000) + 50 persona = 2066 > available
    // -> every message dropped; used = ceil(2066 / 3) = 689 > 425 -> 100%.
    const tiny = makeModel({
      id: "test/ctx-600-meta",
      context_length: 600,
      max_completion_tokens: 100,
    });
    const bigCharacter = makeCharacter({ system_prompt: "S".repeat(2000) });
    const result = estimateContextUsage({
      model: tiny,
      character: bigCharacter,
      personas: persona50,
      messages: [msg(1, "aaa"), msg(2, "bbb")],
    });
    expect(result).toMatchObject({
      usedTokens: 689,
      capacityTokens: 425,
      includedMessages: 0,
      droppedMessages: 2,
      totalMessages: 2,
      percent: 100,
    });
  });
});

describe("buildSystemBlock", () => {
  it("renders labeled sections in backend order and skips blank ones", () => {
    // Mirrors completions.py _build_system_block: "[Label]\n{value}" joined
    // by "\n\n"; whitespace-only sections (description here) are skipped.
    const character = makeCharacter({
      system_prompt: "SP",
      description: "   ",
      personality: "PE",
      scenario: "SC",
      mes_example: "ME",
    });
    expect(buildSystemBlock(character)).toBe(
      "[System Prompt]\nSP\n\n[Personality]\nPE\n\n[Scenario]\nSC\n\n[Example Dialogue]\nME",
    );
  });

  it("feeds the exact fixed cost into the estimate", () => {
    // Sections: "[System Prompt]\nSP" = 18, "[Personality]\nPE" = 16,
    // "[Scenario]\nSC" = 13, "[Example Dialogue]\nME" = 21 -> 68 chars
    // + 3 joins x 2 = 74. Persona " PD " trims to 2; phi "PH" adds 2.
    // fixed = 74 + 2 + 2 = 78 -> used = ceil(78 / 3) = 26 (exact).
    const character = makeCharacter({
      system_prompt: "SP",
      description: "   ",
      personality: "PE",
      scenario: "SC",
      mes_example: "ME",
      post_history_instruction: "PH",
    });
    const result = estimateContextUsage({
      model: ctx1200,
      character,
      personas: [makePersona({ description: " PD " })],
      messages: [],
    });
    expect(result!.usedTokens).toBe(26);
    expect(result!.capacityTokens).toBe(950);
  });
});

describe("getContextUsageState", () => {
  it("maps percentages to normal / warning / danger", () => {
    expect(getContextUsageState(0)).toBe("normal");
    expect(getContextUsageState(74.99)).toBe("normal");
    expect(getContextUsageState(75)).toBe("warning");
    expect(getContextUsageState(91.99)).toBe("warning");
    expect(getContextUsageState(92)).toBe("danger");
    expect(getContextUsageState(100)).toBe("danger");
  });
});

describe("formatTokensCompact", () => {
  it("renders small values as-is and larger ones in one-decimal K", () => {
    expect(formatTokensCompact(0)).toBe("0");
    expect(formatTokensCompact(950)).toBe("950");
    expect(formatTokensCompact(1000)).toBe("1K");
    expect(formatTokensCompact(1234)).toBe("1.2K");
    expect(formatTokensCompact(8064)).toBe("8.1K");
    expect(formatTokensCompact(30720)).toBe("30.7K");
    expect(formatTokensCompact(128000)).toBe("128K");
  });
});
