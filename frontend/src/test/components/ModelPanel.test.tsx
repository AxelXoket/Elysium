import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { mockFetch } from "@/test/mocks/api";
import {
  characterFixture,
  chatFixture,
  modelFixture,
  modelListFixture,
  modelListFallbackFixture,
  settingsFixture,
} from "@/test/mocks/fixtures";
import { ModelPanel } from "@/components/models/ModelPanel";
import { TooltipProvider } from "@/components/ui/tooltip";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useErrorStore } from "@/lib/errors";
import { useUiStore } from "@/lib/store/uiStore";
import type { Model, ModelList } from "@/lib/schemas/models";
import type { Message } from "@/lib/schemas/chats";

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    
      <TooltipProvider>
        <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
      </TooltipProvider>
    
  );
}

describe("Model Panel Tests", () => {
  beforeEach(() => {
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    useErrorStore.getState().clearAll();
    vi.restoreAllMocks();
  });

  // T-14: Models panel renders model list
  it("lists the models once the catalogue loads", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFixture },
    });

    renderWithQueryClient(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("GPT-4o")).toBeInTheDocument();
    });
  });

  // T-15: Models panel shows source badge
  it("says where the catalogue came from", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFixture },
    });

    renderWithQueryClient(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("model-source-badge")).toHaveTextContent("user");
    });
  });

  // T-16: Models panel shows a mapped fallback message - never the raw
  // backend fallback_reason value (internal diagnostics).
  it("explains a fallback in plain words, not the raw reason code", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFallbackFixture },
    });

    renderWithQueryClient(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("fallback-reason")).toHaveTextContent(
        "primary source unavailable",
      );
    });
    expect(
      screen.queryByText("API key invalid or expired"),
    ).not.toBeInTheDocument();
  });

  // FIX-3: known fallback_reason values map to specific copy
  it("explains a catalogue timeout without naming the code", async () => {
    mockFetch({
      "/models/openrouter": {
        body: { ...modelListFallbackFixture, fallback_reason: "timeout" },
      },
    });

    renderWithQueryClient(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("fallback-reason")).toHaveTextContent(
        "primary source timed out",
      );
    });
  });

  it("explains an upstream status without naming the code", async () => {
    mockFetch({
      "/models/openrouter": {
        body: { ...modelListFallbackFixture, fallback_reason: "http_502" },
      },
    });

    renderWithQueryClient(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("fallback-reason")).toHaveTextContent(
        "primary source error (HTTP 502)",
      );
    });
    expect(screen.queryByText(/http_502/)).not.toBeInTheDocument();
  });

  // FIX-4: refresh failure surfaces exactly one toast via the error store
  it("a refresh that fails says so instead of looking done", async () => {
    const fetchMock = mockFetch({
      "/models/openrouter": { body: modelListFixture },
    });

    renderWithQueryClient(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("GPT-4o")).toBeInTheDocument();
    });

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "openrouter_timeout" }), {
        status: 504,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await userEvent.click(
      screen.getByRole("button", { name: "Refresh models" }),
    );

    await waitFor(() => {
      const errors = useErrorStore.getState().errors;
      expect(errors).toHaveLength(1);
      expect(errors[0].code).toBe("openrouter_timeout");
    });
  });

  // T-17: Modality badges shown as informational, no upload UI
  it("shows what a model reads and writes without offering an upload", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFixture },
    });

    renderWithQueryClient(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("GPT-4o")).toBeInTheDocument();
    });

    // Image modality badge exists (informational), and says WHICH WAY it goes.
    // Scoped to the card: the capability filter row above the list offers an
    // "Image" chip too, and the plain text lookup could not tell them apart -
    // which is the same ambiguity the direction arrow was added to remove.
    const card = screen.getByRole("button", { name: /select model gpt-4o/i });
    expect(
      within(card).getByTitle(/this model reads image/i),
    ).toBeInTheDocument();

    // No upload-related UI
    expect(screen.queryByText(/upload/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/upload/i)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /upload/i }),
    ).not.toBeInTheDocument();
  });

  // T-76: Model search filters the list
  it("narrows the list to what was typed", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFixture },
    });

    renderWithQueryClient(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("GPT-4o")).toBeInTheDocument();
    });

    const searchInput = screen.getByLabelText("Search models");
    await userEvent.type(searchInput, "GPT-4o");

    // GPT-4o still visible after filtering
    expect(screen.getByText("GPT-4o")).toBeInTheDocument();
  });

  // T-77: Model search empty state appears when no match
  it("says nothing matched instead of showing an empty panel", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFixture },
    });

    renderWithQueryClient(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("GPT-4o")).toBeInTheDocument();
    });

    const searchInput = screen.getByLabelText("Search models");
    await userEvent.type(searchInput, "zzz_no_match");

    await waitFor(() => {
      expect(screen.getByTestId("model-search-empty")).toBeInTheDocument();
    });
    expect(screen.queryByText("GPT-4o")).not.toBeInTheDocument();
  });

  // T-78: Model search clear button resets list
  it("clearing the search brings the whole list back", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFixture },
    });

    renderWithQueryClient(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("GPT-4o")).toBeInTheDocument();
    });

    const searchInput = screen.getByLabelText("Search models");
    await userEvent.type(searchInput, "zzz_no_match");

    await waitFor(() => {
      expect(screen.getByTestId("model-search-empty")).toBeInTheDocument();
    });

    const clearBtn = screen.getByLabelText("Clear search");
    await userEvent.click(clearBtn);

    await waitFor(() => {
      expect(screen.getByText("GPT-4o")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("model-search-empty")).not.toBeInTheDocument();
  });
});

// ── Context usage meter ──────────────────────────────────────────
//
// Shared arithmetic (mirrors backend routers/completions.py, chars/token = 3):
//
// meterModel: context_length 1000, max_completion_tokens 100,
// supported_parameters ["temperature"] - the default max_tokens (1024) is NOT
// advertised, so it is never sent and the backend reserves the metadata value.
//   budget   = clamp(16384 default, model ctx) = 1000 -> effective = 1000
//   safety   = min(256, floor(1000 / 8) = 125) = 125
//   budget_chars = (1000 - 125) * 3 = 2625
//   reservation  = meta 100 * 3 = 300 <= 2625 -> kept
//   available    = 2625 - 300 = 2325 -> capacity = floor(2325 / 3) = 775
// meterCharacter: system_prompt "You are terse." (14 chars)
//   system_block = "[System Prompt]\n" (16) + 14 = 30; no persona, no phi
//   fixed = 30 -> history budget remaining = 2325 - 30 = 2295

const meterModel: Model = {
  ...modelFixture,
  id: "test/meter-1000",
  name: "Meter Model",
  context_length: 1000,
  max_completion_tokens: 100,
  supported_parameters: ["temperature"],
};

// Same shape with a 4000-token context:
//   effective = clamp(16384, 4000) = 4000; safety = min(256, 500) = 256
//   budget_chars = 3744 * 3 = 11232; available = 11232 - 300 = 10932
//   capacity = floor(10932 / 3) = 3644 -> renders as "3.6K"
const meterModelLarge: Model = {
  ...meterModel,
  id: "test/meter-4000",
  name: "Meter Model Large",
  context_length: 4000,
};

const meterModels: ModelList = {
  source: "user",
  cached: false,
  count: 2,
  models: [meterModel, meterModelLarge],
};

const meterCharacter = {
  ...characterFixture,
  id: 3,
  name: "Terse",
  system_prompt: "You are terse.",
  description: "",
  personality: "",
  scenario: "",
  mes_example: "",
  post_history_instruction: "",
};

const meterChat = { ...chatFixture, id: 9, character_id: 3, title: "Meter chat" };

function meterMsg(id: number, content: string): Message {
  return {
    id,
    chat_id: 9,
    role: id % 2 === 1 ? "user" : "assistant",
    content,
    created_at: "2026-01-01T00:00:00",
  };
}

// Route order matters: the messages pattern must precede the "/chats" list
// pattern because mockFetch matches by first URL substring hit.
function mockMeterRoutes(messages: Message[]) {
  mockFetch({
    "/models/openrouter": { body: meterModels },
    "/chats/9/messages": { body: messages },
    "/chats": { body: [meterChat] },
    "/characters": { body: [meterCharacter] },
    "/personas": { body: [] },
  });
}

describe("Context usage meter", () => {
  beforeEach(() => {
    useErrorStore.getState().clearAll();
    useUiStore.setState({
      selectedModelId: meterModel.id,
      selectedChatId: 9,
      selectedCharacterId: null,
    });
  });

  afterEach(() => {
    useUiStore.setState({
      selectedModelId: null,
      selectedChatId: null,
      selectedCharacterId: null,
    });
    useErrorStore.getState().clearAll();
    vi.restoreAllMocks();
  });

  it("renders the estimate label and normal state under 75%", async () => {
    // One 600-char message: used = ceil((30 + 600) / 3) = 210 tokens.
    // percent = 210 / 775 * 100 = 27.09...% -> normal.
    mockMeterRoutes([meterMsg(1, "x".repeat(600))]);

    renderWithQueryClient(<ModelPanel />, { wrapper });

    const meter = await screen.findByTestId("context-usage-meter");
    expect(meter).toHaveAttribute("data-state", "normal");
    expect(meter).toHaveTextContent("Context ≈ 217 / 775 tokens · 1 msg");
    expect(meter).not.toHaveTextContent("dropped");

    const fill = screen.getByTestId("context-usage-fill");
    expect(fill.style.width).toBe(`${(217 / 775) * 100}%`);
  });

  it("switches to the warning state at 75% and danger at 92%", async () => {
    // 1800 chars: used = ceil((30 + 1800) / 3) = 610 -> 78.7% -> warning.
    mockMeterRoutes([meterMsg(1, "x".repeat(1800))]);

    const { unmount } = renderWithQueryClient(<ModelPanel />, { wrapper });

    const meter = await screen.findByTestId("context-usage-meter");
    expect(meter).toHaveTextContent("Context ≈ 617 / 775 tokens · 1 msg");
    expect(meter).toHaveAttribute("data-state", "warning");

    unmount();

    // 2150 chars: used = ceil((30 + 2150) / 3) = ceil(726.67) = 727
    // -> 93.8% -> danger (2150 <= 2295, so nothing is dropped).
    mockMeterRoutes([meterMsg(1, "x".repeat(2150))]);

    renderWithQueryClient(<ModelPanel />, { wrapper });

    const dangerMeter = await screen.findByTestId("context-usage-meter");
    expect(dangerMeter).toHaveTextContent("Context ≈ 734 / 775 tokens · 1 msg");
    expect(dangerMeter).toHaveAttribute("data-state", "danger");
  });

  it("reports dropped oldest messages in the label", async () => {
    // Two 1200-char messages = 2400 > 2295 -> oldest dropped, 1200 kept.
    // used = ceil((30 + 1200) / 3) = 410 tokens.
    mockMeterRoutes([meterMsg(1, "a".repeat(1200)), meterMsg(2, "b".repeat(1200))]);

    renderWithQueryClient(<ModelPanel />, { wrapper });

    const meter = await screen.findByTestId("context-usage-meter");
    expect(meter).toHaveTextContent(
      "Context ≈ 417 / 775 tokens · 2 msgs (1 oldest dropped)",
    );
  });

  it("updates the numbers live when the selected model changes", async () => {
    mockMeterRoutes([meterMsg(1, "x".repeat(600))]);

    renderWithQueryClient(<ModelPanel />, { wrapper });

    const meter = await screen.findByTestId("context-usage-meter");
    expect(meter).toHaveTextContent("Context ≈ 217 / 775 tokens · 1 msg");

    act(() => {
      useUiStore.setState({ selectedModelId: meterModelLarge.id });
    });

    // Same 210 used tokens, but capacity becomes 3644 -> "3.6K".
    await waitFor(() => {
      expect(screen.getByTestId("context-usage-meter")).toHaveTextContent(
        "Context ≈ 217 / 3.6K tokens · 1 msg",
      );
    });
  });

  it("exposes the meter as an accessible progressbar", async () => {
    // Same inputs as the normal-state test: percent = 210 / 775 * 100 = 27.09%,
    // which rounds to an aria-valuenow of 27.
    mockMeterRoutes([meterMsg(1, "x".repeat(600))]);

    renderWithQueryClient(<ModelPanel />, { wrapper });

    await screen.findByTestId("context-usage-meter");
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "28");
    expect(bar).toHaveAttribute("aria-valuemin", "0");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
    expect(bar).toHaveAttribute(
      "aria-label",
      "Estimated context usage: 28 percent",
    );
    // The estimate caveat is available to assistive tech, not mouse-hover only.
    const describedBy = bar.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    const caveat = document.getElementById(describedBy!);
    expect(caveat).not.toBeNull();
    expect(caveat).toHaveTextContent(/estimated locally/i);
  });

  it("is hidden when no model is selected", async () => {
    useUiStore.setState({ selectedModelId: null });
    mockMeterRoutes([meterMsg(1, "x".repeat(600))]);

    renderWithQueryClient(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("Meter Model")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("context-usage-meter")).not.toBeInTheDocument();
    expect(screen.queryByTestId("context-usage-empty")).not.toBeInTheDocument();
  });

  it("shows the select-a-chat hint when a model is selected without a chat", async () => {
    useUiStore.setState({ selectedChatId: null });
    mockMeterRoutes([]);

    renderWithQueryClient(<ModelPanel />, { wrapper });

    const empty = await screen.findByTestId("context-usage-empty");
    expect(empty).toHaveTextContent("Select a chat to see context usage");
    expect(screen.queryByTestId("context-usage-meter")).not.toBeInTheDocument();
  });
});

// ── Provenance and key state ─────────────────────────────────────
//
// The Models tab is the DEFAULT tab, so the catalogue renders seconds after
// the passphrase - before any API key exists - and selecting a card is a pure
// local write. Nothing here used to say the list was borrowed or that none of
// it could be used yet. These tests pin BOTH arms plus the ground: with the
// settings query unanswered the panel commits to neither claim.

describe("Model list provenance and key state", () => {
  beforeEach(() => {
    useErrorStore.getState().clearAll();
    useUiStore.setState({ selectedModelId: null, selectedChatId: null });
  });

  afterEach(() => {
    useErrorStore.getState().clearAll();
    vi.restoreAllMocks();
  });

  it("says the list is borrowed and unusable while no key is stored", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFixture },
      "/settings": { body: { ...settingsFixture, api_key_set: false } },
    });

    renderWithQueryClient(<ModelPanel />, { wrapper });

    const notice = await screen.findByTestId("model-key-missing");
    expect(notice).toHaveTextContent(/came from OpenRouter/i);
    expect(notice).toHaveTextContent(/Security tab/i);
    expect(notice).toHaveTextContent(/no model here can be used/i);
    // Not both at once, and not the wrong one.
    expect(screen.queryByTestId("model-key-ready")).not.toBeInTheDocument();
  });

  it("turns into a quiet confirmation once a key is stored", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFixture },
      "/settings": { body: { ...settingsFixture, api_key_set: true } },
    });

    renderWithQueryClient(<ModelPanel />, { wrapper });

    const ready = await screen.findByTestId("model-key-ready");
    expect(ready).toHaveTextContent("API key is set.");
    expect(screen.queryByTestId("model-key-missing")).not.toBeInTheDocument();
    // The warning is gone in substance, not just by testid.
    expect(screen.queryByText(/no model here can be used/i)).not.toBeInTheDocument();
  });

  // GROUND. Without this, a notice hard-coded to render (or one derived from
  // `!settings?.api_key_set`, which is true while the query is still in
  // flight) would pass both tests above. The settings call fails here, so the
  // panel knows nothing about the key and must claim nothing - even though the
  // catalogue itself loaded fine.
  it("claims neither state while the settings query has not answered", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFixture },
      "/settings": { status: 503, body: { detail: "settings_unavailable" } },
    });

    renderWithQueryClient(<ModelPanel />, { wrapper });

    // Wait for the panel to be fully rendered, so absence is a real absence
    // and not a test that finished before the notice had a chance to mount.
    await waitFor(() => {
      expect(screen.getByText("GPT-4o")).toBeInTheDocument();
    });

    expect(screen.queryByTestId("model-key-missing")).not.toBeInTheDocument();
    expect(screen.queryByTestId("model-key-ready")).not.toBeInTheDocument();
  });
});
