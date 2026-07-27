/**
 * ReadingRules.test.tsx - the settings surface the pronunciation pipe never had.
 *
 * The machinery shipped long ago and was unit-tested at four layers; what did
 * not exist was any way for a person to put a rule INTO it. So the contract
 * worth testing here is the one that was missing: a rule can be added, edited
 * and - the part a merge-only endpoint could not express - removed.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

import { ReadingRulesSection } from "@/components/settings/ReadingRulesSection";

const getPronunciations = vi.hoisted(() => vi.fn());
const savePronunciations = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api/tts", () => ({ getPronunciations, savePronunciations }));

const LIMITS = { max_entries: 200, max_chars: 80 };

describe("ReadingRulesSection", () => {
  beforeEach(() => {
    getPronunciations
      .mockReset()
      .mockResolvedValue({ pronunciations: {}, ...LIMITS });
    savePronunciations
      .mockReset()
      .mockImplementation((pronunciations) =>
        Promise.resolve({ pronunciations, ...LIMITS }),
      );
  });

  it("shows the rules that are already stored", async () => {
    getPronunciations.mockResolvedValue({
      pronunciations: { Aoife: "EE-fa" },
      ...LIMITS,
    });
    render(<ReadingRulesSection />);

    expect(await screen.findByDisplayValue("Aoife")).toBeInTheDocument();
    expect(screen.getByDisplayValue("EE-fa")).toBeInTheDocument();
  });

  it("saves a rule the user types", async () => {
    render(<ReadingRulesSection />);
    fireEvent.click(await screen.findByRole("button", { name: "Add a rule" }));

    fireEvent.change(screen.getByLabelText("Written as"), {
      target: { value: "Aoife" },
    });
    fireEvent.change(screen.getByLabelText("Said as"), {
      target: { value: "EE-fa" },
    });
    fireEvent.blur(screen.getByLabelText("Said as"));

    await waitFor(() =>
      expect(savePronunciations).toHaveBeenCalledWith({ Aoife: "EE-fa" }),
    );
  });

  it("removes a rule, which a merge-only endpoint could not express", async () => {
    getPronunciations.mockResolvedValue({
      pronunciations: { Aoife: "EE-fa", Siobhan: "shiv-AWN" },
      ...LIMITS,
    });
    render(<ReadingRulesSection />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Remove rule for Aoife" }),
    );

    await waitFor(() =>
      expect(savePronunciations).toHaveBeenCalledWith({ Siobhan: "shiv-AWN" }),
    );
  });

  it("does not send a rule with no written form", async () => {
    render(<ReadingRulesSection />);
    fireEvent.click(await screen.findByRole("button", { name: "Add a rule" }));
    fireEvent.change(screen.getByLabelText("Said as"), {
      target: { value: "orphan" },
    });
    fireEvent.blur(screen.getByLabelText("Said as"));

    await waitFor(() => expect(savePronunciations).toHaveBeenCalledWith({}));
  });

  it("stays out of the way when voice is not set up", async () => {
    getPronunciations.mockRejectedValue({
      status: 404,
      detail: "tts_not_configured",
      message: "",
    });
    const { container } = render(<ReadingRulesSection />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it("says so when the settings existed and could not be read", async () => {
    // The other half of the same split DeliverySection makes: a section that
    // vanishes on a 500 presents a broken backend as a feature that does not
    // exist.
    getPronunciations.mockRejectedValue({
      status: 500,
      detail: "tts_worker_failed",
      message: "",
    });
    render(<ReadingRulesSection />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("stops offering new rules once the table is full", async () => {
    const full: Record<string, string> = {};
    for (let i = 0; i < 200; i += 1) full[`name${i}`] = "x";
    getPronunciations.mockResolvedValue({
      pronunciations: full,
      max_entries: 200,
      max_chars: 80,
    });
    render(<ReadingRulesSection />);

    const add = await screen.findByRole("button", { name: "Add a rule" });
    expect(add).toBeDisabled();
  });
});
