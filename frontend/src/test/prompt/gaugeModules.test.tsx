/**
 * The gauge learns about prompt modules and the layer (W4 H4): ModelPanel
 * passes the enabled modules and the layer settings into the estimate, so a
 * module on the settings page moves the meter by exactly what the request
 * will carry.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen } from "@testing-library/react";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { mockFetch } from "@/test/mocks/api";
import { characterFixture, chatFixture } from "@/test/mocks/fixtures";
import { ModelPanel } from "@/components/models/ModelPanel";
import { TooltipProvider } from "@/components/ui/tooltip";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useErrorStore } from "@/lib/errors";
import { useUiStore } from "@/lib/store/uiStore";
import { PART_SEP, promptCost } from "@/lib/prompt/assembly";
import { estimateContextUsage, formatTokensCompact } from "@/lib/context";
import type { Model, ModelList } from "@/lib/schemas/models";
import type { Message } from "@/lib/schemas/chats";

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <TooltipProvider>
      <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
    </TooltipProvider>
  );
}

// ctx 1000, max 100: available 2325 chars, capacity 775 tokens.
const model: Model = {
  id: "test/gauge-1000", name: "Gauge", description: "", context_length: 1000,
  max_completion_tokens: 100, supported_parameters: ["temperature"],
  input_modalities: ["text"], output_modalities: ["text"], pricing: {},
  top_provider: {}, created: null, canonical_slug: "test/gauge-1000",
};
const models: ModelList = { source: "user", cached: false, count: 1, models: [model] };
const character = {
  ...characterFixture, id: 3, name: "Terse", system_prompt: "You are terse.",
  description: "", personality: "", scenario: "", mes_example: "",
  post_history_instruction: "",
};
const chat = { ...chatFixture, id: 9, character_id: 3 };
const message: Message = {
  id: 1, chat_id: 9, role: "user", content: "x".repeat(600), created_at: "2026-01-01T00:00:00",
};
const MODULE_TEXT = "M".repeat(300);

function moduleRow(enabled: boolean) {
  return {
    id: 5, import_id: 1, kind: "module", name: "Style", content: MODULE_TEXT,
    note: null, enabled, sort_order: 0, revision: 1, source_type: "txt",
    source_filename: "style.txt", st_enabled: null, st_role: null,
    st_injection_depth: null, st_injection_order: null, st_injection_trigger: null,
    triggers: ["send", "regenerate"], badges: [], can_enable: true, blocked_reason: null,
  };
}

function routes(extra: Record<string, { status?: number; body: unknown }>) {
  mockFetch({
    ...extra,
    "/models/openrouter": { body: models },
    "/chats/9/messages": { body: [message] },
    "/chats": { body: [chat] },
    "/characters": { body: [character] },
    "/personas": { body: [] },
  });
}

async function usedTokens(): Promise<number> {
  const meter = await screen.findByTestId("context-usage-meter");
  const m = /Context ≈ (\d+) \/ 775 tokens/.exec(meter.textContent ?? "");
  expect(m).not.toBeNull();
  return Number(m![1]);
}

const base = promptCost({ card: character }).fixedChars;

describe("the gauge charges the greeting as it is sent", () => {
    // The oldest row, when it is the assistant's, is the card's greeting and
    // goes out sanitized: "(OOC:" becomes "(author aside:", nine characters
    // longer. Any other row goes as stored.
    const asAssistant = (id: number, content: string): Message => ({
      id, chat_id: 9, role: "assistant", content, created_at: "2026-01-01T00:00:00",
    });
    const asUser = (id: number, content: string): Message => ({
      ...asAssistant(id, content), role: "user",
    });
    const body = "a".repeat(300) + ")";
    const est = (messages: Message[]) => estimateContextUsage({
      model, character, messages, contextBudgetTokens: 16384,
    })!.usedTokens;

    it("a greeting with a marker is charged at its sanitized length", () => {
      expect(est([asAssistant(1, "(OOC: " + body)]))
        .toBe(est([asAssistant(1, "(author aside: " + body)]));
    });

    it("ground control: the user's own row is charged as typed", () => {
      expect(est([asUser(1, "(OOC: " + body)]))
        .toBeLessThan(est([asUser(1, "(author aside: " + body)]));
    });
});

describe("the gauge charges prompt modules and the layer", () => {
  beforeEach(() => {
    useErrorStore.getState().clearAll();
    useUiStore.setState({ selectedModelId: model.id, selectedChatId: 9, selectedCharacterId: null });
  });
  afterEach(() => {
    useUiStore.setState({ selectedModelId: null, selectedChatId: null, selectedCharacterId: null });
    useErrorStore.getState().clearAll();
    vi.restoreAllMocks();
  });

  it("ground control: no module routes answered, the plain assembly is charged", async () => {
    routes({});
    renderWithQueryClient(<ModelPanel />, { wrapper });
    expect(await usedTokens()).toBe(Math.ceil((base + 600) / 3));
  });

  it("a module switched off costs nothing", async () => {
    routes({
      "/prompt-modules/settings": { body: { prompt_layer_enabled: false, reply_length_words: null } },
      "/prompt-modules": { body: { modules: [moduleRow(false)] } },
    });
    renderWithQueryClient(<ModelPanel />, { wrapper });
    expect(await usedTokens()).toBe(Math.ceil((base + 600) / 3));
  });

  it("an enabled module moves the meter by its text and one separator", async () => {
    routes({
      "/prompt-modules/settings": { body: { prompt_layer_enabled: false, reply_length_words: null } },
      "/prompt-modules": { body: { modules: [moduleRow(true)] } },
    });
    renderWithQueryClient(<ModelPanel />, { wrapper });
    await screen.findByTestId("context-usage-meter");
    const expected = Math.ceil((base + MODULE_TEXT.length + PART_SEP.length + 600) / 3);
    await vi.waitFor(async () => expect(await usedTokens()).toBe(expected));
  });

  it("the layer setting with no module charges the layer", async () => {
    // The layer is ~4.6K characters, more than this small window holds: the
    // turn would be refused and the meter reads over capacity, which is
    // exactly what the backend would do with the layer on.
    routes({
      "/prompt-modules/settings": { body: { prompt_layer_enabled: true, reply_length_words: null } },
      "/prompt-modules": { body: { modules: [] } },
    });
    renderWithQueryClient(<ModelPanel />, { wrapper });
    const layered = promptCost({ card: character, layerEnabled: true }).fixedChars;
    expect(layered).toBeGreaterThan(base + 1000);
    const meter = await screen.findByTestId("context-usage-meter");
    await vi.waitFor(() =>
      expect(meter.textContent).toContain(
        `Context ≈ ${formatTokensCompact(Math.ceil(layered / 3))} / 775 tokens`));
  });
});
