import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { mockFetch } from "@/test/mocks/api";
import {
  characterFixture,
  chatFixture,
  modelFixture,
  modelListFixture,
  modelListFallbackFixture,
} from "@/test/mocks/fixtures";
import { ModelPanel } from "@/components/models/ModelPanel";
import { TooltipProvider } from "@/components/ui/tooltip";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useErrorStore } from "@/lib/errors";
import { useUiStore } from "@/lib/store/uiStore";
import type { Model, ModelList } from "@/lib/schemas/models";
import type { Message } from "@/lib/schemas/chats";

function wrapper({ children }: { children: React.ReactNode }) {
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

describe("Model Panel Tests", () => {
  beforeEach(() => {
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    useErrorStore.getState().clearAll();
    vi.restoreAllMocks();
  });

  // T-14: Models panel renders model list
  it("T-14: renders model list", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFixture },
    });

    render(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("GPT-4o")).toBeInTheDocument();
    });
  });

  // T-15: Models panel shows source badge
  it("T-15: shows source badge", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFixture },
    });

    render(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("model-source-badge")).toHaveTextContent("user");
    });
  });

  // T-16: Models panel shows a mapped fallback message - never the raw
  // backend fallback_reason value (internal diagnostics).
  it("T-16: shows mapped fallback message, not raw fallback_reason", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFallbackFixture },
    });

    render(<ModelPanel />, { wrapper });

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
  it("FIX-3: maps timeout fallback_reason", async () => {
    mockFetch({
      "/models/openrouter": {
        body: { ...modelListFallbackFixture, fallback_reason: "timeout" },
      },
    });

    render(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("fallback-reason")).toHaveTextContent(
        "primary source timed out",
      );
    });
  });

  it("FIX-3: maps http_NNN fallback_reason", async () => {
    mockFetch({
      "/models/openrouter": {
        body: { ...modelListFallbackFixture, fallback_reason: "http_502" },
      },
    });

    render(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByTestId("fallback-reason")).toHaveTextContent(
        "primary source error (HTTP 502)",
      );
    });
    expect(screen.queryByText(/http_502/)).not.toBeInTheDocument();
  });

  // FIX-4: refresh failure surfaces exactly one toast via the error store
  it("FIX-4: failed refresh pushes an error toast", async () => {
    const fetchMock = mockFetch({
      "/models/openrouter": { body: modelListFixture },
    });

    render(<ModelPanel />, { wrapper });

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
  it("T-17: modality badges informational, no upload UI", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFixture },
    });

    render(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("GPT-4o")).toBeInTheDocument();
    });

    // Image modality badge exists (informational)
    expect(screen.getByText("Image")).toBeInTheDocument();

    // No upload-related UI
    expect(screen.queryByText(/upload/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/upload/i)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /upload/i }),
    ).not.toBeInTheDocument();
  });

  // T-76: Model search filters the list
  it("T-76: model search filters the list", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFixture },
    });

    render(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("GPT-4o")).toBeInTheDocument();
    });

    const searchInput = screen.getByLabelText("Search models");
    await userEvent.type(searchInput, "GPT-4o");

    // GPT-4o still visible after filtering
    expect(screen.getByText("GPT-4o")).toBeInTheDocument();
  });

  // T-77: Model search empty state appears when no match
  it("T-77: model search empty state appears when no match", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFixture },
    });

    render(<ModelPanel />, { wrapper });

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
  it("T-78: clear button resets model search", async () => {
    mockFetch({
      "/models/openrouter": { body: modelListFixture },
    });

    render(<ModelPanel />, { wrapper });

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

    render(<ModelPanel />, { wrapper });

    const meter = await screen.findByTestId("context-usage-meter");
    expect(meter).toHaveAttribute("data-state", "normal");
    expect(meter).toHaveTextContent("Context ≈ 210 / 775 tokens · 1 msg");
    expect(meter).not.toHaveTextContent("dropped");

    const fill = screen.getByTestId("context-usage-fill");
    expect(fill.style.width).toBe(`${(210 / 775) * 100}%`);
  });

  it("switches to the warning state at 75% and danger at 92%", async () => {
    // 1800 chars: used = ceil((30 + 1800) / 3) = 610 -> 78.7% -> warning.
    mockMeterRoutes([meterMsg(1, "x".repeat(1800))]);

    const { unmount } = render(<ModelPanel />, { wrapper });

    const meter = await screen.findByTestId("context-usage-meter");
    expect(meter).toHaveTextContent("Context ≈ 610 / 775 tokens · 1 msg");
    expect(meter).toHaveAttribute("data-state", "warning");

    unmount();

    // 2150 chars: used = ceil((30 + 2150) / 3) = ceil(726.67) = 727
    // -> 93.8% -> danger (2150 <= 2295, so nothing is dropped).
    mockMeterRoutes([meterMsg(1, "x".repeat(2150))]);

    render(<ModelPanel />, { wrapper });

    const dangerMeter = await screen.findByTestId("context-usage-meter");
    expect(dangerMeter).toHaveTextContent("Context ≈ 727 / 775 tokens · 1 msg");
    expect(dangerMeter).toHaveAttribute("data-state", "danger");
  });

  it("reports dropped oldest messages in the label", async () => {
    // Two 1200-char messages = 2400 > 2295 -> oldest dropped, 1200 kept.
    // used = ceil((30 + 1200) / 3) = 410 tokens.
    mockMeterRoutes([meterMsg(1, "a".repeat(1200)), meterMsg(2, "b".repeat(1200))]);

    render(<ModelPanel />, { wrapper });

    const meter = await screen.findByTestId("context-usage-meter");
    expect(meter).toHaveTextContent(
      "Context ≈ 410 / 775 tokens · 2 msgs (1 oldest dropped)",
    );
  });

  it("updates the numbers live when the selected model changes", async () => {
    mockMeterRoutes([meterMsg(1, "x".repeat(600))]);

    render(<ModelPanel />, { wrapper });

    const meter = await screen.findByTestId("context-usage-meter");
    expect(meter).toHaveTextContent("Context ≈ 210 / 775 tokens · 1 msg");

    act(() => {
      useUiStore.setState({ selectedModelId: meterModelLarge.id });
    });

    // Same 210 used tokens, but capacity becomes 3644 -> "3.6K".
    await waitFor(() => {
      expect(screen.getByTestId("context-usage-meter")).toHaveTextContent(
        "Context ≈ 210 / 3.6K tokens · 1 msg",
      );
    });
  });

  it("exposes the meter as an accessible progressbar", async () => {
    // Same inputs as the normal-state test: percent = 210 / 775 * 100 = 27.09%,
    // which rounds to an aria-valuenow of 27.
    mockMeterRoutes([meterMsg(1, "x".repeat(600))]);

    render(<ModelPanel />, { wrapper });

    await screen.findByTestId("context-usage-meter");
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "27");
    expect(bar).toHaveAttribute("aria-valuemin", "0");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
    expect(bar).toHaveAttribute(
      "aria-label",
      "Estimated context usage: 27 percent",
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

    render(<ModelPanel />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("Meter Model")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("context-usage-meter")).not.toBeInTheDocument();
    expect(screen.queryByTestId("context-usage-empty")).not.toBeInTheDocument();
  });

  it("shows the select-a-chat hint when a model is selected without a chat", async () => {
    useUiStore.setState({ selectedChatId: null });
    mockMeterRoutes([]);

    render(<ModelPanel />, { wrapper });

    const empty = await screen.findByTestId("context-usage-empty");
    expect(empty).toHaveTextContent("Select a chat to see context usage");
    expect(screen.queryByTestId("context-usage-meter")).not.toBeInTheDocument();
  });
});
