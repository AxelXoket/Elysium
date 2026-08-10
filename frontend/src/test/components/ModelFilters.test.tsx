/**
 * Finding a model by what it can DO, not by what it is called.
 *
 * The catalogue runs to a couple of hundred entries and the only way through it
 * was the name. Meanwhile the capability data has been fetched, parsed and
 * stored per model all along - input_modalities and output_modalities - and was
 * spent on two badges that looked identical to each other.
 *
 * So: no new backend surface, no new request. The row narrows the list the
 * search box already narrows, and it speaks the same language.
 */
import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import type { ReactNode } from "react";

import { ModelPanel } from "@/components/models/ModelPanel";
import { TooltipProvider } from "@/components/ui/tooltip";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import {
  availableModalities,
  capabilityScore,
  matchesFilter,
} from "@/components/models/ModelFilters";
import { mockFetch } from "@/test/mocks/api";
import { modelFixture, modelListFixture } from "@/test/mocks/fixtures";
import { useErrorStore } from "@/lib/errors";
import type { Model } from "@/lib/schemas/models";

function wrapper({ children }: { children: ReactNode }) {
  return (
    
      <TooltipProvider>
        <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
      </TooltipProvider>
    
  );
}

function model(
  id: string,
  name: string,
  input: string[],
  output: string[],
): Model {
  return {
    ...modelFixture,
    id,
    name,
    input_modalities: input,
    output_modalities: output,
  };
}

const PLAIN = model("v/plain", "Plain Text Model", ["text"], ["text"]);
const VISION = model("v/vision", "Vision Reader", ["text", "image"], ["text"]);
const DRAWER = model("v/drawer", "Picture Maker", ["text"], ["text", "image"]);
const BOTH = model("v/both", "Sees And Draws", ["text", "image"],
                   ["text", "image"]);
const SPEAKER = model("v/speaker", "Voice Maker", ["text"], ["text", "audio"]);

const CATALOGUE = {
  ...modelListFixture,
  count: 5,
  models: [PLAIN, VISION, DRAWER, BOTH, SPEAKER],
};

// ── the pure parts ──────────────────────────────────────────────────────────

describe("capability helpers", () => {
  it("offers only capabilities some model actually has", () => {
    const { input, output } = availableModalities([PLAIN, VISION]);
    expect(input.map(([k]) => k)).toEqual(["image"]);
    expect(output).toEqual([]);
  });

  it("counts how many models have each, so the row is a decision not a guess", () => {
    const { input, output } = availableModalities(CATALOGUE.models);
    expect(input).toEqual([["image", 2]]);
    expect(output).toEqual([["image", 2], ["audio", 1]]);
  });

  it("never offers text, because filtering on it filters nothing", () => {
    const { input, output } = availableModalities([PLAIN]);
    expect(input).toEqual([]);
    expect(output).toEqual([]);
  });

  it("ANDs the selections - two picks means a model that does both", () => {
    const both = { input: ["image"], output: ["image"] };
    expect(matchesFilter(BOTH, both)).toBe(true);
    expect(matchesFilter(VISION, both)).toBe(false);
    expect(matchesFilter(DRAWER, both)).toBe(false);
  });

  it("an empty filter matches everything", () => {
    expect(matchesFilter(PLAIN, { input: [], output: [] })).toBe(true);
  });

  it("ranks producing above reading", () => {
    expect(capabilityScore(DRAWER)).toBeGreaterThan(capabilityScore(VISION));
    expect(capabilityScore(BOTH)).toBeGreaterThan(capabilityScore(DRAWER));
    expect(capabilityScore(PLAIN)).toBe(0);
  });
});

// ── the row, against the real panel ─────────────────────────────────────────

describe("the capability filter row", () => {
  beforeEach(() => {
    useErrorStore.getState().clearAll();
    mockFetch({ "/models/openrouter": { body: CATALOGUE } });
  });

  afterEach(() => {
    useErrorStore.getState().clearAll();
    vi.restoreAllMocks();
  });

  async function open() {
    renderWithQueryClient(<ModelPanel />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText("Plain Text Model")).toBeInTheDocument();
    });
  }

  it("keeps only the models that read pictures", async () => {
    await open();
    await userEvent.click(
      screen.getByRole("checkbox", { name: /read image/i }),
    );

    expect(screen.getByText("Vision Reader")).toBeInTheDocument();
    expect(screen.getByText("Sees And Draws")).toBeInTheDocument();
    expect(screen.queryByText("Plain Text Model")).not.toBeInTheDocument();
    expect(screen.queryByText("Picture Maker")).not.toBeInTheDocument();
  });

  it("keeps only the models that make pictures", async () => {
    await open();
    await userEvent.click(
      screen.getByRole("checkbox", { name: /make image/i }),
    );

    expect(screen.getByText("Picture Maker")).toBeInTheDocument();
    expect(screen.getByText("Sees And Draws")).toBeInTheDocument();
    expect(screen.queryByText("Vision Reader")).not.toBeInTheDocument();
  });

  it("two picks means both, not either", async () => {
    await open();
    await userEvent.click(screen.getByRole("checkbox", { name: /read image/i }));
    await userEvent.click(screen.getByRole("checkbox", { name: /make image/i }));

    expect(screen.getByText("Sees And Draws")).toBeInTheDocument();
    expect(screen.queryByText("Vision Reader")).not.toBeInTheDocument();
    expect(screen.queryByText("Picture Maker")).not.toBeInTheDocument();
  });

  it("says how many are left", async () => {
    await open();
    await userEvent.click(screen.getByRole("checkbox", { name: /make image/i }));
    expect(screen.getByText("2 models")).toBeInTheDocument();
  });

  it("clears back to the whole catalogue", async () => {
    await open();
    await userEvent.click(screen.getByRole("checkbox", { name: /read image/i }));
    expect(screen.queryByText("Plain Text Model")).not.toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: /clear capability filters/i }),
    );
    expect(screen.getByText("Plain Text Model")).toBeInTheDocument();
  });

  it("combines with the search box rather than replacing it", async () => {
    await open();
    await userEvent.click(screen.getByRole("checkbox", { name: /read image/i }));
    await userEvent.type(screen.getByLabelText("Search models"), "Vision");

    expect(screen.getByText("Vision Reader")).toBeInTheDocument();
    expect(screen.queryByText("Sees And Draws")).not.toBeInTheDocument();
  });

  it("explains an empty result that the search box did not cause", async () => {
    // Reads pictures AND makes sound: nothing in the catalogue does both, and
    // the search box is untouched - so "no models match your search" would read
    // as a bug rather than as the filter doing its job.
    await open();
    await userEvent.click(screen.getByRole("checkbox", { name: /read image/i }));
    await userEvent.click(screen.getByRole("checkbox", { name: /make audio/i }));
    expect(await screen.findByTestId("model-filter-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("model-search-empty")).not.toBeInTheDocument();
  });

  it("hides itself entirely when the catalogue offers no choices", async () => {
    vi.restoreAllMocks();
    mockFetch({
      "/models/openrouter": {
        body: { ...modelListFixture, count: 1, models: [PLAIN] },
      },
    });
    renderWithQueryClient(<ModelPanel />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText("Plain Text Model")).toBeInTheDocument();
    });
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});

// ── sorting ─────────────────────────────────────────────────────────────────

describe("most capable first", () => {
  beforeEach(() => {
    useErrorStore.getState().clearAll();
    mockFetch({ "/models/openrouter": { body: CATALOGUE } });
  });

  afterEach(() => {
    useErrorStore.getState().clearAll();
    vi.restoreAllMocks();
  });

  it("is off until asked for, so the catalogue keeps its own order", async () => {
    renderWithQueryClient(<ModelPanel />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText("Plain Text Model")).toBeInTheDocument();
    });
    const toggle = screen.getByRole("switch", { name: /most capable first/i });
    expect(toggle).toHaveAttribute("aria-checked", "false");

    const names = screen
      .getAllByRole("button", { name: /^Select model/i })
      .map((b) => within(b).getByText(/Model|Reader|Maker|Draws/).textContent);
    expect(names[0]).toBe("Plain Text Model");
  });

  it("puts the models that can do the most at the top", async () => {
    renderWithQueryClient(<ModelPanel />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText("Plain Text Model")).toBeInTheDocument();
    });
    await userEvent.click(
      screen.getByRole("switch", { name: /most capable first/i }),
    );

    const names = screen
      .getAllByRole("button", { name: /^Select model/i })
      .map((b) => within(b).getByText(/Model|Reader|Maker|Draws/).textContent);
    expect(names[0]).toBe("Sees And Draws");
    expect(names[names.length - 1]).toBe("Plain Text Model");
  });
});
