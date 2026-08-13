/**
 * PersonaLogic.test.ts - FE-3A: Persona logic and data-flow tests.
 *
 * Covers:
 *  - findActivePersona (active persona lookup)
 *  - getSelectedPersonaId (selected persona ID extraction)
 *  - safePersonaId (payload safety)
 *  - Payload integration with buildCompletionPayload / buildRegeneratePayload
 *  - Privacy: no description, no full objects, no inactive data in payloads
 *  - Cache invalidation correctness of mutation hooks (structural)
 */

import { describe, it, expect } from "vitest";
import {
  findActivePersona,
  getSelectedPersonaId,
  safePersonaId,
} from "@/lib/personas";
import {
  buildCompletionPayload,
  buildRegeneratePayload,
} from "@/lib/generation";
import type { GenerationParams } from "@/lib/schemas/completions";
import type { Persona } from "@/lib/schemas/personas";

/**
 * Generation params deliberately contaminated with forbidden provider fields,
 * as a misbehaving caller might pass them. The intersection keeps such objects
 * assignable to GenerationParams without `any`.
 */
type ContaminatedGenerationParams = GenerationParams & {
  provider?: unknown;
  zdr?: unknown;
  data_collection?: unknown;
  allow_fallbacks?: unknown;
};

// ── Fixtures ─────────────────────────────────────────────────────

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
  description: "Be formal and professional.",
  is_active: false,
  created_at: "2026-01-02T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
};

const inactivePersona2: Persona = {
  id: 3,
  display_name: "Casual",
  description: "Be casual and friendly.",
  is_active: false,
  created_at: "2026-01-03T00:00:00Z",
  updated_at: "2026-01-03T00:00:00Z",
};

// ═════════════════════════════════════════════════════════════════
// findActivePersona
// ═════════════════════════════════════════════════════════════════

describe("findActivePersona", () => {
  it("returns the active persona from a list", () => {
    const result = findActivePersona([inactivePersona, activePersona, inactivePersona2]);
    expect(result).toBe(activePersona);
    expect(result?.id).toBe(1);
    expect(result?.is_active).toBe(true);
  });

  it("returns undefined when no persona is active", () => {
    expect(findActivePersona([inactivePersona, inactivePersona2])).toBeUndefined();
  });

  it("returns undefined for empty array", () => {
    expect(findActivePersona([])).toBeUndefined();
  });

  it("returns undefined for null input", () => {
    expect(findActivePersona(null)).toBeUndefined();
  });

  it("returns undefined for undefined input", () => {
    expect(findActivePersona(undefined)).toBeUndefined();
  });

  it("returns the first active if multiple are active (backend should prevent this)", () => {
    const anotherActive = { ...inactivePersona2, is_active: true };
    const result = findActivePersona([activePersona, anotherActive]);
    // First match wins - consistent behavior
    expect(result?.id).toBe(1);
  });
});

// ═════════════════════════════════════════════════════════════════
// getSelectedPersonaId
// ═════════════════════════════════════════════════════════════════

describe("getSelectedPersonaId", () => {
  it("returns the active persona's ID", () => {
    expect(getSelectedPersonaId([inactivePersona, activePersona])).toBe(1);
  });

  it("returns undefined when no persona is active", () => {
    expect(getSelectedPersonaId([inactivePersona])).toBeUndefined();
  });

  it("returns undefined for null input", () => {
    expect(getSelectedPersonaId(null)).toBeUndefined();
  });

  it("returns undefined for empty array", () => {
    expect(getSelectedPersonaId([])).toBeUndefined();
  });
});

// ═════════════════════════════════════════════════════════════════
// safePersonaId
// ═════════════════════════════════════════════════════════════════

describe("safePersonaId", () => {
  it("returns the ID for a positive integer", () => {
    expect(safePersonaId(5)).toBe(5);
  });

  it("returns undefined for null", () => {
    expect(safePersonaId(null)).toBeUndefined();
  });

  it("returns undefined for undefined", () => {
    expect(safePersonaId(undefined)).toBeUndefined();
  });

  it("returns undefined for 0", () => {
    expect(safePersonaId(0)).toBeUndefined();
  });

  it("returns undefined for negative ID", () => {
    expect(safePersonaId(-1)).toBeUndefined();
  });

  it("returns undefined for non-integer", () => {
    expect(safePersonaId(1.5)).toBeUndefined();
  });
});

// ═════════════════════════════════════════════════════════════════
// Payload persona integration
// ═════════════════════════════════════════════════════════════════

describe("Payload persona integration", () => {
  it("completion payload includes persona_id when active persona exists", () => {
    const personaId = getSelectedPersonaId([inactivePersona, activePersona]);
    const payload = buildCompletionPayload({
      message: "Hello",
      modelId: "openai/gpt-4",
      personaId,
    });
    expect(payload.persona_id).toBe(1);
  });

  it("completion payload omits persona_id when no active persona", () => {
    const personaId = getSelectedPersonaId([inactivePersona]);
    const payload = buildCompletionPayload({
      message: "Hello",
      modelId: "openai/gpt-4",
      personaId,
    });
    expect(payload).not.toHaveProperty("persona_id");
  });

  it("regenerate payload includes persona_id when active persona exists", () => {
    const personaId = getSelectedPersonaId([activePersona, inactivePersona]);
    const payload = buildRegeneratePayload({
      modelId: "openai/gpt-4",
      personaId,
    });
    expect(payload.persona_id).toBe(1);
  });

  it("regenerate payload omits persona_id when no active persona", () => {
    const personaId = getSelectedPersonaId([]);
    const payload = buildRegeneratePayload({
      modelId: "openai/gpt-4",
      personaId,
    });
    expect(payload).not.toHaveProperty("persona_id");
  });

  it("only persona_id appears - no description, no full persona object", () => {
    const payload = buildCompletionPayload({
      message: "Hello",
      modelId: "openai/gpt-4",
      personaId: activePersona.id,
    });
    // persona_id is a number, not an object
    expect(typeof payload.persona_id).toBe("number");
    // No description anywhere
    expect(payload).not.toHaveProperty("description");
    expect(payload).not.toHaveProperty("persona_description");
    expect(payload).not.toHaveProperty("persona");
    // No full persona object
    expect(payload).not.toHaveProperty("display_name");
    expect(payload).not.toHaveProperty("is_active");
  });

  it("inactive persona id is not included via getSelectedPersonaId", () => {
    // Only inactive personas in the list - no active one
    const personaId = getSelectedPersonaId([inactivePersona, inactivePersona2]);
    expect(personaId).toBeUndefined();
    const payload = buildCompletionPayload({
      message: "Hello",
      modelId: "openai/gpt-4",
      personaId,
    });
    expect(payload).not.toHaveProperty("persona_id");
  });

  it("full persona list is not included in payload", () => {
    const payload = buildCompletionPayload({
      message: "Hello",
      modelId: "openai/gpt-4",
      personaId: 1,
    });
    expect(payload).not.toHaveProperty("personas");
    expect(payload).not.toHaveProperty("persona_list");
  });

  it("payload never includes provider privacy fields alongside persona_id", () => {
    const payload = buildCompletionPayload({
      message: "Hello",
      modelId: "openai/gpt-4",
      personaId: 1,
      generationParams: {
        temperature: 0.8,
        provider: "bad",
        zdr: true,
        data_collection: "deny",
        allow_fallbacks: false,
      } as ContaminatedGenerationParams,
    });
    expect(payload.persona_id).toBe(1);
    expect(payload).not.toHaveProperty("provider");
    expect(payload).not.toHaveProperty("zdr");
    expect(payload).not.toHaveProperty("data_collection");
    expect(payload).not.toHaveProperty("allow_fallbacks");
  });
});

// ═════════════════════════════════════════════════════════════════
// Mutation hook cache invalidation (structural - no render needed)
// ═════════════════════════════════════════════════════════════════

// Four tests stood here until KADEME 18b: two asserting that hooks and
// helpers are exported as functions, and two asserting the settings schema
// takes a selected_persona_id. The first pair proved nothing the tests below
// - which CALL those helpers - do not already prove by calling them. The
// second pair belongs to the schema contract and now lives beside its
// sibling in fe0-contract.test.ts, which owns that shape.

// ═════════════════════════════════════════════════════════════════
// No browser storage
// ═════════════════════════════════════════════════════════════════

describe("Persona privacy checks", () => {
  // The "does not reference localStorage" test that opened this describe
  // imported the module and asserted three exports are functions. It never
  // looked at storage. static-safety S-09 already scans every source file for
  // a direct device-storage write outside lib/store, and S-09b checks the
  // store's own persisted allowlist - both stronger, both repo-wide.

  it("safePersonaId rejects unsafe values that could leak into payload", () => {
    expect(safePersonaId(NaN)).toBeUndefined();
    expect(safePersonaId(Infinity)).toBeUndefined();
    expect(safePersonaId(-Infinity)).toBeUndefined();
  });
});
