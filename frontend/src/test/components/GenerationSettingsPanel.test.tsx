import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { ModelPanel } from "@/components/models/ModelPanel";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "@/test/mocks/api";
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

async function renderWithModel(model: Model) {
  useUiStore.setState({ selectedModelId: model.id });
  mockFetch({
    "/models/openrouter": { body: modelList(model) },
  });

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

describe("Generation Settings Panel", () => {
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

    fireEvent.change(screen.getByLabelText("Temperature value"), {
      target: { value: "1.5" },
    });
    expect(screen.getByLabelText("Temperature value")).toHaveValue(1.5);

    await userEvent.click(screen.getByRole("button", { name: "Reset all" }));

    expect(screen.getByLabelText("Temperature value")).toHaveValue(0.8);
  });

  it("keeps slider and numeric input in sync", async () => {
    await renderWithModel(fullModel);
    await openDialog();

    fireEvent.change(screen.getByLabelText("Temperature slider"), {
      target: { value: "1.2" },
    });

    expect(screen.getByLabelText("Temperature value")).toHaveValue(1.2);
  });

  it("does not allow numeric inputs to exceed the UI range", async () => {
    await renderWithModel(fullModel);
    await openDialog();

    fireEvent.change(screen.getByLabelText("Top K value"), {
      target: { value: "999" },
    });

    expect(screen.getByLabelText("Top K value")).toHaveValue(200);
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
