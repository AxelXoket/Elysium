import { afterEach, describe, expect, it, vi } from "vitest";
import {
  completeChat,
  regenerateMessage,
} from "@/lib/api/completions";
import {
  clearChat,
  deleteChat,
  deleteMessageAndFollowing,
} from "@/lib/api/chats";
import {
  deleteCharacter,
  patchCharacter,
} from "@/lib/api/characters";
import {
  createPersona,
  deletePersona,
  listPersonas,
  patchPersona,
  selectPersona,
} from "@/lib/api/personas";
import { setApiKey } from "@/lib/api/settings";
import {
  ApiKeySaveResponseSchema,
  SettingsSchema,
} from "@/lib/schemas/settings";
import { PersonaSchema } from "@/lib/schemas/personas";
import { DeletedCountResponseSchema, MessageSchema } from "@/lib/schemas/chats";
import {
  CompletionRequestSchema,
  RegenerateRequestSchema,
} from "@/lib/schemas/completions";
import { completionFixture, personaFixture } from "./mocks/fixtures";
import { mockFetch } from "./mocks/api";

describe("FE-0 contract foundation", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("accepts settings selected_persona_id", () => {
    expect(
      SettingsSchema.parse({
        api_key_set: true,
        proxy_required: false,
        proxy_configured: true,
        proxy_alias: "local",
        selected_persona_id: 7,
      }).selected_persona_id,
    ).toBe(7);
  });

  it("accepts API key valid and validation_unavailable responses", async () => {
    expect(
      ApiKeySaveResponseSchema.parse({
        ok: true,
        key_status: "valid",
      }).key_status,
    ).toBe("valid");
    expect(
      ApiKeySaveResponseSchema.parse({
        ok: false,
        key_status: "validation_unavailable",
      }).key_status,
    ).toBe("validation_unavailable");

    mockFetch({
      "/settings/api-key": {
        body: { ok: false, key_status: "validation_unavailable" },
      },
    });

    await expect(setApiKey("sk-or-v1-test")).resolves.toEqual({
      ok: false,
      key_status: "validation_unavailable",
    });
  });

  it("persona schema includes backend-derived is_active", () => {
    expect(PersonaSchema.parse(personaFixture).is_active).toBe(true);
  });

  it("persona API functions use backend base client paths", async () => {
    const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      let body: unknown = personaFixture;

      if (url.endsWith("/personas") && method === "GET") {
        body = [personaFixture];
      } else if (url.endsWith("/personas/1/select")) {
        body = { ok: true, selected_persona_id: 1 };
      } else if (url.endsWith("/personas/1") && method === "DELETE") {
        body = { ok: true };
      }

      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", mock);

    await listPersonas();
    await createPersona({ display_name: "New", description: "Desc" });
    await patchPersona(1, { description: "Changed" });
    await selectPersona(1);
    await deletePersona(1);

    const urls = mock.mock.calls.map((call) => String(call[0]));
    expect(urls.every((url) => url.startsWith("http://127.0.0.1:8787/api/v1")))
      .toBe(true);
    expect(urls).toContain("http://127.0.0.1:8787/api/v1/personas");
    expect(urls).toContain("http://127.0.0.1:8787/api/v1/personas/1/select");
    expect(mock.mock.calls[4][1]?.method).toBe("DELETE");
  });

  it("completion request can include optional contract fields", async () => {
    const mock = mockFetch({
      "/chats/1/complete": { body: completionFixture },
    });

    await completeChat(1, {
      message: "Hello",
      model_id: "openai/gpt-4o",
      generation_params: {
        temperature: 0.7,
        top_p: 0.9,
        top_k: 40,
        repetition_penalty: 1.05,
        max_tokens: 512,
        seed: 123,
      },
      persona_id: 2,
      context_budget_tokens: 4096,
    });

    const body = JSON.parse(mock.mock.calls[0][1]?.body as string);
    expect(body.generation_params).toEqual({
      temperature: 0.7,
      top_p: 0.9,
      top_k: 40,
      repetition_penalty: 1.05,
      max_tokens: 512,
      seed: 123,
    });
    expect(body.persona_id).toBe(2);
    expect(body.context_budget_tokens).toBe(4096);
    expect(body).not.toHaveProperty("provider");
    expect(body).not.toHaveProperty("zdr");
    expect(body).not.toHaveProperty("data_collection");
    expect(body).not.toHaveProperty("allow_fallbacks");
  });

  it("regenerate request matches completion response shape without provider fields", async () => {
    const mock = mockFetch({
      "/chats/1/messages/3/regenerate": { body: completionFixture },
    });

    const result = await regenerateMessage(1, 3, {
      model_id: "openai/gpt-4o",
      generation_params: { max_tokens: 256 },
      persona_id: null,
      context_budget_tokens: null,
    });

    const body = JSON.parse(mock.mock.calls[0][1]?.body as string);
    expect(result.user_message.id).toBe(completionFixture.user_message.id);
    expect(result.assistant_message.id).toBe(
      completionFixture.assistant_message.id,
    );
    expect(body).not.toHaveProperty("provider");
    expect(body).not.toHaveProperty("zdr");
    expect(body).not.toHaveProperty("data_collection");
    expect(body).not.toHaveProperty("allow_fallbacks");
  });

  it("message rows parse attachments with an empty-array default", () => {
    const base = {
      id: 1,
      chat_id: 1,
      role: "user",
      content: "hi",
      created_at: "2026-01-01T00:00:00",
    };

    // Rows without the key (older payloads, optimistic entries) default to []
    expect(MessageSchema.parse(base).attachments).toEqual([]);

    // Rows with attachment metadata round-trip
    const withImages = MessageSchema.parse({
      ...base,
      attachments: [{ id: 9, mime: "image/png", width: 640, height: 480 }],
    });
    expect(withImages.attachments).toEqual([
      { id: 9, mime: "image/png", width: 640, height: 480 },
    ]);
  });

  it("completion request accepts at most 4 positive attachment ids", () => {
    const base = { message: "hi", model_id: "m" };

    expect(
      CompletionRequestSchema.parse({ ...base, attachments: [1, 2, 3, 4] })
        .attachments,
    ).toEqual([1, 2, 3, 4]);
    // Omitted key stays omitted (never an empty array)
    expect(CompletionRequestSchema.parse(base).attachments).toBeUndefined();
    expect(
      CompletionRequestSchema.safeParse({ ...base, attachments: [1, 2, 3, 4, 5] })
        .success,
    ).toBe(false);
    expect(
      CompletionRequestSchema.safeParse({ ...base, attachments: [0] }).success,
    ).toBe(false);
    expect(
      CompletionRequestSchema.safeParse({ ...base, attachments: [1.5] }).success,
    ).toBe(false);

    // Regenerate carries no attachments field - unknown keys are stripped
    const regen = RegenerateRequestSchema.parse({
      model_id: "m",
      attachments: [1],
    });
    expect(regen).not.toHaveProperty("attachments");
  });

  it("clear and message delete parse deleted_count", async () => {
    expect(
      DeletedCountResponseSchema.parse({ ok: true, deleted_count: 3 })
        .deleted_count,
    ).toBe(3);

    mockFetch({
      "/chats/1/messages/2": { body: { ok: true, deleted_count: 4 } },
      "/chats/1/clear": { body: { ok: true, deleted_count: 5 } },
      "/chats/1": { body: { ok: true } },
    });

    await expect(clearChat(1)).resolves.toEqual({
      ok: true,
      deleted_count: 5,
    });
    await expect(deleteMessageAndFollowing(1, 2)).resolves.toEqual({
      ok: true,
      deleted_count: 4,
    });
    await expect(deleteChat(1)).resolves.toEqual({ ok: true });
  });

  it("character PATCH sends changed fields only and DELETE exists", async () => {
    const mock = mockFetch({
      "/characters/1": {
        body: {
          id: 1,
          name: "Test Character",
          description: "Changed",
          personality: "",
          scenario: "",
          first_mes: "",
          mes_example: "",
          system_prompt: "",
          post_history_instruction: "",
          tags: [],
          created_at: "2026-01-01T00:00:00",
        },
      },
    });

    await patchCharacter(1, { description: "Changed" });

    const patchBody = JSON.parse(mock.mock.calls[0][1]?.body as string);
    expect(patchBody).toEqual({ description: "Changed" });

    vi.unstubAllGlobals();
    const deleteMock = mockFetch({
      "/characters/1": { body: { ok: true } },
    });
    await deleteCharacter(1);
    expect(deleteMock.mock.calls[0][1]?.method).toBe("DELETE");
  });
});
