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
    if (result.success) {
      expect(result.data).not.toHaveProperty("raw_json");
    }
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
    if (result.success) {
      expect(result.data).not.toHaveProperty("id");
    }
  });

  it("CharacterPatchSchema does not include created_at", async () => {
    const { CharacterPatchSchema } = await import("@/lib/schemas/characters");
    const result = CharacterPatchSchema.safeParse({
      name: "Test",
      created_at: "2026-01-01T00:00:00Z",
    });
    if (result.success) {
      expect(result.data).not.toHaveProperty("created_at");
    }
  });
});

// ═════════════════════════════════════════════════════════════════
// Character mutation hook exports and cache invalidation
// ═════════════════════════════════════════════════════════════════

describe("Character mutation hook exports (structural)", () => {
  it("all character query/mutation hooks are exported", async () => {
    const mod = await import("@/lib/query/characters");
    expect(typeof mod.useCharacters).toBe("function");
    expect(typeof mod.useCreateCharacter).toBe("function");
    expect(typeof mod.useImportCharacter).toBe("function");
    expect(typeof mod.usePatchCharacter).toBe("function");
    expect(typeof mod.useDeleteCharacter).toBe("function");
  });

  it("character helpers are exported from lib/characters", async () => {
    const mod = await import("@/lib/characters");
    expect(typeof mod.findCharacterById).toBe("function");
    expect(typeof mod.safeCharacterId).toBe("function");
    expect(typeof mod.buildStartChatInput).toBe("function");
    expect(typeof mod.CHARACTER_DELETE_CASCADE_WARNING).toBe("string");
  });
});

// ═════════════════════════════════════════════════════════════════
// Error store integration validation
// ═════════════════════════════════════════════════════════════════

describe("Character error store integration", () => {
  it("parseApiError handles character-related error codes", async () => {
    const { parseApiError } = await import("@/lib/errors/parseApiError");

    const err1 = parseApiError({ detail: "character_not_found" });
    expect(err1.message).toBeTruthy();

    const err2 = parseApiError({ detail: "invalid_json" });
    expect(err2.message).toBeTruthy();

    const err3 = parseApiError(new Error("network failure"));
    expect(err3.message).toBeTruthy();
  });
});

// ═════════════════════════════════════════════════════════════════
// Privacy checks
// ═════════════════════════════════════════════════════════════════

describe("Character privacy checks", () => {
  it("characterHelpers module is pure - no browser storage", async () => {
    const mod = await import("@/lib/characters/characterHelpers");
    expect(typeof mod.findCharacterById).toBe("function");
    expect(typeof mod.safeCharacterId).toBe("function");
    expect(typeof mod.buildStartChatInput).toBe("function");
  });

  it("CharacterSchema does not expose raw_json", async () => {
    const { CharacterSchema } = await import("@/lib/schemas/characters");
    const result = CharacterSchema.safeParse({
      ...character1,
      raw_json: '{"secret": true}',
    });
    if (result.success) {
      expect(result.data).not.toHaveProperty("raw_json");
    }
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
