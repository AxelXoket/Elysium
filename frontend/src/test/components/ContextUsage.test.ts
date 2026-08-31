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
  buildPersonaBlock,
  getContextUsageState,
  formatTokensCompact,
} from "@/lib/context";
import {
  CHARS_PER_TOKEN,
  IMAGE_TOKEN_ESTIMATE,
} from "@/lib/context/estimateContextUsage";
import type { Model } from "@/lib/schemas/models";
import type { Character } from "@/lib/schemas/characters";
import type { Persona } from "@/lib/schemas/personas";
import type { Message } from "@/lib/schemas/chats";
import type { ContextUsageEstimate } from "@/lib/context/estimateContextUsage";

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
// persona50: display_name "Test Persona" (12) + description 50 chars
//   persona_block = "[User Persona: Test Persona]\n" (29) + 50 = 79 chars
//   (v1.1 KUME D: the block now carries the NAME header, not just the desc)
// fixed = 116 + 79 + 0 (no post_history_instruction) = 195 chars
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
    // fixed = 195 -> used = ceil(195 / 3) = 65; capacity = 950 (see ctx1200).
    const result = estimateContextUsage({
      model: ctx1200,
      character: character100,
      personas: persona50,
      messages: [],
    });
    expect(result).toMatchObject({
      usedTokens: 73,
      capacityTokens: 950,
      reservedOutputTokens: 100,
      includedMessages: 0,
      droppedMessages: 0,
      totalMessages: 0,
      isEstimate: true,
    });
    // percent = 65 / 950 * 100 = 6.842...
    expect(result!.percent).toBeCloseTo((73 / 950) * 100, 9);
  });

  it("drops the oldest messages until the history fits", () => {
    // available = 2850, fixed = 195 -> remaining = 2655.
    // history = 3 x 1000 = 3000 > 2655 -> drop oldest -> 2000 <= 2655.
    // used = ceil((195 + 2000) / 3) = ceil(2195 / 3) = 732.
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
      usedTokens: 740,
      capacityTokens: 950,
      reservedOutputTokens: 100,
      includedMessages: 2,
      droppedMessages: 1,
      totalMessages: 3,
    });
    expect(result!.percent).toBeCloseTo((740 / 950) * 100, 9);
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
      usedTokens: 740,
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
    // A VISION model, and the modality is stated rather than inherited.
    // `makeModel` defaults `input_modalities` to ["text"], so this fixture
    // was quietly text-only - and the backend charges nothing for an
    // attachment on a model that does not take images. The subject of this
    // test is the 1100-token charge, so the model has to be one that can
    // actually receive the picture.
    const ctx4000 = makeModel({
      id: "test/ctx-4000",
      context_length: 4000,
      max_completion_tokens: 100,
      input_modalities: ["text", "image"],
    });
    const plain = [msg(1, "a".repeat(1000)), msg(2, "b".repeat(1000))];
    // Without attachments: used = ceil((195 + 2000) / 3) = ceil(2195/3) = 732.
    const without = estimateContextUsage({
      model: ctx4000,
      character: character100,
      personas: persona50,
      messages: plain,
    });
    expect(without!.usedTokens).toBe(740);
    expect(without!.capacityTokens).toBe(3644);

    // One attachment adds 1100 * 3 = 3300 chars to that message:
    // used = ceil((195 + 1000 + 1000 + 3300) / 3) = ceil(5495 / 3) = 1832.
    // 1832 - 732 = 1100 tokens, exactly IMAGE_TOKEN_ESTIMATE.
    // On a USER row: those are the only images that get re-sent, so those are
    // the only ones that cost anything. (plain[1] is an assistant row - see the
    // test below.)
    const withImage = [
      { ...plain[0], attachments: [{ id: 7 }] } as Message,
      plain[1],
    ];
    const withAttachment = estimateContextUsage({
      model: ctx4000,
      character: character100,
      personas: persona50,
      messages: withImage,
    });
    expect(withAttachment!.usedTokens).toBe(1840);
    expect(withAttachment!.usedTokens - without!.usedTokens).toBe(1100);
    expect(withAttachment!.includedMessages).toBe(2);
  });

  it("charges nothing for a past image when the model takes no images", () => {
    // The SECOND gate. `_entry_chars` charges an attachment only when
    // `include_images` is true, and `include_images` is
    // `_model_accepts_images(meta)`. Charging 3300 characters on a text-only
    // model reserves room for bytes that are never sent, and the history trim
    // this drives would evict real turns to make it.
    const textOnly = makeModel({
      id: "test/text-only",
      context_length: 4000,
      max_completion_tokens: 100,
      input_modalities: ["text"],
    });
    const plain = [msg(1, "a".repeat(1000)), msg(2, "b".repeat(1000))];
    const withImage = [
      { ...plain[0], attachments: [{ id: 7 }] } as Message,
      plain[1],
    ];

    const without = estimateContextUsage({
      model: textOnly, character: character100, personas: persona50,
      messages: plain,
    });
    const withAttachment = estimateContextUsage({
      model: textOnly, character: character100, personas: persona50,
      messages: withImage,
    });

    expect(withAttachment!.usedTokens - without!.usedTokens).toBe(0);
  });

  it("still charges the image on a model that takes images", () => {
    // GROUND CONTROL for the gate above, with the modality stated rather
    // than absent - the test at the top of this pair leaves it absent, which
    // the backend reads as "unknown, let the provider decide".
    const vision = makeModel({
      id: "test/vision",
      context_length: 4000,
      max_completion_tokens: 100,
      input_modalities: ["text", "image"],
    });
    const plain = [msg(1, "a".repeat(1000)), msg(2, "b".repeat(1000))];
    const withImage = [
      { ...plain[0], attachments: [{ id: 7 }] } as Message,
      plain[1],
    ];

    const without = estimateContextUsage({
      model: vision, character: character100, personas: persona50,
      messages: plain,
    });
    const withAttachment = estimateContextUsage({
      model: vision, character: character100, personas: persona50,
      messages: withImage,
    });

    expect(withAttachment!.usedTokens - without!.usedTokens).toBe(
      IMAGE_TOKEN_ESTIMATE);
  });

  it("still charges the image when the model lists no modalities", () => {
    // The half of the mirror that is easy to get wrong, and the reason
    // `acceptsImages` is not `input_modalities.includes("image")`.
    //
    // `_model_accepts_images` refuses ONLY when metadata POSITIVELY says the
    // model has no image input; unknown or empty metadata is allowed through,
    // because the provider is the final arbiter. Reading it as a plain
    // membership test makes every model with a cold metadata cache look
    // text-only, and the gauge then under-reports on exactly the models it
    // knows least about. Deriving this rule twice, differently, is what once
    // let the attachment gate accept an image that assembly silently dropped.
    const unknown = makeModel({
      id: "test/unknown",
      context_length: 4000,
      max_completion_tokens: 100,
      input_modalities: [],
    });
    const plain = [msg(1, "a".repeat(1000)), msg(2, "b".repeat(1000))];
    const withImage = [
      { ...plain[0], attachments: [{ id: 7 }] } as Message,
      plain[1],
    ];

    const without = estimateContextUsage({
      model: unknown, character: character100, personas: persona50,
      messages: plain,
    });
    const withAttachment = estimateContextUsage({
      model: unknown, character: character100, personas: persona50,
      messages: withImage,
    });

    expect(withAttachment!.usedTokens - without!.usedTokens).toBe(
      IMAGE_TOKEN_ESTIMATE);
  });

  it("charges a message with no attachment the same under either model", () => {
    // POSITIVE CONTROL: the modality gate touches attachments and nothing
    // else. Without this, a fix that shrank every message on a text-only
    // model would pass both tests above.
    const shared = {
      context_length: 4000, max_completion_tokens: 100,
    };
    const textOnly = makeModel({
      ...shared, id: "test/text-only", input_modalities: ["text"] });
    const vision = makeModel({
      ...shared, id: "test/vision", input_modalities: ["text", "image"] });
    const plain = [msg(1, "a".repeat(1000)), msg(2, "b".repeat(1000))];

    const a = estimateContextUsage({
      model: textOnly, character: character100, personas: persona50,
      messages: plain,
    });
    const b = estimateContextUsage({
      model: vision, character: character100, personas: persona50,
      messages: plain,
    });

    expect(a!.usedTokens).toBe(b!.usedTokens);
  });

  it("charges nothing for a picture the model produced", () => {
    // A generated image is display-only: the backend's role gate keeps it out
    // of every later payload and charges 0 budget for it, so charging here
    // would over-report the gauge the moment image output is used. Mirrors
    // completions.py's _entry_chars.
    //
    // A VISION model on purpose. With the default text-only fixture the
    // assistant image would be free for TWO reasons at once, and this test
    // would go green without the role gate doing anything at all.
    const ctx4000 = makeModel({
      id: "test/ctx-4000",
      context_length: 4000,
      max_completion_tokens: 100,
      input_modalities: ["text", "image"],
    });
    const plain = [msg(1, "a".repeat(1000)), msg(2, "b".repeat(1000))];
    const baseline = estimateContextUsage({
      model: ctx4000,
      character: character100,
      personas: persona50,
      messages: plain,
    });
    const withGenerated = estimateContextUsage({
      model: ctx4000,
      character: character100,
      personas: persona50,
      messages: [
        plain[0],
        { ...plain[1], attachments: [{ id: 9 }] } as Message,
      ],
    });
    expect(plain[1].role).toBe("assistant");
    expect(withGenerated!.usedTokens).toBe(baseline!.usedTokens);
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
    // fixed = 195 -> used = ceil(195 / 3) = 65.
    expect(result!.usedTokens).toBe(73);

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
      usedTokens: 214,
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
    expect(result!.usedTokens).toBe(47);
  });

  it("charges a name-only persona for its header (v1.1 KUME D)", () => {
    // display_name "Nova" (4), no description ->
    //   block = "[User Persona: Nova]" = 20 chars.
    // fixed = 116 + 20 = 136 -> used = ceil(136 / 3) = 46 (vs 39 with none).
    const nameOnly = [
      makePersona({ display_name: "Nova", description: "", is_active: true }),
    ];
    const result = estimateContextUsage({
      model: ctx1200,
      character: character100,
      personas: nameOnly,
      messages: [],
    });
    expect(result!.usedTokens).toBe(54);
  });

  it("clamps percent to 100 when even the fixed cost overflows", () => {
    // ctx 600, meta max 100: safety = 75; budget_chars = 1575;
    // reservation 300 -> available = 1275 -> capacity = 425.
    // fixed = 2016 (system block 16 + 2000) + 79 persona = 2095 > available
    // -> every message dropped; used = ceil(2095 / 3) = 699 > 425 -> 100%.
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
      usedTokens: 707,
      capacityTokens: 425,
      includedMessages: 0,
      droppedMessages: 2,
      totalMessages: 2,
      percent: 100,
    });
  });
});

describe("buildSystemBlock", () => {
  it("opens with the character's name, and charges the estimate for it", () => {
    // K-31. The name never reached the model at all - five fields went out
    // and the one word naming who is speaking was not among them.
    //
    // This test exists because closing that record moved a dozen hardcoded
    // token numbers across three test files, and moving numbers is exactly
    // how a mirror drifts unnoticed: nothing in that churn says WHY they
    // moved. So the header is asserted directly, and the estimate is asserted
    // to have grown by it.
    const named = makeCharacter({ name: "Estimator", system_prompt: "SP" });
    const nameless = makeCharacter({ name: "", system_prompt: "SP" });

    expect(buildSystemBlock(named).startsWith("[Character: Estimator]")).toBe(
      true,
    );
    // A blank name emits no empty header, exactly like the persona block's
    // own defensive branch.
    expect(buildSystemBlock(nameless)).toBe("[System Prompt]\nSP");
    // And the difference is the header itself, so the gauge is counting what
    // the provider is actually sent rather than a stale five-field shape.
    expect(
      buildSystemBlock(named).length - buildSystemBlock(nameless).length,
    ).toBe("[Character: Estimator]\n\n".length);
  });


  it("charges the notebook, and charges it from the SERVER's number", () => {
    // The gauge rebuilds almost every other block in TypeScript, and that is
    // how the character header drifted: two languages building one string,
    // undercounting every conversation until somebody noticed. The notebook is
    // measured once, in Python, and carried - so this asserts the number is
    // ADDED, not that it is recomputed correctly here.
    //
    // Untested until an audit pointed it out: both call sites could have been
    // deleted and the whole suite stayed green.
    const base = {
      model: ctx1200,
      character: character100,
      personas: persona50,
      messages: [msg(1, "hi")],
    };
    const withNotes = { ...base, notebookChars: 300 };
    const before = estimateContextUsage(base)!;
    const after = estimateContextUsage(withNotes)!;
    expect(after.usedTokens - before.usedTokens).toBe(300 / 3);
  });

  it("treats a missing or negative count as zero", () => {
    // An older backend sends no field at all, and the schema defaults it -
    // but a gauge that then charged NaN would read as a full context window.
    const base = {
      model: ctx1200,
      character: character100,
      personas: persona50,
      messages: [msg(1, "hi")],
    };
    const plain = estimateContextUsage(base)!;
    for (const notebookChars of [undefined, null, -50]) {
      expect(
        estimateContextUsage({ ...base, notebookChars })!.usedTokens,
      ).toBe(plain.usedTokens);
    }
  });

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
      "[Character: Estimator]\n\n[System Prompt]\nSP\n\n[Personality]\nPE\n\n[Scenario]\nSC\n\n[Example Dialogue]\nME",
    );
  });

  it("feeds the exact fixed cost into the estimate", () => {
    // Sections: "[System Prompt]\nSP" = 18, "[Personality]\nPE" = 16,
    // "[Scenario]\nSC" = 13, "[Example Dialogue]\nME" = 21 -> 68 chars
    // + 3 joins x 2 = 74. Persona (name "Test Persona", " PD " -> "PD"):
    // block = "[User Persona: Test Persona]\nPD" = 31; phi "PH" adds 2.
    // fixed = 74 + 31 + 2 = 107 -> used = ceil(107 / 3) = 36.
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
    expect(result!.usedTokens).toBe(44);
    expect(result!.capacityTokens).toBe(950);
  });
});

describe("buildPersonaBlock", () => {
  it("renders the name header with the description on the next line", () => {
    const p = makePersona({ display_name: "Nova", description: "Likes tea." });
    expect(buildPersonaBlock(p)).toBe("[User Persona: Nova]\nLikes tea.");
  });

  it("injects a header-only block for a name-only persona", () => {
    const p = makePersona({ display_name: "Nova", description: "" });
    expect(buildPersonaBlock(p)).toBe("[User Persona: Nova]");
  });

  it("treats a whitespace-only description as name-only", () => {
    const p = makePersona({ display_name: "Nova", description: "   " });
    expect(buildPersonaBlock(p)).toBe("[User Persona: Nova]");
  });

  it("trims the name and the description", () => {
    const p = makePersona({ display_name: "  Nova  ", description: "  hi  " });
    expect(buildPersonaBlock(p)).toBe("[User Persona: Nova]\nhi");
  });

  it("returns an empty string for no persona", () => {
    expect(buildPersonaBlock(null)).toBe("");
    expect(buildPersonaBlock(undefined)).toBe("");
  });

  it("defensively returns the bare description for a blank name (backend mirror)", () => {
    const p = makePersona({ display_name: "  ", description: "orphaned" });
    expect(buildPersonaBlock(p)).toBe("orphaned");
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


describe("G2: the voice-delivery block in the fixed cost", () => {
  it("charges voicePromptChars exactly like the PHI", () => {
    const model = makeModel({ context_length: 3000 });
    const character = makeCharacter({ system_prompt: "S".repeat(300) });
    const base = estimateContextUsage({
      model,
      character,
      messages: [],
    })!;
    const withVoice = estimateContextUsage({
      model,
      character,
      messages: [],
      voicePromptChars: 3200,
    })!;
    // 3200 chars at 3 chars/token is ~1066-1067 tokens of extra fixed cost
    // (the exact value depends on where the ceil rounding lands relative to
    // the base). What matters: the gauge moves by the block's full weight the
    // moment the toggle would inject it - not by zero, not by an estimate.
    const diff = withVoice.usedTokens - base.usedTokens;
    expect(diff).toBeGreaterThanOrEqual(Math.floor(3200 / 3));
    expect(diff).toBeLessThanOrEqual(Math.ceil(3200 / 3));
  });

  it("an unknown flag charges nothing - matching a backend that cannot inject what it cannot read", () => {
    const model = makeModel({ context_length: 3000 });
    const character = makeCharacter({});
    const a = estimateContextUsage({ model, character, messages: [] })!;
    const b = estimateContextUsage({
      model,
      character,
      messages: [],
      voicePromptChars: null,
    })!;
    expect(b.usedTokens).toBe(a.usedTokens);
  });

  it("the voice block can push history out, exactly like any other fixed cost", () => {
    const model = makeModel({ context_length: 1200 });
    const character = makeCharacter({ system_prompt: "S".repeat(60) });
    const messages = [
      msg(1, "H".repeat(1200)),
      msg(2, "H".repeat(1200)),
    ];
    const without = estimateContextUsage({ model, character, messages })!;
    const withVoice = estimateContextUsage({
      model,
      character,
      messages,
      voicePromptChars: 1500,
    })!;
    // STRICT (audit-2: <= was satisfied by eviction doing nothing at all).
    // The fixture is sized so the block genuinely pushes a message out.
    expect(without.includedMessages).toBeGreaterThan(0);
    expect(withVoice.includedMessages).toBeLessThan(without.includedMessages);
    expect(withVoice.usedTokens).toBeGreaterThan(0);
  });
});

describe("predicting the backend's refusal", () => {
  // completions.py: `min_required = system_chars + user_msg_chars`, and
  // `if min_required > available: raise HTTPException(400, ...)`. The trim
  // loop can drop every message and still be over the line - and it exits
  // declaring the history fitted, so the gauge said "amber" for a request
  // that could not be sent at all.
  //
  // The threshold is built from the estimator's own numbers rather than
  // hand-copied, so the two cannot drift.

  function estimateWith(notebookChars: number, pendingChars = 0) {
    const ctx4000 = makeModel({
      id: "test/ctx-4000",
      context_length: 4000,
      max_completion_tokens: 100,
      input_modalities: ["text", "image"],
    });
    return estimateContextUsage({
      model: ctx4000,
      character: character100,
      personas: persona50,
      messages: [msg(1, "a".repeat(100))],
      notebookChars,
      pendingMessageChars: pendingChars,
    });
  }

  it("says nothing when the turn fits", () => {
    // GROUND CONTROL: without it, a field hard-wired to "context_too_large"
    // passes every assertion below.
    const fits = estimateWith(0);
    expect(fits!.willRefuse).toBeNull();
    expect(fits!.overflowChars).toBe(0);
    expect(fits!.percent).toBeLessThan(100);
  });

  it("refuses when the blocks that cannot shrink exceed the budget", () => {
    // capacityTokens * CHARS_PER_TOKEN is the estimator's own `available`.
    const base = estimateWith(0)!;
    const available = base.capacityTokens * CHARS_PER_TOKEN;

    const over = estimateWith(available + 5000);

    expect(over!.willRefuse).toBe("context_too_large");
    expect(over!.overflowChars).toBeGreaterThan(0);
  });

  /** The smallest value of `arg` for which the estimate refuses.
   *
   * Found by bisection rather than by rebuilding the budget arithmetic in
   * the test. `willRefuse` is monotone in either input - more characters
   * never un-refuses - so the flip point is exact, and nothing here has to
   * retype a formula that could drift away from the one under test.
   */
  function flipPoint(f: (n: number) => ContextUsageEstimate | null): number {
    let lo = 0;
    let hi = 1_000_000;
    expect(f(lo)!.willRefuse).toBeNull();
    expect(f(hi)!.willRefuse).toBe("context_too_large");
    while (lo < hi) {
      const mid = Math.floor((lo + hi) / 2);
      if (f(mid)!.willRefuse) hi = mid;
      else lo = mid + 1;
    }
    return lo;
  }

  it("turns over at exactly one character, not before", () => {
    // POSITIVE CONTROL at the boundary. Without it the field is decorative:
    // any threshold at all satisfies the two tests above.
    const flip = flipPoint((n) => estimateWith(n));

    expect(estimateWith(flip - 1)!.willRefuse).toBeNull();
    expect(estimateWith(flip - 1)!.overflowChars).toBe(0);
    expect(estimateWith(flip)!.willRefuse).toBe("context_too_large");
    expect(estimateWith(flip)!.overflowChars).toBeGreaterThan(0);
  });

  it("grows the overflow by one for each character past the line", () => {
    // The half `percent` cannot carry: it is clamped to 100, so "over by
    // forty" and "over by nine thousand" read identically on the bar.
    const flip = flipPoint((n) => estimateWith(n));

    expect(estimateWith(flip + 100)!.overflowChars
           - estimateWith(flip)!.overflowChars).toBe(100);
  });

  it("counts the outgoing message when the caller supplies it", () => {
    // The backend's `min_required` includes the message being sent. This
    // file deliberately leaves it out of `usedTokens`; it belongs in the
    // refusal threshold, and the default of 0 means a refusal can be missed
    // but never invented.
    const flip = flipPoint((n) => estimateWith(0, n));

    expect(estimateWith(0, flip - 1)!.willRefuse).toBeNull();
    expect(estimateWith(0, flip)!.willRefuse).toBe("context_too_large");
  });
});

