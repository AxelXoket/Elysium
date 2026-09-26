/**
 * D3, the Thinking switch: shown only for models that list reasoning, usable
 * only where reasoning is optional, off by default, and carried to the
 * server as `generation_params.thinking` by all three payload builders.
 *
 * Ground controls: off sends nothing; a model with no reasoning shows no
 * switch. Positive controls: on reaches the body of send, regenerate and
 * edit, next to the samplers that still go.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { TooltipProvider } from "@/components/ui/tooltip";
import {
  GenerationSettingsProvider,
  useGenerationSettings,
} from "@/components/generation/GenerationSettingsContext";
import {
  THINKING_MANDATORY_LABEL,
  THINKING_MANDATORY_NOTE,
  THINKING_OFF_NOTE,
  THINKING_ON_NOTE,
  THINKING_SWITCH_LABEL,
  THINKING_UNKNOWN_LABEL,
  THINKING_UNKNOWN_NOTE,
} from "@/components/generation/ThinkingSetting";
import { ModelPanel } from "@/components/models/ModelPanel";
import {
  buildCompletionPayload,
  buildEditPayload,
  buildRegeneratePayload,
} from "@/lib/generation/generationParams";
import { getReasoningMode } from "@/lib/models";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "@/test/mocks/api";
import { modelFixture, settingsFixture } from "@/test/mocks/fixtures";
import type { Model } from "@/lib/schemas/models";

const base: Model = {
  ...modelFixture,
  id: "test/plain",
  supported_parameters: ["temperature", "max_tokens"],
  reasoning: null,
};
const optional: Model = {
  ...base,
  id: "test/optional",
  supported_parameters: ["temperature", "max_tokens", "reasoning"],
  reasoning: { mandatory: false, supported_efforts: ["low", "high"] },
};
const mandatory: Model = {
  ...optional,
  id: "test/mandatory",
  reasoning: { mandatory: true, supported_efforts: ["low"] },
};
const unknown: Model = { ...optional, id: "test/unknown", reasoning: null };

describe("getReasoningMode", () => {
  it("reads the four cases the server reads", () => {
    expect(getReasoningMode(base)).toBe("none");
    expect(getReasoningMode(optional)).toBe("optional");
    expect(getReasoningMode(mandatory)).toBe("mandatory");
    expect(getReasoningMode(unknown)).toBe("unknown");
    expect(getReasoningMode({ ...optional, reasoning: {} })).toBe("unknown");
    expect(getReasoningMode(null)).toBe("none");
  });
});

describe("the payload builders carry the switch", () => {
  const params = { temperature: 0.7, thinking: true };

  it("send, regenerate and edit all carry thinking when it is on", () => {
    for (const payload of [
      buildCompletionPayload({ message: "m", modelId: optional.id, generationParams: params, model: optional }),
      buildRegeneratePayload({ modelId: optional.id, generationParams: params, model: optional }),
      buildEditPayload({ message: "m", modelId: optional.id, generationParams: params, model: optional }),
    ]) {
      expect(payload.generation_params).toEqual({ temperature: 0.7, thinking: true });
    }
  });

  it("thinking alone still makes a generation_params object", () => {
    const payload = buildRegeneratePayload({
      modelId: optional.id,
      generationParams: { thinking: true },
      model: optional,
    });
    expect(payload.generation_params).toEqual({ thinking: true });
  });

  it("GROUND: off sends nothing, and the samplers go as before", () => {
    const payload = buildCompletionPayload({
      message: "m",
      modelId: optional.id,
      generationParams: { temperature: 0.7, thinking: false },
      model: optional,
    });
    expect(payload.generation_params).toEqual({ temperature: 0.7 });
  });
});

function Extra({ children }: { children: ReactNode }) {
  return (
    <TooltipProvider>
      <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
    </TooltipProvider>
  );
}

async function renderDialog(model: Model) {
  const api: { current: ReturnType<typeof useGenerationSettings> | null } = { current: null };
  function Probe({ take }: { take: (a: ReturnType<typeof useGenerationSettings>) => void }) {
    take(useGenerationSettings());
    return null;
  }
  useUiStore.setState({ selectedModelId: model.id });
  vi.stubGlobal(
    "fetch",
    mockFetch({
      "/settings": { body: settingsFixture },
      "/models/openrouter": {
        body: { source: "user", cached: true, count: 1, models: [model] },
      },
    }),
  );
  renderWithQueryClient(
    <>
      <ModelPanel />
      <Probe take={(next) => (api.current = next)} />
    </>,
    { wrapper: Extra },
  );
  await waitFor(() =>
    expect(screen.getByTestId("generation-settings-trigger")).toBeInTheDocument(),
  );
  const user = userEvent.setup();
  await user.click(screen.getByTestId("generation-settings-trigger"));
  expect(await screen.findByText("Sampling")).toBeInTheDocument();
  // Wait for the model list to reach the dialog (the description shows its id).
  await waitFor(() => expect(screen.getAllByText(model.id).length).toBeGreaterThan(0));
  return { api, user };
}

describe("the Thinking switch in the dialog", () => {
  beforeEach(() => {
    useUiStore.setState({ selectedModelId: null, selectedChatId: null, selectedCharacterId: null });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("is off by default and turns thinking on for an optional model", async () => {
    const { api, user } = await renderDialog(optional);
    const toggle = screen.getByRole("switch", { name: THINKING_SWITCH_LABEL });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(toggle).toHaveTextContent("Off");
    expect(screen.getByText(THINKING_OFF_NOTE)).toBeInTheDocument();
    expect(api.current!.getRequestSettings().generationParams.thinking).toBeUndefined();

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(toggle).toHaveTextContent("On");
    expect(screen.getByText(THINKING_ON_NOTE)).toBeInTheDocument();
    expect(api.current!.getRequestSettings().generationParams.thinking).toBe(true);

    act(() => api.current!.resetAll());
    expect(api.current!.getRequestSettings().generationParams.thinking).toBeUndefined();
  });

  it("is disabled with its reason on a mandatory model", async () => {
    await renderDialog(mandatory);
    const toggle = screen.getByRole("switch", { name: THINKING_SWITCH_LABEL });
    expect(toggle).toBeDisabled();
    expect(screen.getByText(THINKING_MANDATORY_NOTE)).toBeInTheDocument();
    // It says what happens: the model thinks. Never "Off" beside
    // "The selected model always thinks".
    expect(toggle).toHaveTextContent(THINKING_MANDATORY_LABEL);
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(toggle).not.toHaveTextContent(/^Off$/);
  });

  it("is disabled with its reason when the catalogue says nothing", async () => {
    await renderDialog(unknown);
    const toggle = screen.getByRole("switch", { name: THINKING_SWITCH_LABEL });
    expect(toggle).toBeDisabled();
    expect(screen.getByText(THINKING_UNKNOWN_NOTE)).toBeInTheDocument();
    expect(toggle).toHaveTextContent(THINKING_UNKNOWN_LABEL);
    expect(toggle).not.toHaveTextContent(/^Off$/);
  });

  it("GROUND: a model with no reasoning shows no switch", async () => {
    await renderDialog(base);
    // Positive control that the dialog did render its other switch.
    expect(screen.getByRole("switch", { name: "Let replies include pictures" })).toBeInTheDocument();
    expect(screen.queryByRole("switch", { name: THINKING_SWITCH_LABEL })).toBeNull();
    expect(screen.queryByTestId("thinking-setting")).toBeNull();
  });

  it("the switch label does not repeat its section title", async () => {
    await renderDialog(optional);
    // Ground: the section is still titled Thinking.
    expect(screen.getByRole("heading", { level: 3, name: "Thinking" })).toBeInTheDocument();
    // The switch says what it does instead of saying "Thinking" again.
    expect(THINKING_SWITCH_LABEL).not.toBe("Thinking");
    expect(screen.queryByRole("switch", { name: "Thinking" })).toBeNull();
    expect(screen.getByRole("switch", { name: THINKING_SWITCH_LABEL })).toBeInTheDocument();
  });
});
