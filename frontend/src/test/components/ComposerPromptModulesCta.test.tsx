/**
 * The chat's error band for a module refusal carries a shortcut to the page
 * that fixes it (Settings > Prompt modules), the same way a key error
 * carries one to Security.
 *
 * Ground: an unrelated error has no Prompt modules shortcut. Positive
 * control: the key error still gets its Security one in this same harness,
 * so a band that rendered no shortcut at all could not pass as "absent".
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type ReactNode } from "react";

import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { Composer } from "@/components/chat/Composer";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "../mocks/api";
import { settingsFixture } from "../mocks/fixtures";

const OVER_BUDGET = {
  status: 409,
  detail: "prompt_modules_over_budget",
  overBudget: { modules: [{ id: 1, name: "Pacing", tokens: 4200 }] },
};

function wrapper({ children }: { children: ReactNode }) {
  return <GenerationSettingsProvider>{children}</GenerationSettingsProvider>;
}

function Harness({ error }: { error: unknown }) {
  const [draft, setDraft] = useState("");
  return (
    <Composer
      onSend={() => undefined}
      isPending={false}
      sendError={error}
      draft={draft}
      onDraftChange={setDraft}
    />
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  mockFetch({ "/settings": { body: settingsFixture } });
  useUiStore.setState({
    selectedChatId: 1,
    selectedModelId: "openai/gpt-4o",
    settingsOpen: false,
    settingsInitialPage: null,
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
  useUiStore.setState({ settingsOpen: false, settingsInitialPage: null });
});

describe("the over-budget error band", () => {
  it("offers Go to Prompt modules, which opens that settings page", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(<Harness error={OVER_BUDGET} />, { wrapper });
    const cta = await screen.findByRole("button", { name: "Go to Prompt modules" });
    // Not the Security shortcut: the fix is not a key.
    expect(screen.queryByRole("button", { name: "Go to Security" })).toBeNull();
    await user.click(cta);
    expect(useUiStore.getState().settingsOpen).toBe(true);
    expect(useUiStore.getState().settingsInitialPage).toBe("prompt");
  });

  it("GROUND: a key error gets Security, not Prompt modules", async () => {
    renderWithQueryClient(
      <Harness error={{ status: 401, detail: "api_key_missing" }} />,
      { wrapper },
    );
    expect(await screen.findByRole("button", { name: "Go to Security" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Go to Prompt modules" })).toBeNull();
  });

  it("GROUND: an unrelated error has no shortcut at all", async () => {
    renderWithQueryClient(
      <Harness error={{ status: 500, detail: "internal_error" }} />,
      { wrapper },
    );
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Go to Prompt modules" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Go to Security" })).toBeNull();
  });
});
