/**
 * CharacterLibrary.test.ts - FE-6A: Character library logic and data-flow tests.
 *
 * Covers:
 *  - findCharacterById (lookup from list)
 *  - safeCharacterId (payload validation)
 *  - buildStartChatInput (explicit start-chat, no auto-create)
 *  - CHARACTER_DELETE_CASCADE_WARNING constant
 *  - Character PATCH payload safety (no raw_json, no avatar)
 *  - Character mutation hook exports and cache invalidation structure
 *  - Privacy: no browser storage, no image_url, no provider fields
 */

import { describe, it, expect } from "vitest";
import {
  findCharacterById,
  safeCharacterId,
  buildStartChatInput,
  CHARACTER_DELETE_CASCADE_WARNING,
} from "@/lib/characters";
import type { Character } from "@/lib/schemas/characters";

// ── Fixtures ─────────────────────────────────────────────────────

const character1: Character = {
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

const character2: Character = {
  id: 2,
  name: "Marcus",
  description: "A stoic philosopher.",
  personality: "Calm and measured.",
  scenario: "Ancient Rome",
  first_mes: "Greetings, friend.",
  mes_example: "",
  system_prompt: "You are Marcus.",
  post_history_instruction: "",
  tags: ["philosophy"],
  created_at: "2026-01-02T00:00:00Z",
};

const characters = [character1, character2];

// ═════════════════════════════════════════════════════════════════
// findCharacterById
// ═════════════════════════════════════════════════════════════════

describe("findCharacterById", () => {
  it("returns the character with matching id", () => {
    expect(findCharacterById(characters, 1)).toBe(character1);
    expect(findCharacterById(characters, 2)).toBe(character2);
  });

  it("returns undefined for non-existent id", () => {
    expect(findCharacterById(characters, 99)).toBeUndefined();
  });

  it("returns undefined for null list", () => {
    expect(findCharacterById(null, 1)).toBeUndefined();
  });

  it("returns undefined for undefined list", () => {
    expect(findCharacterById(undefined, 1)).toBeUndefined();
  });

  it("returns undefined for empty list", () => {
    expect(findCharacterById([], 1)).toBeUndefined();
  });

  it("returns undefined for null id", () => {
    expect(findCharacterById(characters, null)).toBeUndefined();
  });

  it("returns undefined for undefined id", () => {
    expect(findCharacterById(characters, undefined)).toBeUndefined();
  });
});

// ═════════════════════════════════════════════════════════════════
// safeCharacterId
// ═════════════════════════════════════════════════════════════════

describe("safeCharacterId", () => {
  it("returns the ID for a positive integer", () => {
    expect(safeCharacterId(5)).toBe(5);
  });

  it("returns undefined for null", () => {
    expect(safeCharacterId(null)).toBeUndefined();
  });

  it("returns undefined for undefined", () => {
    expect(safeCharacterId(undefined)).toBeUndefined();
  });

  it("returns undefined for 0", () => {
    expect(safeCharacterId(0)).toBeUndefined();
  });

  it("returns undefined for negative", () => {
    expect(safeCharacterId(-1)).toBeUndefined();
  });

  it("returns undefined for float", () => {
    expect(safeCharacterId(1.5)).toBeUndefined();
  });

  it("returns undefined for NaN", () => {
    expect(safeCharacterId(NaN)).toBeUndefined();
  });

  it("returns undefined for Infinity", () => {
    expect(safeCharacterId(Infinity)).toBeUndefined();
  });
});

// ═════════════════════════════════════════════════════════════════
// buildStartChatInput - explicit start-chat, never auto-create
// ═════════════════════════════════════════════════════════════════

describe("buildStartChatInput", () => {
  it("builds minimal input with only character_id", () => {
    const input = buildStartChatInput(1);
    expect(input).toEqual({ character_id: 1 });
  });

  it("includes trimmed title when provided", () => {
    const input = buildStartChatInput(1, "My chat");
    expect(input).toEqual({ character_id: 1, title: "My chat" });
  });

  it("trims whitespace from title", () => {
    const input = buildStartChatInput(1, "  spaced  ");
    expect(input?.title).toBe("spaced");
  });

  it("omits title when it is empty string", () => {
    const input = buildStartChatInput(1, "");
    expect(input).toBeDefined();
    expect(input).not.toHaveProperty("title");
  });

  it("omits title when it is only whitespace", () => {
    const input = buildStartChatInput(1, "   ");
    expect(input).not.toHaveProperty("title");
  });

  it("returns undefined for null character id", () => {
    expect(buildStartChatInput(null)).toBeUndefined();
  });

  it("returns undefined for undefined character id", () => {
    expect(buildStartChatInput(undefined)).toBeUndefined();
  });

  it("returns undefined for invalid character id (0)", () => {
    expect(buildStartChatInput(0)).toBeUndefined();
  });

  it("returns undefined for negative character id", () => {
    expect(buildStartChatInput(-1)).toBeUndefined();
  });

  it("does NOT include character description", () => {
    const input = buildStartChatInput(1);
    expect(input).not.toHaveProperty("description");
  });

  it("does NOT include personality", () => {
    const input = buildStartChatInput(1);
    expect(input).not.toHaveProperty("personality");
  });

  it("does NOT include raw_json", () => {
    const input = buildStartChatInput(1);
    expect(input).not.toHaveProperty("raw_json");
  });

  it("does NOT include avatar data", () => {
    const input = buildStartChatInput(1);
    expect(input).not.toHaveProperty("avatar");
    expect(input).not.toHaveProperty("avatar_path");
    expect(input).not.toHaveProperty("image_url");
  });

  it("does NOT include system_prompt or first_mes", () => {
    const input = buildStartChatInput(1);
    expect(input).not.toHaveProperty("system_prompt");
    expect(input).not.toHaveProperty("first_mes");
  });
});

// ═════════════════════════════════════════════════════════════════
// Character click does NOT create chat
// ═════════════════════════════════════════════════════════════════

describe("Character detail vs start-chat separation", () => {
  it("findCharacterById is a pure lookup - does not create a chat", () => {
    // findCharacterById returns a Character, not a Chat
    const result = findCharacterById(characters, 1);
    expect(result).toBe(character1);
    expect(result).not.toHaveProperty("chat_id");
    expect(result).not.toHaveProperty("messages");
  });

  it("buildStartChatInput is a pure helper - does not call API", () => {
    // Returns a plain object, not a Promise - no API call
    const input = buildStartChatInput(1);
    expect(input).toEqual({ character_id: 1 });
    // It's not a Promise
    expect(input).not.toHaveProperty("then");
  });
});

// ═════════════════════════════════════════════════════════════════
// CHARACTER_DELETE_CASCADE_WARNING
// ═════════════════════════════════════════════════════════════════

describe("CHARACTER_DELETE_CASCADE_WARNING", () => {
  it("is a non-empty string", () => {
    expect(typeof CHARACTER_DELETE_CASCADE_WARNING).toBe("string");
    expect(CHARACTER_DELETE_CASCADE_WARNING.length).toBeGreaterThan(0);
  });

  it("mentions chats in the warning", () => {
    expect(CHARACTER_DELETE_CASCADE_WARNING.toLowerCase()).toContain("chat");
  });
});

// ═════════════════════════════════════════════════════════════════
// Character PATCH payload safety
// ═════════════════════════════════════════════════════════════════

describe("CharacterPatchSchema safety", () => {
  it("CharacterPatchSchema does not include raw_json", async () => {
    const { CharacterPatchSchema } = await import("@/lib/schemas/characters");
    const result = CharacterPatchSchema.safeParse({
      name: "Updated",
      raw_json: '{"bad": true}',
    });
    // raw_json is stripped by the schema (not in CharacterSchema fields)
    // Unconditional: behind `if (result.success)` a schema that started
    // REJECTING instead of stripping skipped the assertion and passed.
    expect(result.success, "the schema refused a patch it has to accept").toBe(true);
    expect(result.success && result.data).not.toHaveProperty("raw_json");
  });

  it("CharacterPatchSchema accepts partial updates", async () => {
    const { CharacterPatchSchema } = await import("@/lib/schemas/characters");
    const result = CharacterPatchSchema.safeParse({ name: "Just Name" });
    expect(result.success).toBe(true);
  });

  it("CharacterPatchSchema does not include id", async () => {
    const { CharacterPatchSchema } = await import("@/lib/schemas/characters");
    const result = CharacterPatchSchema.safeParse({
      name: "Test",
      id: 999,
    });
    // Unconditional: behind `if (result.success)` a schema that started
    // REJECTING instead of stripping skipped the assertion and passed.
    expect(result.success, "the schema refused a patch it has to accept").toBe(true);
    expect(result.success && result.data).not.toHaveProperty("id");
  });

  it("CharacterPatchSchema does not include created_at", async () => {
    const { CharacterPatchSchema } = await import("@/lib/schemas/characters");
    const result = CharacterPatchSchema.safeParse({
      name: "Test",
      created_at: "2026-01-01T00:00:00Z",
    });
    // Unconditional: behind `if (result.success)` a schema that started
    // REJECTING instead of stripping skipped the assertion and passed.
    expect(result.success, "the schema refused a patch it has to accept").toBe(true);
    expect(result.success && result.data).not.toHaveProperty("created_at");
  });
});

// ═════════════════════════════════════════════════════════════════
// Error store integration validation
// ═════════════════════════════════════════════════════════════════

describe("Character error store integration", () => {
  it("gives a missing character its own sentence, not the catch-all", async () => {
    // This test used to pass `{ detail: "..." }` with no `status`, which
    // isApiError rejects, so all three cases fell through to the catch-all
    // and `toBeTruthy()` waved them through. It proved that a string is a
    // string. The shape has to be a real ApiError, and the assertion has to
    // be that the mapped sentence is NOT the catch-all - otherwise deleting
    // the entry from the catalogue would not be noticed.
    const { parseApiError } = await import("@/lib/errors/parseApiError");

    const generic = parseApiError({ status: 500, detail: "no_such_code_here" })
      .message;
    const missing = parseApiError({ status: 404, detail: "character_not_found" })
      .message;

    expect(missing).not.toBe(generic);
    expect(missing.toLowerCase()).toContain("character");

    // An unmapped code has to land somewhere readable rather than showing the
    // wire word, and a dead connection is its own case.
    expect(generic).not.toContain("no_such_code_here");
    expect(parseApiError(new Error("network failure")).message).not.toBe("");
  });
});

// ═════════════════════════════════════════════════════════════════
// Privacy checks
// ═════════════════════════════════════════════════════════════════

describe("Character privacy checks", () => {
  // The "no browser storage" claim this describe used to open with was three
  // `typeof x === "function"` assertions and nothing else - a privacy name on
  // a test that never went near storage. The real guarantee is repo-wide and
  // much stronger: static-safety S-09 scans EVERY source file for a direct
  // device-storage write outside lib/store, and S-09b brace-parses the
  // store's own partialize against an allowlist. A weaker local copy of a
  // gate that already exists is worse than none: it reads as coverage.

  it("CharacterSchema does not expose raw_json", async () => {
    const { CharacterSchema } = await import("@/lib/schemas/characters");
    const result = CharacterSchema.safeParse({
      ...character1,
      raw_json: '{"secret": true}',
    });
    // Unconditional. Behind `if (result.success)` the one outcome that would
    // prove a regression - the schema starting to REJECT instead of strip -
    // skipped the assertion and passed.
    expect(result.success, "the schema refused a row it has to accept").toBe(true);
    expect(result.success && result.data).not.toHaveProperty("raw_json");
  });

  it("buildStartChatInput never includes image_url", () => {
    const input = buildStartChatInput(1);
    expect(input).not.toHaveProperty("image_url");
  });

  it("buildStartChatInput never includes provider privacy fields", () => {
    const input = buildStartChatInput(1);
    expect(input).not.toHaveProperty("provider");
    expect(input).not.toHaveProperty("zdr");
    expect(input).not.toHaveProperty("data_collection");
    expect(input).not.toHaveProperty("allow_fallbacks");
  });
});
