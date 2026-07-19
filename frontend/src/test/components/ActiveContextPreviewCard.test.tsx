/**
 * ActiveContextPreviewCard.test.tsx - FE-8B: Active Context Preview UI tests.
 *
 * Covers:
 *  - Collapsed by default; chevron toggle expands/collapses (component state)
 *  - PREVIEW_DISCLAIMER rendered verbatim
 *  - Included items render (model, persona, character, messages, params, budget)
 *  - NOT_INCLUDED_ITEMS render in full
 *  - Live updates when model / persona / generation params change
 *  - Quiet fallbacks when nothing is selected
 *  - Privacy: no descriptions, system prompts, or message content rendered
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import {
  GenerationSettingsProvider,
  useGenerationSettings,
} from "@/components/generation/GenerationSettingsContext";
import { ActiveContextPreviewCard } from "@/components/preview/ActiveContextPreviewCard";
import { NOT_INCLUDED_ITEMS, PREVIEW_DISCLAIMER } from "@/lib/preview";
import { TEXT_ONLY_NOTE } from "@/lib/models";
import { keys } from "@/lib/query/keys";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "@/test/mocks/api";
import {
  characterFixture,
  chatFixture,
  messageFixture,
  modelFixture,
  personaFixture,
} from "@/test/mocks/fixtures";
import type { Model, ModelList } from "@/lib/schemas/models";
import type { Message } from "@/lib/schemas/chats";
import type { Persona } from "@/lib/schemas/personas";

// ── Fixtures ─────────────────────────────────────────────────────

const secondModel: Model = {
  ...modelFixture,
  id: "test/second-model",
  name: "Second Model",
};

const permissiveModel: Model = {
  ...modelFixture,
  id: "test/permissive",
  name: "Permissive Model",
  supported_parameters: [],
};

const modelListBody: ModelList = {
  source: "user",
  cached: false,
  count: 3,
  models: [modelFixture, secondModel, permissiveModel],
};

const userMessage: Message = {
  ...messageFixture,
  id: 2,
  role: "user",
  content: "Very private user message body",
};

function mockAllRoutes() {
  mockFetch({
    "/models/openrouter": { body: modelListBody },
    "/personas": { body: [personaFixture] },
    "/characters": { body: [characterFixture] },
    "/chats/1/messages": { body: [messageFixture, userMessage] },
  });
}

// ── Harness ──────────────────────────────────────────────────────

function renderCard() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  const api: { current: ReturnType<typeof useGenerationSettings> | null } = {
    current: null,
  };

  function Probe() {
    api.current = useGenerationSettings();
    return null;
  }

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={qc}>
        <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
      </QueryClientProvider>
    );
  }

  render(
    <>
      <ActiveContextPreviewCard />
      <Probe />
    </>,
    { wrapper: Wrapper },
  );

  return { qc, api };
}

async function openCard() {
  const user = userEvent.setup();
  await user.click(screen.getByTestId("active-context-preview-toggle"));
  return user;
}

describe("ActiveContextPreviewCard", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: 1,
      selectedCharacterId: characterFixture.id,
      selectedModelId: modelFixture.id,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: null,
      selectedCharacterId: null,
      selectedModelId: null,
    });
  });

  it("is collapsed by default and expands via the header toggle", async () => {
    mockAllRoutes();
    renderCard();

    const toggle = screen.getByTestId("active-context-preview-toggle");
    // The content stays mounted (smooth height collapse), so the state is
    // conveyed by aria-expanded rather than DOM presence.
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    const user = await openCard();
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(PREVIEW_DISCLAIMER)).toBeInTheDocument();

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("renders the disclaimer verbatim", async () => {
    mockAllRoutes();
    renderCard();
    await openCard();

    // Exact constant - the handoff doc requires verbatim rendering.
    expect(screen.getByText(PREVIEW_DISCLAIMER)).toBeInTheDocument();
  });

  it("renders the included items from live data", async () => {
    mockAllRoutes();
    renderCard();
    await openCard();

    expect(screen.getByText("Next request includes")).toBeInTheDocument();
    expect(screen.getByText("Not included")).toBeInTheDocument();
    expect(await screen.findByText("GPT-4o")).toBeInTheDocument();
    expect(await screen.findByText("Test Persona")).toBeInTheDocument();
    expect(await screen.findByText("Test Character")).toBeInTheDocument();
    expect(
      await screen.findByText("2 messages in chat history"),
    ).toBeInTheDocument();
    // modelFixture supports temperature and top_p only.
    expect(screen.getByText("temperature, top_p")).toBeInTheDocument();
    expect(screen.getByText("16384 tokens")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Chat history inclusion budget (app-level, not forwarded to provider)",
      ),
    ).toBeInTheDocument();
    // modelFixture accepts text + image, both of which Elysium sends, so the
    // unsent-modality note must NOT show.
    expect(screen.queryByText(TEXT_ONLY_NOTE)).not.toBeInTheDocument();
  });

  it("renders every not-included item", async () => {
    mockAllRoutes();
    renderCard();
    await openCard();

    for (const item of NOT_INCLUDED_ITEMS) {
      expect(screen.getByText(item)).toBeInTheDocument();
    }
  });

  it("updates when the selected model changes", async () => {
    mockAllRoutes();
    renderCard();
    await openCard();

    expect(await screen.findByText("GPT-4o")).toBeInTheDocument();

    act(() => {
      useUiStore.setState({ selectedModelId: secondModel.id });
    });

    expect(await screen.findByText("Second Model")).toBeInTheDocument();
    expect(screen.queryByText("GPT-4o")).not.toBeInTheDocument();
  });

  it("updates when the active persona changes", async () => {
    mockAllRoutes();
    const { qc } = renderCard();
    await openCard();

    expect(await screen.findByText("Test Persona")).toBeInTheDocument();

    const newActive: Persona = {
      ...personaFixture,
      id: 2,
      display_name: "Night Owl",
    };
    act(() => {
      qc.setQueryData(keys.personas(), [
        { ...personaFixture, is_active: false },
        newActive,
      ]);
    });

    expect(await screen.findByText("Night Owl")).toBeInTheDocument();
    expect(screen.queryByText("Test Persona")).not.toBeInTheDocument();
  });

  it("updates when generation params change", async () => {
    useUiStore.setState({ selectedModelId: permissiveModel.id });
    mockAllRoutes();
    const { api } = renderCard();
    await openCard();

    expect(
      await screen.findByText(
        "temperature, top_p, top_k, repetition_penalty, max_tokens",
      ),
    ).toBeInTheDocument();

    act(() => {
      api.current!.setSetting("seed", "42");
    });
    expect(
      screen.getByText(
        "temperature, top_p, top_k, repetition_penalty, max_tokens, seed",
      ),
    ).toBeInTheDocument();

    act(() => {
      api.current!.setStopSequences(["User:"]);
    });
    expect(
      screen.getByText(
        "temperature, top_p, top_k, repetition_penalty, max_tokens, seed, stop",
      ),
    ).toBeInTheDocument();
  });

  it("shows quiet fallbacks when nothing is selected", async () => {
    useUiStore.setState({
      selectedChatId: null,
      selectedCharacterId: null,
      selectedModelId: null,
    });
    mockFetch({
      "/models/openrouter": {
        body: { source: "user", cached: false, count: 0, models: [] },
      },
      "/personas": { body: [] },
      "/characters": { body: [] },
    });
    renderCard();
    await openCard();

    expect(screen.getByText("No model selected")).toBeInTheDocument();
    expect(screen.getByText("No active persona")).toBeInTheDocument();
    expect(screen.getByText("No character selected")).toBeInTheDocument();
    expect(screen.getByText("No chat selected")).toBeInTheDocument();
    expect(screen.getByText(PREVIEW_DISCLAIMER)).toBeInTheDocument();
  });

  it("enriches the Messages row with the shared context estimate", async () => {
    // Estimator arithmetic (mirrors backend routers/completions.py):
    // modelFixture: ctx 128000, max_completion 16384, supports only
    // temperature/top_p -> the default max_tokens (1024) is never sent, so
    // the meta value 16384 is reserved.
    //   budget = 16384 (settings default) -> effective = min(16384, 128000)
    //   safety = 256 -> budget_chars = (16384 - 256) * 3 = 48384
    //   reservation = 16384 * 3 = 49152 > 48384 -> floor(48384 / 2) = 24192
    //   available = 24192 -> capacity = floor(24192 / 3) = 8064 -> "8.1K"
    // Fixed cost from characterFixture + personaFixture:
    //   system_block = "[System Prompt]\n" + 25 + "\n\n[Description]\n" + 32
    //     + "\n\n[Personality]\n" + 21 + "\n\n[Scenario]\n" + 20 = 159 chars
    //   persona = 15, phi = 0 -> fixed = 174
    // History: 28 + 30 = 58 chars, both fit.
    //   used = ceil((174 + 58) / 3) = ceil(232 / 3) = 78
    mockFetch({
      "/models/openrouter": { body: modelListBody },
      "/personas": { body: [personaFixture] },
      "/characters": { body: [characterFixture] },
      "/chats/1/messages": { body: [messageFixture, userMessage] },
      "/chats": { body: [chatFixture] },
    });
    renderCard();
    await openCard();

    expect(
      await screen.findByText("2 of 2 messages fit (≈78 / 8.1K tokens)"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("2 messages in chat history"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("context-usage-dropped-note"),
    ).not.toBeInTheDocument();
  });

  it("shows the dropped-oldest note when history exceeds the budget", async () => {
    // Tiny model: ctx 600, max_completion 100, same supported params.
    //   budget = clamp(16384, 600) = 600; safety = min(256, 75) = 75
    //   budget_chars = 525 * 3 = 1575; reservation = 100 * 3 = 300
    //   available = 1275 -> capacity = floor(1275 / 3) = 425
    // fixed = 174 -> remaining = 1101; history 600 + 600 = 1200 > 1101
    //   -> oldest dropped -> 600 kept; used = ceil((174 + 600) / 3) = 258
    const tinyModel: Model = {
      ...modelFixture,
      id: "test/tiny-ctx",
      name: "Tiny Ctx",
      context_length: 600,
      max_completion_tokens: 100,
    };
    const tinyList: ModelList = {
      source: "user",
      cached: false,
      count: 1,
      models: [tinyModel],
    };
    const longAssistant: Message = {
      ...messageFixture,
      id: 1,
      role: "assistant",
      content: "a".repeat(600),
    };
    const longUser: Message = {
      ...messageFixture,
      id: 2,
      role: "user",
      content: "b".repeat(600),
    };
    useUiStore.setState({ selectedModelId: tinyModel.id });
    mockFetch({
      "/models/openrouter": { body: tinyList },
      "/personas": { body: [personaFixture] },
      "/characters": { body: [characterFixture] },
      "/chats/1/messages": { body: [longAssistant, longUser] },
      "/chats": { body: [chatFixture] },
    });
    renderCard();
    await openCard();

    expect(
      await screen.findByText("1 of 2 messages fit (≈258 / 425 tokens)"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("context-usage-dropped-note"),
    ).toHaveTextContent("(1 oldest dropped)");
  });

  it("never renders secret-ish or content strings", async () => {
    mockAllRoutes();
    renderCard();
    await openCard();

    // Wait until live data is in so the check covers the populated card.
    expect(await screen.findByText("Test Persona")).toBeInTheDocument();
    expect(await screen.findByText("Test Character")).toBeInTheDocument();

    const text = document.body.textContent ?? "";
    expect(text).not.toContain(personaFixture.description);
    expect(text).not.toContain(characterFixture.description);
    expect(text).not.toContain(characterFixture.personality);
    expect(text).not.toContain(characterFixture.system_prompt);
    expect(text).not.toContain(userMessage.content);
    expect(text.toLowerCase()).not.toContain("api key:");
    expect(text.toLowerCase()).not.toContain("sk-");
  });
});
