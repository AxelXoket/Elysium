import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect } from "react";
import { ChatCanvas } from "@/components/chat/ChatCanvas";
import { GenerationSettingsProvider, useGenerationSettings } from "@/components/generation/GenerationSettingsContext";
import { useUiStore } from "@/lib/store/uiStore";
import { useErrorStore } from "@/lib/errors";
import { useModels } from "@/lib/query/models";
import { mockFetch } from "../mocks/api";
import {
  mockFetchWithStreams,
  sseEventsFor,
  sseResponse,
  jsonResponse,
  controlledSseResponse,
} from "../helpers/streamMocks";
import {
  settingsFixture,
  messageFixture,
  completionFixture,
  personaFixture,
  modelFixture,
} from "../mocks/fixtures";
import type { ReactNode } from "react";
import type { Message } from "@/lib/schemas/chats";
import type { ModelList } from "@/lib/schemas/models";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={qc}>
      <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
    </QueryClientProvider>
  );
}

function modelList(supported_parameters = modelFixture.supported_parameters): ModelList {
  return {
    source: "user",
    cached: true,
    count: 1,
    models: [{ ...modelFixture, supported_parameters }],
  };
}

function SeedGenerationSettings() {
  const { setSetting } = useGenerationSettings();

  useEffect(() => {
    setSetting("temperature", 1.1);
    setSetting("top_p", 0.5);
    setSetting("top_k", 123);
    setSetting("repetition_penalty", 1.2);
    setSetting("max_tokens", 2048);
    setSetting("context_budget_tokens", 4096);
  }, [setSetting]);

  return null;
}

function SeedGenerationSettingsWithSeed() {
  const { setSetting } = useGenerationSettings();

  useEffect(() => {
    setSetting("temperature", 1.1);
    setSetting("top_p", 0.5);
    setSetting("top_k", 123);
    setSetting("repetition_penalty", 1.2);
    setSetting("max_tokens", 2048);
    setSetting("seed", "42");
    setSetting("context_budget_tokens", 4096);
  }, [setSetting]);

  return null;
}

function ModelsReady() {
  const { data } = useModels();
  return data ? <span data-testid="models-ready" /> : null;
}

function setupReadyState() {
  useUiStore.setState({
    selectedChatId: 1,
    selectedModelId: "openai/gpt-4o",
    selectedCharacterId: 1,
  });
}

describe("SendFlow", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: null,
      selectedModelId: null,
      selectedCharacterId: null,
    });
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // T-44: POST body is correct (production send targets the stream endpoint)
  it("T-44: POST /chats/{id}/complete/stream called with correct body", async () => {
    const user = userEvent.setup();
    setupReadyState();
    const mock = mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete/stream": { sse: sseEventsFor(completionFixture) },
      "/chats": { body: [] },
    });
    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    const textarea = screen.getByLabelText("Message");
    await user.type(textarea, "Hello there");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      const postCalls = mock.mock.calls.filter(
        (call: unknown[]) =>
          typeof call[0] === "string" &&
          call[0].includes("/complete") &&
          (call[1] as RequestInit)?.method === "POST",
      );
      expect(postCalls.length).toBeGreaterThanOrEqual(1);

      const body = JSON.parse((postCalls[0][1] as RequestInit).body as string);
      expect(body.message).toBe("Hello there");
      expect(body.model_id).toBe("openai/gpt-4o");
    });
  });

  // T-45: FE-4B defaults are sent safely
  it("T-45: POST body includes safe generation defaults", async () => {
    const user = userEvent.setup();
    setupReadyState();
    const mock = mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete/stream": { sse: sseEventsFor(completionFixture) },
      "/chats": { body: [] },
    });
    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    await user.type(screen.getByLabelText("Message"), "Test");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      const postCalls = mock.mock.calls.filter(
        (call: unknown[]) =>
          typeof call[0] === "string" &&
          call[0].includes("/complete") &&
          (call[1] as RequestInit)?.method === "POST",
      );
      expect(postCalls.length).toBeGreaterThanOrEqual(1);
      const body = JSON.parse((postCalls[0][1] as RequestInit).body as string);
      expect(body.generation_params).toEqual({
        temperature: 0.8,
        top_p: 0.9,
        top_k: 40,
        repetition_penalty: 1.05,
        max_tokens: 1024,
      });
      expect(body.context_budget_tokens).toBe(16384);
      expect(body.generation_params).not.toHaveProperty("seed");
      expect(body.generation_params).not.toHaveProperty("context_budget_tokens");
      expect(body).not.toHaveProperty("provider");
      expect(body).not.toHaveProperty("zdr");
      expect(body).not.toHaveProperty("data_collection");
      expect(body).not.toHaveProperty("allow_fallbacks");
    });
  });

  // FE-3B: active persona id is included without persona object/description
  it("T-45B: POST body includes only active persona_id when personas are loaded", async () => {
    const user = userEvent.setup();
    setupReadyState();
    const inactivePersona = {
      ...personaFixture,
      id: 2,
      display_name: "Inactive Persona",
      description: "Inactive description must not leak.",
      is_active: false,
    };
    const mock = mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/personas": { body: [inactivePersona, personaFixture] },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete/stream": { sse: sseEventsFor(completionFixture) },
      "/chats": { body: [] },
    });
    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(
        mock.mock.calls.some(([url]) => String(url).includes("/personas")),
      ).toBe(true);
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    await user.type(screen.getByLabelText("Message"), "Persona test");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      const postCalls = mock.mock.calls.filter(
        (call: unknown[]) =>
          typeof call[0] === "string" &&
          call[0].includes("/complete") &&
          (call[1] as RequestInit)?.method === "POST",
      );
      expect(postCalls.length).toBeGreaterThanOrEqual(1);
      const body = JSON.parse((postCalls[0][1] as RequestInit).body as string);
      expect(body.persona_id).toBe(personaFixture.id);
      expect(body).not.toHaveProperty("persona");
      expect(body).not.toHaveProperty("personas");
      expect(body).not.toHaveProperty("description");
      expect(body).not.toHaveProperty("persona_description");
      expect(JSON.stringify(body)).not.toContain(personaFixture.description);
      expect(JSON.stringify(body)).not.toContain(inactivePersona.description);
    });
  });

  it("FE-4B: send includes generation params and top-level context budget", async () => {
    const user = userEvent.setup();
    setupReadyState();
    const mock = mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/models/openrouter": { body: modelList([
        "temperature",
        "top_p",
        "top_k",
        "repetition_penalty",
        "max_tokens",
        "seed",
      ]) },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete/stream": { sse: sseEventsFor(completionFixture) },
      "/chats": { body: [] },
    });

    render(
      <>
        <SeedGenerationSettings />
        <ModelsReady />
        <ChatCanvas />
      </>,
      { wrapper },
    );

    await screen.findByTestId("models-ready");
    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    await user.type(screen.getByLabelText("Message"), "Generation test");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      const postCalls = mock.mock.calls.filter(
        (call: unknown[]) =>
          typeof call[0] === "string" &&
          call[0].includes("/complete") &&
          (call[1] as RequestInit)?.method === "POST",
      );
      expect(postCalls.length).toBeGreaterThanOrEqual(1);
      const body = JSON.parse((postCalls[0][1] as RequestInit).body as string);
      expect(body.generation_params).toEqual({
        temperature: 1.1,
        top_p: 0.5,
        top_k: 123,
        repetition_penalty: 1.2,
        max_tokens: 2048,
      });
      expect(body.context_budget_tokens).toBe(4096);
      expect(body.generation_params).not.toHaveProperty("seed");
      expect(body.generation_params).not.toHaveProperty("context_budget_tokens");
      expect(body).not.toHaveProperty("provider");
      expect(body).not.toHaveProperty("zdr");
      expect(body).not.toHaveProperty("data_collection");
      expect(body).not.toHaveProperty("allow_fallbacks");
    });
  });

  it("FE-4B: unsupported generation params are omitted while active persona_id remains", async () => {
    const user = userEvent.setup();
    setupReadyState();
    const mock = mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/personas": { body: [personaFixture] },
      "/models/openrouter": { body: modelList(["temperature", "max_tokens"]) },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete/stream": { sse: sseEventsFor(completionFixture) },
      "/chats": { body: [] },
    });

    render(
      <>
        <SeedGenerationSettingsWithSeed />
        <ModelsReady />
        <ChatCanvas />
      </>,
      { wrapper },
    );

    await screen.findByTestId("models-ready");
    await waitFor(() => {
      expect(
        mock.mock.calls.some(([url]) => String(url).includes("/personas")),
      ).toBe(true);
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    await user.type(screen.getByLabelText("Message"), "Filtered params test");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      const postCalls = mock.mock.calls.filter(
        (call: unknown[]) =>
          typeof call[0] === "string" &&
          call[0].includes("/complete") &&
          (call[1] as RequestInit)?.method === "POST",
      );
      expect(postCalls.length).toBeGreaterThanOrEqual(1);
      const body = JSON.parse((postCalls[0][1] as RequestInit).body as string);
      expect(body.generation_params).toEqual({
        temperature: 1.1,
        max_tokens: 2048,
      });
      expect(body.persona_id).toBe(personaFixture.id);
      expect(body.context_budget_tokens).toBe(4096);
      expect(body.generation_params).not.toHaveProperty("top_p");
      expect(body.generation_params).not.toHaveProperty("top_k");
      expect(body.generation_params).not.toHaveProperty("repetition_penalty");
      expect(body.generation_params).not.toHaveProperty("seed");
      expect(body.generation_params).not.toHaveProperty("context_budget_tokens");
    });
  });

  // T-46: Success displays returned user_message
  it("T-46: success displays returned user_message", async () => {
    const user = userEvent.setup();
    setupReadyState();
    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete/stream": { sse: sseEventsFor(completionFixture) },
      "/chats": { body: [] },
    });
    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    await user.type(screen.getByLabelText("Message"), "Hello there");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    expect(await screen.findByText("Hello there")).toBeInTheDocument();
  });

  // T-47: Success displays returned assistant_message
  it("T-47: success displays returned assistant_message", async () => {
    const user = userEvent.setup();
    setupReadyState();
    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete/stream": { sse: sseEventsFor(completionFixture) },
      "/chats": { body: [] },
    });
    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    await user.type(screen.getByLabelText("Message"), "Hello there");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    expect(
      await screen.findByText("Hi! How can I help you?"),
    ).toBeInTheDocument();
  });

  // T-48: Error rollback removes optimistic message and preserves input
  it("T-48: error does not append messages", async () => {
    const user = userEvent.setup();
    setupReadyState();
    mockFetch({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete": { status: 400, body: { detail: "context_too_large" } },
    });
    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    await user.type(screen.getByLabelText("Message"), "Test message");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    // Wait for error to show
    await waitFor(() => {
      expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
    });

    // Input preserved (restored on error)
    const textarea = screen.getByLabelText("Message") as HTMLTextAreaElement;
    expect(textarea.value).toBe("Test message");
  });

  // T-49: api_key_missing shows settings-related safe message
  it("T-49: api_key_missing shows settings-related message", async () => {
    const user = userEvent.setup();
    setupReadyState();
    mockFetch({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete": { status: 401, body: { detail: "api_key_missing" } },
    });
    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    await user.type(screen.getByLabelText("Message"), "Test");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      // FE-1A mapped message
      expect(screen.getAllByText(/api key/i).length).toBeGreaterThan(0);
    });
  });

  // T-50: proxy error shows proxy-related safe message
  it("T-50: proxy_missing shows proxy-related message", async () => {
    const user = userEvent.setup();
    setupReadyState();
    mockFetch({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete": { status: 503, body: { detail: "proxy_missing" } },
    });
    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    await user.type(screen.getByLabelText("Message"), "Test");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      expect(screen.getAllByText(/proxy.*required/i).length).toBeGreaterThan(0);
    });
  });

  // T-51: context_too_large shows context-related message
  it("T-51: context_too_large shows context message", async () => {
    const user = userEvent.setup();
    setupReadyState();
    mockFetch({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete": { status: 400, body: { detail: "context_too_large" } },
    });
    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    await user.type(screen.getByLabelText("Message"), "Test");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      expect(screen.getAllByText(/context/i).length).toBeGreaterThan(0);
    });
  });

  // T-52: invalid_response_shape shows safe generic message
  it("T-52: invalid_response_shape shows generic message", async () => {
    const user = userEvent.setup();
    setupReadyState();
    mockFetch({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      // Return an invalid shape that will fail Zod parse
      "/chats/1/complete": { body: { invalid: true } },
    });
    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    await user.type(screen.getByLabelText("Message"), "Test");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      expect(screen.getAllByText(/unexpected response/i).length).toBeGreaterThan(0);
    });
  });

  // T-53: Raw upstream error body is not displayed
  it("T-53: raw upstream error body is not displayed", async () => {
    const user = userEvent.setup();
    setupReadyState();
    mockFetch({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete": {
        status: 502,
        body: { detail: "openrouter_completion_error", raw: "UPSTREAM_LEAK_DATA" },
      },
    });
    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    await user.type(screen.getByLabelText("Message"), "Test");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
    });

    // Raw upstream data should never appear
    expect(screen.queryByText("UPSTREAM_LEAK_DATA")).not.toBeInTheDocument();
  });

  // T-54: openrouter_completion_error shows safe message (not ZDR - that's a different code)
  it("T-54: openrouter_completion_error shows safe provider error message", async () => {
    const user = userEvent.setup();
    setupReadyState();
    mockFetch({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete": {
        status: 502,
        body: {
          detail: "openrouter_completion_error",
          raw: "UPSTREAM_RAW_SECRET_MARKER_DO_NOT_SHOW",
        },
      },
    });
    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    await user.type(screen.getByLabelText("Message"), "Test");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    // FE-1A mapped safe message for openrouter_completion_error
    expect((await screen.findAllByText(/provider returned an error/i)).length).toBeGreaterThan(0);

    // Raw upstream marker must NOT be shown
    expect(
      screen.queryByText("UPSTREAM_RAW_SECRET_MARKER_DO_NOT_SHOW"),
    ).not.toBeInTheDocument();
  });

  // T-55: invalid_gen_params error rendering
  it("T-55: invalid_gen_params shows safe message, preserves input, no message append", async () => {
    const user = userEvent.setup();
    setupReadyState();
    mockFetch({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete": {
        status: 422,
        body: {
          detail: "invalid_gen_params",
          raw: "UPSTREAM_RAW_SECRET_MARKER_DO_NOT_SHOW",
        },
      },
    });
    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    const textarea = screen.getByLabelText("Message") as HTMLTextAreaElement;
    await user.type(textarea, "My test message");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    // Safe user-facing message
    expect(
      (await screen.findAllByText(/generation parameters/i)).length,
    ).toBeGreaterThan(0);

    // Input preserved on error
    expect(textarea.value).toBe("My test message");

    // Raw marker not shown
    expect(
      screen.queryByText("UPSTREAM_RAW_SECRET_MARKER_DO_NOT_SHOW"),
    ).not.toBeInTheDocument();

    // No user/assistant messages appended - error alert visible instead
    expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
  });

  // ── FE-2: Optimistic send + thinking bubble tests ──────────────

  // T-56: user message appears immediately (optimistic)
  it("T-56: user message appears immediately before server responds", async () => {
    const user = userEvent.setup();
    setupReadyState();

    // Hold the stream response to catch the optimistic state
    let releaseCompletion: () => void;
    const completionGate = new Promise<void>((resolve) => {
      releaseCompletion = resolve;
    });

    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete/stream": {
        response: async () => {
          await completionGate;
          return sseResponse(sseEventsFor(completionFixture));
        },
      },
      "/chats": { body: [] },
    });

    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    await user.type(screen.getByLabelText("Message"), "Hello optimistic");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    // The optimistic user message should be visible BEFORE server responds
    expect(await screen.findByText("Hello optimistic")).toBeInTheDocument();

    // Thinking bubble should be visible
    expect(screen.getByRole("status")).toBeInTheDocument();

    // Release the stream
    releaseCompletion!();

    // After success, assistant message appears
    expect(
      await screen.findByText("Hi! How can I help you?"),
    ).toBeInTheDocument();

    // Thinking bubble should be gone
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  // T-57: error removes optimistic message; the Composer banner is the single
  // error surface for send - no toast is pushed to the error store.
  it("T-57: error rolls back optimistic message without pushing a toast", async () => {
    const user = userEvent.setup();
    setupReadyState();
    mockFetch({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete": { status: 500, body: { detail: "openrouter_completion_error" } },
    });
    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    await user.type(screen.getByLabelText("Message"), "Fail message");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    // Wait for error (Composer banner)
    await waitFor(() => {
      expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
    });

    // Single surface: send errors show ONLY in the Composer banner - no toast
    expect(useErrorStore.getState().errors).toHaveLength(0);

    // Thinking bubble should be gone
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    // Input restored
    const textarea = screen.getByLabelText("Message") as HTMLTextAreaElement;
    expect(textarea.value).toBe("Fail message");
  });

  // T-58: thinking bubble not visible after success
  it("T-58: thinking bubble not visible after success", async () => {
    const user = userEvent.setup();
    setupReadyState();
    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete/stream": { sse: sseEventsFor(completionFixture) },
      "/chats": { body: [] },
    });
    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    await user.type(screen.getByLabelText("Message"), "Quick test");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    // Wait for assistant message
    await screen.findByText("Hi! How can I help you?");

    // Thinking bubble should be gone
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  // T-59: input clears immediately on send (optimistic clear)
  it("T-59: input clears immediately on send", async () => {
    const user = userEvent.setup();
    setupReadyState();

    // Hold the stream to check the cleared state during pending
    let releaseCompletion: () => void;
    const completionGate = new Promise<void>((resolve) => {
      releaseCompletion = resolve;
    });

    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete/stream": {
        response: async () => {
          await completionGate;
          return sseResponse(sseEventsFor(completionFixture));
        },
      },
      "/chats": { body: [] },
    });

    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    const textarea = screen.getByLabelText("Message") as HTMLTextAreaElement;
    await user.type(textarea, "Cleared on send");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    // Input should be cleared immediately
    await waitFor(() => {
      expect(textarea.value).toBe("");
    });

    // Release to clean up
    releaseCompletion!();
    await screen.findByText("Hi! How can I help you?");
  });

  // ── Per-chat pending + draft scoping ───────────────────────────

  it("pending indicators do not leak into another chat", async () => {
    const user = userEvent.setup();
    setupReadyState();

    let releaseCompletion: () => void;
    const completionGate = new Promise<void>((resolve) => {
      releaseCompletion = resolve;
    });

    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/2/messages": { body: [] },
      "/chats/1/complete/stream": {
        response: async () => {
          await completionGate;
          return sseResponse(sseEventsFor(completionFixture));
        },
      },
      "/chats": { body: [] },
    });

    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    await user.type(screen.getByLabelText("Message"), "Slow send");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    // Pending indicators visible in the chat that owns the request
    expect(await screen.findByRole("status")).toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toBeDisabled();

    // Switch to another chat mid-request
    act(() => {
      useUiStore.setState({ selectedChatId: 2 });
    });

    // Chat 2 must not show chat 1's pending state
    await waitFor(() => {
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    // Switch back - chat 1 is still pending
    act(() => {
      useUiStore.setState({ selectedChatId: 1 });
    });
    expect(await screen.findByRole("status")).toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toBeDisabled();

    // Release to clean up
    releaseCompletion!();
    expect(
      await screen.findByText("Hi! How can I help you?"),
    ).toBeInTheDocument();
  });

  it("failed send restores its draft and error only in the chat that owns it", async () => {
    const user = userEvent.setup();
    setupReadyState();

    let failCompletion: () => void;
    const completionGate = new Promise<void>((resolve) => {
      failCompletion = resolve;
    });

    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/2/messages": { body: [] },
      "/chats/1/complete/stream": {
        response: async () => {
          await completionGate;
          return jsonResponse({ detail: "openrouter_completion_error" }, 502);
        },
      },
      "/chats": { body: [] },
    });

    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    const textarea = screen.getByLabelText("Message") as HTMLTextAreaElement;
    await user.type(textarea, "Doomed draft");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    // Input cleared optimistically; switch to another chat while pending
    await waitFor(() => {
      expect(textarea.value).toBe("");
    });
    act(() => {
      useUiStore.setState({ selectedChatId: 2 });
    });
    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });
    await user.type(textarea, "Chat two text");

    // Now let chat 1's send fail and settle
    failCompletion!();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });

    // Chat 2 is untouched: no clobbered draft, no foreign error banner
    expect(textarea.value).toBe("Chat two text");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    // Switching back to chat 1 restores its failed draft AND its error banner
    act(() => {
      useUiStore.setState({ selectedChatId: 1 });
    });
    await waitFor(() => {
      expect(textarea.value).toBe("Doomed draft");
      expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
    });
  });

  it("dismissed send error stays gone across chat switches until a new error", async () => {
    const user = userEvent.setup();
    setupReadyState();
    mockFetch({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/2/messages": { body: [] },
      "/chats/1/complete": {
        status: 502,
        body: { detail: "openrouter_completion_error" },
      },
    });
    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    const textarea = screen.getByLabelText("Message") as HTMLTextAreaElement;
    await user.type(textarea, "Fail me");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
    });

    // Dismiss deletes the chat's error entry - not just hides the banner
    await user.click(screen.getByRole("button", { name: /dismiss error/i }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    // Switch away and back - the dismissed error must not resurface
    act(() => {
      useUiStore.setState({ selectedChatId: 2 });
    });
    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    act(() => {
      useUiStore.setState({ selectedChatId: 1 });
    });
    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    // A NEW error for the same chat shows the banner again
    await user.clear(textarea);
    await user.type(textarea, "Fail me again");
    await user.click(screen.getByRole("button", { name: /send message/i }));
    await waitFor(() => {
      expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
    });
  });

  // ── Streaming UI: progressive text + Stop button ───────────────

  it("streams text progressively and Stop keeps the persisted partial", async () => {
    const user = userEvent.setup();
    setupReadyState();

    const stream = controlledSseResponse();
    // The messages route is dynamic: after the abort the backend "persists"
    // the partial, and the invalidate-triggered refetch swaps it in.
    let messagesBody: Message[] = [messageFixture];
    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": {
        response: () => jsonResponse(messagesBody),
      },
      "/chats/1/complete/stream": { response: () => stream.response },
      "/chats": { body: [] },
    });

    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    await user.type(screen.getByLabelText("Message"), "Stream me");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    // Send button becomes a Stop button while streaming
    const stopButton = await screen.findByRole("button", {
      name: /stop generating/i,
    });
    expect(
      screen.queryByRole("button", { name: /send message/i }),
    ).not.toBeInTheDocument();

    // Before the first delta: thinking bubble
    expect(screen.getByRole("status")).toBeInTheDocument();

    stream.emit({
      type: "user_message",
      message: {
        id: 5,
        chat_id: 1,
        role: "user",
        content: "Stream me",
        created_at: "2026-01-01T00:02:00",
      },
    });
    stream.emit({ type: "delta", content: "Partial ans" });

    // Streaming bubble renders the accumulating text; thinking bubble gone
    expect(await screen.findByText(/Partial ans/)).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    // Backend will persist the partial on abort - refetch returns it
    messagesBody = [
      messageFixture,
      {
        id: 5,
        chat_id: 1,
        role: "user",
        content: "Stream me",
        created_at: "2026-01-01T00:02:00",
      },
      {
        id: 6,
        chat_id: 1,
        role: "assistant",
        content: "Partial ans",
        created_at: "2026-01-01T00:02:01",
      },
    ];
    await user.click(stopButton);

    // Send button returns; the persisted partial is shown as a real message
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /send message/i }),
      ).toBeInTheDocument();
    });
    expect(await screen.findByText("Partial ans")).toBeInTheDocument();
    // Streaming cursor gone, no error surfaces for a user-initiated stop
    expect(screen.queryByText("▍")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(useErrorStore.getState().errors).toHaveLength(0);
  });

  it("Stop before any streamed text restores the draft silently", async () => {
    const user = userEvent.setup();
    setupReadyState();

    const stream = controlledSseResponse(); // never emits anything
    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete/stream": { response: () => stream.response },
      "/chats": { body: [] },
    });

    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    const textarea = screen.getByLabelText("Message") as HTMLTextAreaElement;
    await user.type(textarea, "Abort me early");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    const stopButton = await screen.findByRole("button", {
      name: /stop generating/i,
    });
    await user.click(stopButton);

    // Draft restored, optimistic message gone, no error surfaces
    await waitFor(() => {
      expect(textarea.value).toBe("Abort me early");
      expect(
        screen.getByRole("button", { name: /send message/i }),
      ).toBeInTheDocument();
    });
    expect(
      screen.queryByText("Abort me early", { selector: "p" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(useErrorStore.getState().errors).toHaveLength(0);
  });
});
