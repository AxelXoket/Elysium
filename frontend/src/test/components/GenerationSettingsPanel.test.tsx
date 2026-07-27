import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";
import {
  CONTEXT_BUDGET_UI_MAX,
  GenerationSettingsProvider,
  getContextBudgetUiMax,
  useGenerationSettings,
} from "@/components/generation/GenerationSettingsContext";
import { ModelPanel } from "@/components/models/ModelPanel";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "@/test/mocks/api";
import { settingsFixture } from "@/test/mocks/fixtures";
import { modelFixture } from "@/test/mocks/fixtures";
import type { ModelList, Model } from "@/lib/schemas/models";
import type { ReactNode } from "react";

const allSupported = [
  "temperature",
  "top_p",
  "top_k",
  "repetition_penalty",
  "max_tokens",
  "seed",
];

const fullModel: Model = {
  ...modelFixture,
  id: "test/full-support",
  name: "Full Support",
  context_length: 32768,
  max_completion_tokens: 8192,
  supported_parameters: allSupported,
};

const stopModel: Model = {
  ...fullModel,
  id: "test/stop-support",
  name: "Stop Support",
  supported_parameters: [...allSupported, "stop"],
};

function modelList(model: Model): ModelList {
  return {
    source: "user",
    cached: true,
    count: 1,
    models: [model],
  };
}

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return (
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
      </TooltipProvider>
    </QueryClientProvider>
  );
}

/**
 * Routes every dialog test needs, with a WORKING stop-sequences store.
 *
 * Stop sequences round-trip through the vault now (they are character names -
 * user content - so browser storage is closed to them), so a chip exists only
 * once the save has landed and the server has echoed it back. A stub that
 * always answered with an empty list would delete every chip on add.
 */
function mockDialogFetch(model: Model) {
  const base = mockFetch({
    "/settings": { body: settingsFixture },
    "/models/openrouter": { body: modelList(model) },
  });
  let stored: string[] = [];
  const json = (body: unknown) =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });

  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/settings/stop-sequences")) {
      stored = JSON.parse(String(init?.body)).stop_sequences as string[];
      return json({ ok: true, stop_sequences: stored });
    }
    if (url.includes("/settings")) {
      return json({ ...settingsFixture, stop_sequences: stored });
    }
    return base(input, init);
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

async function renderWithModel(model: Model) {
  useUiStore.setState({ selectedModelId: model.id });
  mockDialogFetch(model);

  render(<ModelPanel />, { wrapper });

  await waitFor(() => {
    expect(screen.getByTestId("generation-settings-trigger")).toBeInTheDocument();
  });
}

async function openDialog() {
  const user = userEvent.setup();
  await user.click(screen.getByTestId("generation-settings-trigger"));
  expect(await screen.findByText("Sampling")).toBeInTheDocument();
  return user;
}

/**
 * Render ModelPanel together with a probe that exposes the settings context,
 * so dialog interactions can be asserted against getRequestSettings().
 */
async function renderWithModelAndApi(model: Model) {
  const api: { current: ReturnType<typeof useGenerationSettings> | null } = {
    current: null,
  };

  function Probe() {
    api.current = useGenerationSettings();
    return null;
  }

  useUiStore.setState({ selectedModelId: model.id });
  mockDialogFetch(model);

  render(
    <>
      <ModelPanel />
      <Probe />
    </>,
    { wrapper },
  );

  await waitFor(() => {
    expect(screen.getByTestId("generation-settings-trigger")).toBeInTheDocument();
  });

  return api;
}

function addStopSequence(value: string) {
  const input = screen.getByLabelText("Stop sequence");
  fireEvent.change(input, { target: { value } });
  fireEvent.keyDown(input, { key: "Enter" });
}

describe("Generation Settings Panel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedModelId: null,
      selectedChatId: null,
      selectedCharacterId: null,
      // v1.1 FF7: gen scalars now persist to the module-global uiStore and the
      // provider hydrates from it - reset them so tests don't leak into each
      // other's "last committed value".
      genTemperature: 0.8,
      genTopP: 0.9,
      genTopK: 40,
      genRepetitionPenalty: 1.05,
      genMaxOutput: 1024,
      genSeed: "",
      genContextBudget: 16384,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the Generation Settings trigger under the selected model section", async () => {
    await renderWithModel(fullModel);

    expect(screen.getByText("Selected Model")).toBeInTheDocument();
    expect(screen.getByTestId("generation-settings-trigger")).toHaveTextContent(
      "Generation Settings",
    );
  });

  it("opens the central dialog when the trigger is clicked", async () => {
    await renderWithModel(fullModel);
    await openDialog();

    expect(screen.getAllByText("Generation Settings").length).toBeGreaterThan(0);
    expect(screen.getAllByText(fullModel.id).length).toBeGreaterThan(0);
  });

  it("closes the dialog with the top-right X button", async () => {
    await renderWithModel(fullModel);
    await openDialog();

    const closeButtons = screen.getAllByRole("button", { name: "Close" });
    await userEvent.click(closeButtons[closeButtons.length - 1]);

    await waitFor(() => {
      expect(screen.queryByText("Sampling")).not.toBeInTheDocument();
    });
  });

  it("closes the dialog on outside click", async () => {
    await renderWithModel(fullModel);
    await openDialog();

    const overlay = document.querySelector('[data-slot="dialog-overlay"]');
    expect(overlay).not.toBeNull();
    await userEvent.click(overlay as HTMLElement);

    await waitFor(() => {
      expect(screen.queryByText("Sampling")).not.toBeInTheDocument();
    });
  });

  it("shows the defined default values", async () => {
    await renderWithModel(fullModel);
    await openDialog();

    expect(screen.getByLabelText("Temperature value")).toHaveValue(0.8);
    expect(screen.getByLabelText("Top P value")).toHaveValue(0.9);
    expect(screen.getByLabelText("Top K value")).toHaveValue(40);
    expect(screen.getByLabelText("Repetition penalty value")).toHaveValue(1.05);
    expect(screen.getByLabelText("Max new tokens value")).toHaveValue(1024);
    expect(screen.getByLabelText("Seed value")).toHaveValue(null);
    expect(screen.getByLabelText("Context budget value")).toHaveValue(16384);
  });

  it("Reset all restores the model-aware defaults", async () => {
    await renderWithModel(fullModel);
    await openDialog();

    const input = screen.getByLabelText("Temperature value");
    fireEvent.change(input, { target: { value: "1.5" } });
    fireEvent.blur(input);
    expect(input).toHaveValue(1.5);

    await userEvent.click(screen.getByRole("button", { name: "Reset all" }));

    expect(input).toHaveValue(0.8);
  });

  it("keeps slider and numeric input in sync", async () => {
    await renderWithModel(fullModel);
    await openDialog();

    fireEvent.change(screen.getByLabelText("Temperature slider"), {
      target: { value: "1.2" },
    });

    expect(screen.getByLabelText("Temperature value")).toHaveValue(1.2);
  });

  it("keeps the raw value while typing and clamps to the UI range on blur", async () => {
    await renderWithModel(fullModel);
    await openDialog();

    const input = screen.getByLabelText("Top K value");
    fireEvent.change(input, { target: { value: "999" } });

    // Typing in progress: value is not clamped mid-keystroke
    expect(input).toHaveValue(999);

    fireEvent.blur(input);
    expect(input).toHaveValue(200);
  });

  it("commits the numeric input on Enter", async () => {
    await renderWithModel(fullModel);
    await openDialog();

    const input = screen.getByLabelText("Temperature value");
    fireEvent.change(input, { target: { value: "5" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(input).toHaveValue(2);
  });

  it("reverts to the last committed value when cleared and blurred", async () => {
    await renderWithModel(fullModel);
    await openDialog();

    const input = screen.getByLabelText("Temperature value");
    fireEvent.change(input, { target: { value: "" } });

    // Clearing the field must not snap to the minimum while editing
    expect(input).toHaveValue(null);

    fireEvent.blur(input);
    expect(input).toHaveValue(0.8);
  });

  it("reverts invalid input to the last committed value on blur", async () => {
    await renderWithModel(fullModel);
    await openDialog();

    const input = screen.getByLabelText("Top K value");
    fireEvent.change(input, { target: { value: "50" } });
    fireEvent.blur(input);
    expect(input).toHaveValue(50);

    fireEvent.change(input, { target: { value: "-" } });
    fireEvent.blur(input);
    expect(input).toHaveValue(50);
  });

  it("disables unsupported params when the selected model excludes them", async () => {
    await renderWithModel({
      ...fullModel,
      supported_parameters: ["temperature", "max_tokens"],
    });
    await openDialog();

    expect(screen.getByLabelText("Top P value")).toBeDisabled();
    expect(screen.getAllByText("Not supported by selected model.").length).toBeGreaterThan(0);
  });

  it("shows unknown support copy on the trigger when supported_parameters is empty", async () => {
    await renderWithModel({
      ...fullModel,
      supported_parameters: [],
    });

    expect(
      screen.getByText("Parameter support is unknown for this model."),
    ).toBeInTheDocument();
  });

  it("keeps all controls enabled when supported_parameters is empty (permissive fallback)", async () => {
    await renderWithModel({
      ...fullModel,
      supported_parameters: [],
    });
    await openDialog();

    expect(screen.getByLabelText("Temperature value")).toBeEnabled();
    expect(screen.getByLabelText("Top P value")).toBeEnabled();
    expect(screen.getByLabelText("Top K value")).toBeEnabled();
    expect(screen.getByLabelText("Repetition penalty value")).toBeEnabled();
    expect(screen.getByLabelText("Max new tokens value")).toBeEnabled();
    expect(screen.getByLabelText("Seed value")).toBeEnabled();
    expect(screen.getByLabelText("Stop sequence")).toBeEnabled();
    expect(
      screen.queryByText("Not supported by selected model."),
    ).not.toBeInTheDocument();
  });

  it("shows the seed contract range in the helper text", async () => {
    await renderWithModel(fullModel);
    await openDialog();

    expect(
      screen.getByText(/-2147483648 to 2147483647/),
    ).toBeInTheDocument();
  });

  it("respects selected model max_completion_tokens for Max new tokens", async () => {
    await renderWithModel({
      ...fullModel,
      max_completion_tokens: 256,
    });
    await openDialog();

    expect(screen.getByLabelText("Max new tokens value")).toHaveValue(256);
    expect(screen.getByLabelText("Max new tokens value")).toHaveAttribute(
      "max",
      "256",
    );
  });

  it("respects selected model context_length for Context budget", async () => {
    await renderWithModel({
      ...fullModel,
      context_length: 4096,
    });
    await openDialog();

    expect(screen.getByLabelText("Context budget value")).toHaveValue(4096);
    expect(screen.getByLabelText("Context budget value")).toHaveAttribute(
      "max",
      "4096",
    );
  });
});

// ── Seed clamping through the provider (contract: -(2^31) to 2^31-1) ──

function renderSettingsHarness() {
  const api: { current: ReturnType<typeof useGenerationSettings> | null } = {
    current: null,
  };

  function Harness() {
    api.current = useGenerationSettings();
    return null;
  }

  render(
    <QueryClientProvider client={new QueryClient()}>
      <GenerationSettingsProvider>
        <Harness />
      </GenerationSettingsProvider>
    </QueryClientProvider>,
  );

  return api;
}

describe("Generation Settings seed clamping", () => {
  it("passes in-range seeds through unchanged", () => {
    const api = renderSettingsHarness();

    act(() => api.current!.setSetting("seed", "42"));
    expect(api.current!.getRequestSettings().generationParams.seed).toBe(42);

    act(() => api.current!.setSetting("seed", "-7"));
    expect(api.current!.getRequestSettings().generationParams.seed).toBe(-7);
  });

  it("clamps oversized seeds to the int32 contract bounds", () => {
    const api = renderSettingsHarness();

    act(() => api.current!.setSetting("seed", "99999999999999999999"));
    expect(api.current!.getRequestSettings().generationParams.seed).toBe(
      2147483647,
    );

    act(() => api.current!.setSetting("seed", "-99999999999999999999"));
    expect(api.current!.getRequestSettings().generationParams.seed).toBe(
      -2147483648,
    );
  });

  it("keeps the exact contract boundary values", () => {
    const api = renderSettingsHarness();

    act(() => api.current!.setSetting("seed", "2147483647"));
    expect(api.current!.getRequestSettings().generationParams.seed).toBe(
      2147483647,
    );

    act(() => api.current!.setSetting("seed", "-2147483648"));
    expect(api.current!.getRequestSettings().generationParams.seed).toBe(
      -2147483648,
    );
  });

  it("omits seed for empty or non-integer input", () => {
    const api = renderSettingsHarness();

    act(() => api.current!.setSetting("seed", ""));
    expect(
      api.current!.getRequestSettings().generationParams.seed,
    ).toBeUndefined();

    act(() => api.current!.setSetting("seed", "-"));
    expect(
      api.current!.getRequestSettings().generationParams.seed,
    ).toBeUndefined();
  });
});

// ── Stop sequences via the provider ──────────────────────────────

describe("Generation Settings stop sequences (context)", () => {
  it("omits stop while no sequences are set", () => {
    const api = renderSettingsHarness();
    expect(
      api.current!.getRequestSettings().generationParams.stop,
    ).toBeUndefined();
  });

  it("emits stop in array form when sequences exist", () => {
    const api = renderSettingsHarness();

    act(() => api.current!.setStopSequences(["User:"]));
    expect(api.current!.getRequestSettings().generationParams.stop).toEqual([
      "User:",
    ]);

    act(() => api.current!.setStopSequences(["User:", "\n\n"]));
    expect(api.current!.getRequestSettings().generationParams.stop).toEqual([
      "User:",
      "\n\n",
    ]);
  });

  it("omits stop again when sequences are cleared", () => {
    const api = renderSettingsHarness();

    act(() => api.current!.setStopSequences(["User:"]));
    act(() => api.current!.setStopSequences([]));
    expect(
      api.current!.getRequestSettings().generationParams.stop,
    ).toBeUndefined();
  });

  it("resetAll clears stop sequences", () => {
    const api = renderSettingsHarness();

    act(() => api.current!.setStopSequences(["User:", "END"]));
    act(() => api.current!.resetAll());
    expect(api.current!.stopSequences).toEqual([]);
    expect(
      api.current!.getRequestSettings().generationParams.stop,
    ).toBeUndefined();
  });
});

// ── Stop sequences in the dialog ─────────────────────────────────

describe("Generation Settings stop sequences (dialog)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedModelId: null,
      selectedChatId: null,
      selectedCharacterId: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("adds a chip on Enter and emits array-form stop", async () => {
    const api = await renderWithModelAndApi(stopModel);
    await openDialog();

    addStopSequence("User:");

    expect(screen.getByTestId("stop-sequence-chip")).toHaveTextContent("User:");
    expect(screen.getByLabelText("Stop sequence")).toHaveValue("");
    expect(api.current!.getRequestSettings().generationParams.stop).toEqual([
      "User:",
    ]);
  });

  it("adds a chip with the Add button", async () => {
    const api = await renderWithModelAndApi(stopModel);
    const user = await openDialog();

    fireEvent.change(screen.getByLabelText("Stop sequence"), {
      target: { value: "END" },
    });
    await user.click(screen.getByRole("button", { name: "Add" }));

    expect(screen.getByTestId("stop-sequence-chip")).toHaveTextContent("END");
    expect(api.current!.getRequestSettings().generationParams.stop).toEqual([
      "END",
    ]);
  });

  it("converts a typed literal \\n to a real newline and displays it back as \\n", async () => {
    const api = await renderWithModelAndApi(stopModel);
    await openDialog();

    addStopSequence("\\n");

    // Chip shows the escaped form; the stored sequence is a real newline.
    expect(screen.getByTestId("stop-sequence-chip")).toHaveTextContent("\\n");
    expect(api.current!.getRequestSettings().generationParams.stop).toEqual([
      "\n",
    ]);
  });

  it("removes a chip with its remove button", async () => {
    const api = await renderWithModelAndApi(stopModel);
    const user = await openDialog();

    addStopSequence("User:");
    addStopSequence("END");
    expect(screen.getAllByTestId("stop-sequence-chip")).toHaveLength(2);

    const removeButtons = screen.getAllByLabelText(/Remove stop sequence/);
    await user.click(removeButtons[0]);

    expect(screen.getAllByTestId("stop-sequence-chip")).toHaveLength(1);
    expect(api.current!.getRequestSettings().generationParams.stop).toEqual([
      "END",
    ]);
  });

  it("ignores duplicate sequences", async () => {
    const api = await renderWithModelAndApi(stopModel);
    await openDialog();

    addStopSequence("User:");
    addStopSequence("User:");

    expect(screen.getAllByTestId("stop-sequence-chip")).toHaveLength(1);
    expect(api.current!.getRequestSettings().generationParams.stop).toEqual([
      "User:",
    ]);
  });

  it("ignores empty input on commit", async () => {
    const api = await renderWithModelAndApi(stopModel);
    await openDialog();

    addStopSequence("");

    expect(screen.queryByTestId("stop-sequence-chip")).not.toBeInTheDocument();
    expect(
      api.current!.getRequestSettings().generationParams.stop,
    ).toBeUndefined();
  });

  it("caps at 4 sequences and disables the input at the cap", async () => {
    const api = await renderWithModelAndApi(stopModel);
    await openDialog();

    addStopSequence("a");
    addStopSequence("b");
    addStopSequence("c");
    addStopSequence("d");

    expect(screen.getAllByTestId("stop-sequence-chip")).toHaveLength(4);
    expect(screen.getByTestId("stop-sequence-count")).toHaveTextContent("4/4");
    expect(screen.getByLabelText("Stop sequence")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add" })).toBeDisabled();
    expect(api.current!.getRequestSettings().generationParams.stop).toEqual([
      "a",
      "b",
      "c",
      "d",
    ]);
  });

  it("re-enables the input when a chip is removed at the cap", async () => {
    await renderWithModelAndApi(stopModel);
    const user = await openDialog();

    addStopSequence("a");
    addStopSequence("b");
    addStopSequence("c");
    addStopSequence("d");
    expect(screen.getByLabelText("Stop sequence")).toBeDisabled();

    await user.click(screen.getAllByLabelText(/Remove stop sequence/)[0]);

    expect(screen.getByTestId("stop-sequence-count")).toHaveTextContent("3/4");
    expect(screen.getByLabelText("Stop sequence")).toBeEnabled();
  });

  // Audit: this case used to assert the SECTION WAS DISABLED, on the premise
  // stated in the code ("stop is filtered out of unsupported-model requests
  // anyway"). That premise is false: filterParamsByModel keeps `stop`
  // regardless of supported_parameters and the backend mirrors it. So the UI
  // blocked a feature that works AND kept sending the chips it had greyed out.
  it("stays usable on a model that does not advertise stop", async () => {
    // fullModel supports the six other params but not stop.
    const api = await renderWithModelAndApi(fullModel);
    const user = await openDialog();

    expect(screen.getByLabelText("Stop sequence")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Add" })).toBeEnabled();

    await user.type(screen.getByLabelText("Stop sequence"), "Human:");
    await user.click(screen.getByRole("button", { name: "Add" }));

    // The chip is on the wire either way - which is exactly why the editor
    // must not pretend the feature is unavailable.
    expect(api.current!.getRequestSettings().generationParams.stop).toEqual([
      "Human:",
    ]);
  });

  // U3: a user who added stop chips under a model that supports `stop`, then
  // switched to one that does not, must still be able to clear the stale chips.
  it("U3: keeps chip remove buttons enabled when the model lacks stop support", async () => {
    // fullModel supports the six other params but NOT stop.
    const api = await renderWithModelAndApi(fullModel);
    const user = await openDialog();

    // Chips carried over in memory from a previous stop-capable model.
    act(() => api.current!.setStopSequences(["User:", "END"]));

    // Every remove button stays enabled so stale chips can be cleared.
    const removeButtons = screen.getAllByLabelText(/Remove stop sequence/);
    expect(removeButtons).toHaveLength(2);
    for (const btn of removeButtons) {
      expect(btn).toBeEnabled();
    }

    // Removal actually works (and clearing all chips drops stop from the request).
    await user.click(removeButtons[0]);
    expect(screen.getAllByTestId("stop-sequence-chip")).toHaveLength(1);
    expect(api.current!.getRequestSettings().generationParams.stop).toEqual([
      "END",
    ]);

    await user.click(screen.getAllByLabelText(/Remove stop sequence/)[0]);
    expect(screen.queryByTestId("stop-sequence-chip")).not.toBeInTheDocument();
    expect(
      api.current!.getRequestSettings().generationParams.stop,
    ).toBeUndefined();
  });

  // U4: a long pasted stop value must not force the dialog to overflow - the
  // chip truncates and exposes the full value through `title`.
  it("U4: long stop chip truncates and exposes the full value via title", async () => {
    const api = await renderWithModelAndApi(stopModel);
    await openDialog();

    const long = `STOP-${"x".repeat(90)}`;
    act(() => api.current!.setStopSequences([long]));

    const chip = screen.getByTestId("stop-sequence-chip");
    expect(chip).toHaveAttribute("title", long);

    const truncated = chip.querySelector(".truncate");
    expect(truncated).not.toBeNull();
    expect(truncated).toHaveTextContent(long);
  });

  // U4: the newline-as-\n display form is preserved in the chip title too.
  it("U4: chip title shows newlines in the escaped \\n display form", async () => {
    const api = await renderWithModelAndApi(stopModel);
    await openDialog();

    act(() => api.current!.setStopSequences(["a\nb"]));

    expect(screen.getByTestId("stop-sequence-chip")).toHaveAttribute(
      "title",
      "a\\nb",
    );
  });

  // a11y: remove-button labels are indexed so they are not all identical.
  it("indexes the remove-button aria-labels", async () => {
    await renderWithModelAndApi(stopModel);
    await openDialog();

    addStopSequence("User:");
    addStopSequence("END");

    expect(
      screen.getByLabelText("Remove stop sequence 1"),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText("Remove stop sequence 2"),
    ).toBeInTheDocument();
  });

  it("shows the newline helper copy", async () => {
    await renderWithModelAndApi(stopModel);
    await openDialog();

    expect(
      screen.getByText(/Type \\n for a newline\. Max 4 sequences\./),
    ).toBeInTheDocument();
  });

  it("Reset all clears the chips", async () => {
    const api = await renderWithModelAndApi(stopModel);
    const user = await openDialog();

    addStopSequence("User:");
    expect(screen.getByTestId("stop-sequence-chip")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Reset all" }));

    expect(screen.queryByTestId("stop-sequence-chip")).not.toBeInTheDocument();
    expect(
      api.current!.getRequestSettings().generationParams.stop,
    ).toBeUndefined();
  });
});

// ── Context budget UI cap (schema consistency) ───────────────────

describe("Context budget UI max cap", () => {
  it("caps getContextBudgetUiMax at the contract maximum", () => {
    expect(
      getContextBudgetUiMax({ ...fullModel, context_length: 3_000_000 }),
    ).toBe(CONTEXT_BUDGET_UI_MAX);
    expect(CONTEXT_BUDGET_UI_MAX).toBe(2_000_000);
  });

  it("keeps smaller model context lengths unchanged", () => {
    expect(
      getContextBudgetUiMax({ ...fullModel, context_length: 128000 }),
    ).toBe(128000);
  });

  it("keeps the fallback when the model is unknown", () => {
    expect(getContextBudgetUiMax(undefined)).toBe(32768);
    expect(getContextBudgetUiMax(null)).toBe(32768);
  });

  it("caps the Context budget control for a >2M-context model", async () => {
    await renderWithModel({ ...fullModel, context_length: 3_000_000 });
    await openDialog();

    expect(screen.getByLabelText("Context budget value")).toHaveAttribute(
      "max",
      "2000000",
    );
  });

  // ── v1.1 FF7: sampling scalars survive a provider remount (vault re-lock).
  // Stop sequences survive too, but from the VAULT rather than localStorage -
  // they are character names, i.e. user content, which browser storage is
  // closed to (privacy rule S-09b).
  describe("FF7 persistence", () => {
    /** The settings endpoint, with the server's own clamp/dedupe behaviour. */
    function stubSettingsStore() {
      let stored: string[] = [];
      vi.stubGlobal(
        "fetch",
        vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
          const url = String(input);
          const json = (data: unknown) =>
            new Response(JSON.stringify(data), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            });
          if (url.includes("/settings/stop-sequences")) {
            const body = JSON.parse(String(init?.body)) as {
              stop_sequences: string[];
            };
            stored = [...new Set(body.stop_sequences.filter(Boolean))].slice(0, 4);
            return json({ ok: true, stop_sequences: stored });
          }
          if (url.includes("/settings")) {
            return json({
              ...settingsFixture,
              stop_sequences: stored,
            });
          }
          return json({});
        }),
      );
    }

    beforeEach(() => {
      stubSettingsStore();
      // Reset the persisted slice to defaults for a clean baseline.
      useUiStore.getState().setGenSettings({
        genTemperature: 0.8,
        genTopP: 0.9,
        genTopK: 40,
        genRepetitionPenalty: 1.05,
        genMaxOutput: 1024,
        genSeed: "",
        genContextBudget: 16384,
      });
    });

    function ProbeOnly() {
      const api = useGenerationSettings();
      probeRef.current = api;
      return null;
    }
    const probeRef: {
      current: ReturnType<typeof useGenerationSettings> | null;
    } = { current: null };

    it("temperature/max output rehydrate after a remount, and so do stop sequences", async () => {
      const { unmount } = render(
        <QueryClientProvider client={new QueryClient()}>
          <GenerationSettingsProvider>
            <ProbeOnly />
          </GenerationSettingsProvider>
        </QueryClientProvider>,
      );

      act(() => {
        probeRef.current!.setSetting("temperature", 1.3);
        probeRef.current!.setSetting("max_tokens", 512);
        probeRef.current!.setStopSequences(["Alice", "Bob"]);
      });

      // Persisted slice mirrors the sampling scalars (write-through).
      expect(useUiStore.getState().genTemperature).toBe(1.3);
      expect(useUiStore.getState().genMaxOutput).toBe(512);

      // The stop sequences go to the VAULT, so the write is a round trip -
      // let it land before tearing the provider down.
      await waitFor(() => {
        expect(
          (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.some(
            ([url]) => String(url).includes("/settings/stop-sequences"),
          ),
        ).toBe(true);
      });

      // Simulate the VaultGate remounting the provider on lock/unlock.
      unmount();
      probeRef.current = null;
      render(
        <QueryClientProvider client={new QueryClient()}>
          <GenerationSettingsProvider>
            <ProbeOnly />
          </GenerationSettingsProvider>
        </QueryClientProvider>,
      );

      // Sampling scalars rehydrated from the persisted slice.
      expect(probeRef.current!.settings.temperature).toBe(1.3);
      expect(probeRef.current!.settings.max_tokens).toBe(512);
      // Stop sequences survive too, from the VAULT rather than from
      // localStorage. They are character names - user content - so browser
      // storage is closed to them (privacy rule S-09b); keeping them in memory
      // meant retyping them every session and after every vault lock. The
      // encrypted settings table keeps the rule and drops the retyping.
      await waitFor(() => {
        expect(probeRef.current!.stopSequences).toEqual(["Alice", "Bob"]);
      });
    });
  });
});

/**
 * Stop sequences round-trip through the vault, so the local mirror has the two
 * failure modes every write-through mirror has.
 */
describe("stop sequences: the vault mirror", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  function ProbeOnly({
    take,
  }: {
    take: (api: ReturnType<typeof useGenerationSettings>) => void;
  }) {
    take(useGenerationSettings());
    return null;
  }

  function renderProbe(fetchImpl: typeof fetch) {
    vi.stubGlobal("fetch", fetchImpl);
    const ref: { current: ReturnType<typeof useGenerationSettings> | null } = {
      current: null,
    };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <GenerationSettingsProvider>
          <ProbeOnly take={(api) => (ref.current = api)} />
        </GenerationSettingsProvider>
      </QueryClientProvider>,
    );
    return ref;
  }

  it("reverts to what the vault holds when the save fails", async () => {
    // Keeping the mirror would leave the chips showing a value that was never
    // saved, contradicting the error toast raised beside them.
    const ref = renderProbe(
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/settings/stop-sequences")) {
          return new Response(JSON.stringify({ detail: "vault_locked" }), {
            status: 423,
          });
        }
        return new Response(
          JSON.stringify({ ...settingsFixture, stop_sequences: ["Kept"] }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }) as unknown as typeof fetch,
    );

    await waitFor(() => expect(ref.current!.stopSequences).toEqual(["Kept"]));
    act(() => ref.current!.setStopSequences(["Never saved"]));
    await waitFor(() => expect(ref.current!.stopSequences).toEqual(["Kept"]));
  });

  it("a slow first save does not clobber a newer edit", async () => {
    // Two quick edits: the FIRST response must not retire the SECOND one's
    // mirror, or the older list flashes back until the second lands.
    let releaseFirst: (() => void) | null = null;
    let call = 0;
    const ref = renderProbe(
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const ok = (body: unknown) =>
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        if (url.includes("/settings/stop-sequences")) {
          const sent = JSON.parse(String(init?.body)).stop_sequences;
          call += 1;
          if (call === 1) {
            await new Promise<void>((r) => {
              releaseFirst = r;
            });
          }
          return ok({ ok: true, stop_sequences: sent });
        }
        return ok({ ...settingsFixture, stop_sequences: [] });
      }) as unknown as typeof fetch,
    );

    await waitFor(() => expect(ref.current).not.toBeNull());
    act(() => ref.current!.setStopSequences(["first"]));
    act(() => ref.current!.setStopSequences(["second"]));
    expect(ref.current!.stopSequences).toEqual(["second"]);

    await act(async () => {
      releaseFirst?.();
      await Promise.resolve();
    });
    // Still the newer edit, never a flash back to "first".
    await waitFor(() => expect(ref.current!.stopSequences).toEqual(["second"]));
  });
});
