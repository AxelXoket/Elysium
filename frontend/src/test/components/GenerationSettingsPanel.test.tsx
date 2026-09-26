import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { TooltipProvider } from "@/components/ui/tooltip";
import {
  CONTEXT_BUDGET_UI_MAX,
  GenerationSettingsProvider,
  SAMPLER_KEYS,
  SAMPLER_UNSET_DISPLAY,
  getContextBudgetUiMax,
  useGenerationSettings,
} from "@/components/generation/GenerationSettingsContext";
import { ModelPanel } from "@/components/models/ModelPanel";
import {
  IMAGE_OUTPUT_NOT_ASKED_NOTE,
  IMAGE_OUTPUT_UNKNOWN_NOTE,
} from "@/components/generation/ImageOutputSetting";
import { GEN_PERSISTED_DEFAULTS, useUiStore } from "@/lib/store/uiStore";
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

function Extra({ children }: { children: ReactNode }) {
  return (
    <TooltipProvider>
      <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
    </TooltipProvider>
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

  renderWithQueryClient(<ModelPanel />, { wrapper: Extra });

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

  // The handle is passed in rather than closed over, matching the `take`
  // shape further down this file. Same moment - the write still happens during
  // the probe's render - but the assignment no longer sits inside a component,
  // which is what react-hooks/immutability is actually about.
  function Probe({
    take,
  }: {
    take: (api: ReturnType<typeof useGenerationSettings>) => void;
  }) {
    take(useGenerationSettings());
    return null;
  }

  useUiStore.setState({ selectedModelId: model.id });
  mockDialogFetch(model);

  renderWithQueryClient(
    <>
      <ModelPanel />
      <Probe take={(next) => (api.current = next)} />
    </>,
    { wrapper: Extra },
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

    // D1: the default is UNSET: an empty box that says "default", not 0.8
    // and not the display number either.
    const box = screen.getByLabelText("Temperature value");
    expect(box).toHaveValue(null);
    expect(box).toHaveAttribute("placeholder", "default");
    expect(screen.getByLabelText("Temperature slider")).toHaveAttribute(
      "aria-valuetext",
      "Provider default",
    );
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

  function Harness({
    take,
  }: {
    take: (api: ReturnType<typeof useGenerationSettings>) => void;
  }) {
    take(useGenerationSettings());
    return null;
  }

  function SettingsOnly({ children }: { children: ReactNode }) {
    return <GenerationSettingsProvider>{children}</GenerationSettingsProvider>;
  }

  renderWithQueryClient(<Harness take={(next) => (api.current = next)} />, {
    wrapper: SettingsOnly,
  });

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
  it("keeps stop chips removable on a model that cannot use them", async () => {
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
  it("shortens a long stop chip but keeps the whole value reachable", async () => {
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
  it("shows a newline in a stop chip as the escape that was typed", async () => {
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

    function ProbeOnly({
      take,
    }: {
      take: (api: ReturnType<typeof useGenerationSettings>) => void;
    }) {
      take(useGenerationSettings());
      return null;
    }
    const probeRef: {
      current: ReturnType<typeof useGenerationSettings> | null;
    } = { current: null };
    const keep = (next: ReturnType<typeof useGenerationSettings>) => {
      probeRef.current = next;
    };

    function SettingsOnly({ children }: { children: ReactNode }) {
      return <GenerationSettingsProvider>{children}</GenerationSettingsProvider>;
    }

    it("temperature/max output rehydrate after a remount, and so do stop sequences", async () => {
      const { unmount } = renderWithQueryClient(<ProbeOnly take={keep} />, {
        wrapper: SettingsOnly,
      });

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
      renderWithQueryClient(<ProbeOnly take={keep} />, { wrapper: SettingsOnly });

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

  function SettingsOnly({ children }: { children: ReactNode }) {
    return <GenerationSettingsProvider>{children}</GenerationSettingsProvider>;
  }

  function renderProbe(fetchImpl: typeof fetch) {
    vi.stubGlobal("fetch", fetchImpl);
    const ref: { current: ReturnType<typeof useGenerationSettings> | null } = {
      current: null,
    };
    renderWithQueryClient(<ProbeOnly take={(api) => (ref.current = api)} />, {
      wrapper: SettingsOnly,
    });
    return ref;
  }

  it("reverts to what the vault holds when the save fails, and says why", async () => {
    // Keeping the mirror would leave the chips showing a value that was never
    // saved, contradicting the error toast raised beside them.
    //
    // That toast is the half this test named and never checked. Until
    // KADEME 18b, deleting the pushError from the onError handler left the
    // ENTIRE frontend suite green, so the revert was pinned and the sentence
    // explaining it was not - which is the worse half to lose: chips that
    // silently snap back read as a UI glitch, not as a refused write.
    const { useErrorStore } = await import("@/lib/errors");
    useErrorStore.getState().clearAll();

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

    await waitFor(() =>
      expect(
        useErrorStore.getState().errors,
        "the chips snapped back and nothing said the vault refused them",
      ).toHaveLength(1),
    );
    useErrorStore.getState().clearAll();
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

/**
 * KUSUR-DEFTERI K-22, second half: the pictures toggle.
 *
 * The narration voice a page over at least corrects itself out loud enough to
 * see. This one has no failure surface at all - `useSetImageOutput` carries no
 * onError, the caller passes no options object, and nothing anywhere reads
 * `.isError` - so a refused write is indistinguishable from a click that did
 * not register. There is no global mutation handler to catch it either:
 * lib/query/queryClient.ts sets defaults for queries only, and the whole of
 * src/ contains no MutationCache.
 *
 * The ledger recorded that no characterisation test was possible here,
 * because pinning "nothing is visible" would pass against a component that
 * never rendered. It is possible with a control beside it: the success case
 * proves the switch moves when the vault agrees, so the failure case saying
 * it did NOT move means something.
 */
describe("pictures in replies: a toggle with no failure surface", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  const pictureModel: Model = {
    ...fullModel,
    id: "test/pictures",
    output_modalities: ["text", "image"],
  };

  /** Renders the dialog with the pictures switch reachable and Off. */
  async function renderPictures(refuse: boolean) {
    let stored = false;
    let posted = 0;
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      });

    useUiStore.setState({ selectedModelId: pictureModel.id });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/settings/image-output")) {
          posted += 1;
          if (refuse) return json({ detail: "vault_locked" }, 423);
          stored = JSON.parse(String(init?.body)).image_output_enabled;
          // `ok` is not decoration: ImageOutputResponseSchema requires it, and
          // leaving it out made the success case reject in the parser instead
          // of the wire, which looked exactly like the refusal below.
          return json({ ok: true, image_output_enabled: stored });
        }
        if (url.includes("/settings/stop-sequences")) return json({ ok: true, stop_sequences: [] });
        if (url.includes("/models/openrouter")) return json(modelList(pictureModel));
        return json({ ...settingsFixture, image_output_enabled: stored });
      }),
    );

    renderWithQueryClient(<ModelPanel />, { wrapper: Extra });
    await waitFor(() => {
      expect(screen.getByTestId("generation-settings-trigger")).toBeInTheDocument();
    });
    await openDialog();

    const toggle = await screen.findByRole("switch", {
      name: "Let replies include pictures",
    });
    expect(toggle, "the switch did not start from the stored value")
      .toHaveAttribute("aria-checked", "false");
    return { toggle, sent: () => posted };
  }

  it("turns the pictures switch on when the vault takes it", async () => {
    // The control. Without this, the test below would pass against a switch
    // that never moves for any reason at all.
    const { toggle, sent } = await renderPictures(false);

    await userEvent.click(toggle);
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
    expect(sent()).toBe(1);
  });

  it("leaves the switch where it was when the vault refuses, and says so", async () => {
    // REWRITTEN for K-22. The last two assertions used to require silence.
    //
    // The mutation had no onError at all and its caller passes no options, so
    // a refused write snapped the switch back with nothing said anywhere - a
    // rejected save and a mis-registered click looked identical. The switch
    // not moving is still correct and is still asserted; what is new is that
    // the refusal now reaches the error store, which maps the backend's code
    // to its catalogued sentence rather than inventing one here.
    const { useErrorStore } = await import("@/lib/errors");
    useErrorStore.getState().clearAll();
    const { toggle, sent } = await renderPictures(true);

    await userEvent.click(toggle);

    await waitFor(() => expect(sent(), "the click went nowhere").toBe(1));
    expect(
      toggle,
      "the switch moved on a refused write, which would be the worse bug",
    ).toHaveAttribute("aria-checked", "false");
    await waitFor(() =>
      expect(
        useErrorStore.getState().errors,
        "the vault refused and the user was told nothing",
      ).toHaveLength(1),
    );
  });
});

/**
 * H-63: the switch is on, so what does the panel CLAIM about the request?
 *
 * The backend adds `modalities: ["text","image"]` unless the cached metadata
 * positively rules image output out - absent metadata, a missing field and an
 * empty list all mean "ask, the provider decides" (_model_emits_images,
 * backend/routers/completions.py, pinned by the shared truth table both suites
 * read). The panel used to read that same empty list as a definite no and
 * print "it will not be asked for one" while the request went out asking.
 *
 * A privacy-first interface is allowed to say "I do not know". It is not
 * allowed to say "no" about a request that says yes.
 *
 * Both sentences are IMPORTED from the component, so a copy edit cannot make
 * an absence assertion pass by accident.
 */
describe("pictures in replies: what the panel claims about the request", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  /** Opens the dialog with the switch already ON and `model` selected. */
  async function renderWithPicturesOn(model: Model) {
    const json = (body: unknown) =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });

    useUiStore.setState({ selectedModelId: model.id });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/settings/stop-sequences")) {
          return json({ ok: true, stop_sequences: [] });
        }
        if (url.includes("/models/openrouter")) return json(modelList(model));
        return json({ ...settingsFixture, image_output_enabled: true });
      }),
    );

    renderWithQueryClient(<ModelPanel />, { wrapper: Extra });
    await waitFor(() => {
      expect(screen.getByTestId("generation-settings-trigger")).toBeInTheDocument();
    });
    await openDialog();

    // The positive control for every absence assertion below. If the switch
    // were off, neither note would render for any model and "the sentence is
    // not there" would be worth nothing.
    const toggle = await screen.findByRole("switch", {
      name: "Let replies include pictures",
    });
    expect(toggle, "the harness did not reach the state under test")
      .toHaveAttribute("aria-checked", "true");
  }

  const emptyList: Model = {
    ...fullModel,
    id: "test/no-modality-list",
    // What the backend caches for a model whose OpenRouter architecture block
    // omits output_modalities. This is the reachable case, not a contrivance.
    output_modalities: [],
  };

  const textOnly: Model = {
    ...fullModel,
    id: "test/text-only-output",
    output_modalities: ["text"],
  };

  const drawing: Model = {
    ...fullModel,
    id: "test/draws",
    output_modalities: ["text", "image"],
  };

  it("does not claim a picture will not be asked for when it will be", async () => {
    await renderWithPicturesOn(emptyList);

    expect(
      screen.queryByText(IMAGE_OUTPUT_NOT_ASKED_NOTE),
      "the panel told the reader the request would not ask, and it does",
    ).toBeNull();
  });

  it("says what it actually knows about that model instead", async () => {
    // Positive control, same harness and same render as the assertion above:
    // the block is reached and does produce copy, so the missing sentence is a
    // real difference and not an empty render.
    await renderWithPicturesOn(emptyList);

    expect(screen.getByText(IMAGE_OUTPUT_UNKNOWN_NOTE)).toBeInTheDocument();
  });

  it("still says it plainly when the model's list rules image output out", async () => {
    // Ground control. The definite-no path is the one that was always right
    // and it has to survive the fix untouched.
    await renderWithPicturesOn(textOnly);

    expect(screen.getByText(IMAGE_OUTPUT_NOT_ASKED_NOTE)).toBeInTheDocument();
    expect(screen.queryByText(IMAGE_OUTPUT_UNKNOWN_NOTE)).toBeNull();
  });

  it("says neither when the model declares image output", async () => {
    await renderWithPicturesOn(drawing);

    expect(screen.queryByText(IMAGE_OUTPUT_NOT_ASKED_NOTE)).toBeNull();
    expect(screen.queryByText(IMAGE_OUTPUT_UNKNOWN_NOTE)).toBeNull();
  });
});

// D1: samplers default to UNSET, and an unset sampler is not sent at all.
describe("Generation Settings unset samplers (D1)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockDialogFetch(fullModel);
    useUiStore.setState({ ...GEN_PERSISTED_DEFAULTS });
  });

  it("ground: a fresh store sends max_tokens and no sampler key at all", () => {
    const api = renderSettingsHarness();
    const params = api.current!.getRequestSettings().generationParams;
    expect(params.max_tokens).toBe(GEN_PERSISTED_DEFAULTS.genMaxOutput);
    for (const key of SAMPLER_KEYS) {
      expect(key in params, `${key} was sent while unset`).toBe(false);
    }
  });

  it("a sampler the user sets is sent, and only that one", () => {
    const api = renderSettingsHarness();
    act(() => api.current!.setSetting("temperature", 0.7));
    const params = api.current!.getRequestSettings().generationParams;
    expect(params.temperature).toBe(0.7);
    for (const key of SAMPLER_KEYS.filter((k) => k !== "temperature")) {
      expect(key in params).toBe(false);
    }
  });

  it("a set value equal to an old default is still sent once chosen", () => {
    const api = renderSettingsHarness();
    act(() => api.current!.setSetting("repetition_penalty", 1.05));
    expect(
      api.current!.getRequestSettings().generationParams.repetition_penalty,
    ).toBe(1.05);
  });

  it("clearing a sampler takes it out of the request again", () => {
    const api = renderSettingsHarness();
    act(() => api.current!.setSetting("top_k", 25));
    expect(api.current!.getRequestSettings().generationParams.top_k).toBe(25);
    act(() => api.current!.setSetting("top_k", null));
    expect("top_k" in api.current!.getRequestSettings().generationParams).toBe(false);
    expect(useUiStore.getState().genTopK).toBeNull();
  });

  it("the dialog shows an unset sampler as the provider default", async () => {
    await renderWithModel(fullModel);
    await openDialog();
    // An empty box with "default" in it: no number the request will not carry.
    const box = screen.getByLabelText("Top P value");
    expect(box).toHaveValue(null);
    expect(box).toHaveAttribute("placeholder", "default");
    // The slider still has a resting place, and says what it means.
    const slider = screen.getByLabelText("Top P slider");
    expect(slider).toHaveValue(String(SAMPLER_UNSET_DISPLAY.top_p));
    expect(slider).toHaveAttribute("aria-valuetext", "Provider default");
    // Positive control: no clear button while nothing is set.
    expect(
      screen.queryByRole("button", { name: "Use the provider default for Top P" }),
    ).toBeNull();
  });

  it("the clear button returns a set sampler to unset", async () => {
    const api = await renderWithModelAndApi(fullModel);
    await openDialog();
    fireEvent.change(screen.getByLabelText("Top P slider"), {
      target: { value: "0.5" },
    });
    expect(api.current!.getRequestSettings().generationParams.top_p).toBe(0.5);
    // Set: the box shows the number and the slider no longer says default.
    expect(screen.getByLabelText("Top P value")).toHaveValue(0.5);
    expect(screen.getByLabelText("Top P slider")).not.toHaveAttribute("aria-valuetext");

    await userEvent.click(
      screen.getByRole("button", { name: "Use the provider default for Top P" }),
    );
    expect("top_p" in api.current!.getRequestSettings().generationParams).toBe(false);
    expect(screen.getByLabelText("Top P value")).toHaveValue(null);
    expect(screen.getByLabelText("Top P value")).toHaveAttribute("placeholder", "default");
  });

  it("the dialog opens with the focus on its title: not a clear button, not the footer", async () => {
    const api = await renderWithModelAndApi(fullModel);
    act(() => api.current!.setSetting("temperature", 1.2));
    await openDialog();
    // Positive control: a clear button exists and is the first control.
    const clear = screen.getByRole("button", {
      name: "Use the provider default for Temperature",
    });
    expect(clear).toBeInTheDocument();
    // The title takes the focus: it is the first thing in the dialog, so
    // focusing it cannot scroll the dialog down (the footer Close did, and
    // opened it at "Seed"). jsdom has no layout, so the scroll itself is
    // measured in a real headless browser; here the guard is WHERE it lands.
    const title = screen.getByRole("heading", { name: /Generation Settings/ });
    await waitFor(() => expect(title).toHaveFocus());
    expect(clear).not.toHaveFocus();
    // Everything focusable in the dialog comes after it: nothing above it to
    // scroll past, and it is not a tab stop of its own.
    const sections = screen.getAllByRole("heading", { level: 3 });
    expect(title.compareDocumentPosition(sections[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(title).toHaveAttribute("tabindex", "-1");
    // Ground: the footer Close is not where the focus went.
    const footerClose = screen
      .getAllByRole("button", { name: "Close" })
      .find((b) => b.textContent?.trim() === "Close");
    expect(footerClose).toBeDefined();
    expect(footerClose).not.toHaveFocus();
  });

  it("clearing a sampler hands the focus to its box, not to the dialog", async () => {
    const api = await renderWithModelAndApi(fullModel);
    act(() => api.current!.setSetting("top_p", 0.5));
    await openDialog();
    const user = userEvent.setup();
    const clear = screen.getByRole("button", { name: "Use the provider default for Top P" });
    clear.focus();
    await user.keyboard("{Enter}");
    expect("top_p" in api.current!.getRequestSettings().generationParams).toBe(false);
    // Ground: the button that had the focus is gone.
    expect(
      screen.queryByRole("button", { name: "Use the provider default for Top P" }),
    ).toBeNull();
    expect(screen.getByLabelText("Top P value")).toHaveFocus();
  });

  it("an unset value can be typed straight into the empty box", async () => {
    const api = await renderWithModelAndApi(fullModel);
    await openDialog();
    const box = screen.getByLabelText("Top K value");
    expect(box).toHaveValue(null);
    // Ground: leaving it empty keeps it unset.
    fireEvent.blur(box);
    expect("top_k" in api.current!.getRequestSettings().generationParams).toBe(false);
    fireEvent.change(box, { target: { value: "30" } });
    fireEvent.blur(box);
    expect(api.current!.getRequestSettings().generationParams.top_k).toBe(30);
    expect(box).toHaveValue(30);
  });

  it("the persisted slice keeps null and turns junk into unset", () => {
    useUiStore.getState().setGenSettings({ genTemperature: 1.2 });
    expect(useUiStore.getState().genTemperature).toBe(1.2);
    useUiStore.getState().setGenSettings({ genTemperature: null });
    expect(useUiStore.getState().genTemperature).toBeNull();
    useUiStore
      .getState()
      .setGenSettings({ genTopP: "0.5" as unknown as number });
    expect(useUiStore.getState().genTopP).toBeNull();
    // Out of range still clamps, as before.
    useUiStore.getState().setGenSettings({ genTopP: 7 });
    expect(useUiStore.getState().genTopP).toBe(1);
  });
});
