import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatCanvas } from "@/components/chat/ChatCanvas";
import { useUiStore } from "@/lib/store/uiStore";
import { useErrorStore } from "@/lib/errors";
import { mockFetch } from "../mocks/api";
import {
  settingsFixture,
  messageFixture,
  completionFixture,
} from "../mocks/fixtures";
import type { ReactNode } from "react";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
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

  // T-44: POST body is correct
  it("T-44: POST /chats/{id}/complete called with correct body", async () => {
    const user = userEvent.setup();
    setupReadyState();
    const mock = mockFetch({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete": { body: completionFixture },
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

  // T-45: POST body does not contain generation_params
  it("T-45: POST body has no generation_params", async () => {
    const user = userEvent.setup();
    setupReadyState();
    const mock = mockFetch({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete": { body: completionFixture },
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
      expect(body).not.toHaveProperty("generation_params");
      expect(body).not.toHaveProperty("provider");
      // Only message and model_id
      expect(Object.keys(body).sort()).toEqual(["message", "model_id"]);
    });
  });

  // T-46: Success displays returned user_message
  it("T-46: success displays returned user_message", async () => {
    const user = userEvent.setup();
    setupReadyState();
    mockFetch({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete": { body: completionFixture },
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
    mockFetch({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete": { body: completionFixture },
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
      expect(screen.getByRole("alert")).toBeInTheDocument();
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
      expect(screen.getByText(/api key/i)).toBeInTheDocument();
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
      expect(screen.getByText(/proxy.*required/i)).toBeInTheDocument();
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
      expect(screen.getByText(/context/i)).toBeInTheDocument();
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
      expect(screen.getByText(/unexpected response/i)).toBeInTheDocument();
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
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });

    // Raw upstream data should never appear
    expect(screen.queryByText("UPSTREAM_LEAK_DATA")).not.toBeInTheDocument();
  });

  // T-54: openrouter_completion_error shows safe message (not ZDR — that's a different code)
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
    expect(await screen.findByText(/provider returned an error/i)).toBeInTheDocument();

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
      await screen.findByText(/generation parameters/i),
    ).toBeInTheDocument();

    // Input preserved on error
    expect(textarea.value).toBe("My test message");

    // Raw marker not shown
    expect(
      screen.queryByText("UPSTREAM_RAW_SECRET_MARKER_DO_NOT_SHOW"),
    ).not.toBeInTheDocument();

    // No user/assistant messages appended — error alert visible instead
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  // ── FE-2: Optimistic send + thinking bubble tests ──────────────

  // T-56: user message appears immediately (optimistic)
  it("T-56: user message appears immediately before server responds", async () => {
    const user = userEvent.setup();
    setupReadyState();

    // Use a delayed completion response to catch the optimistic state
    let resolveCompletion: (v: unknown) => void;
    const completionPromise = new Promise((resolve) => {
      resolveCompletion = resolve;
    });

    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/settings")) {
        return new Response(JSON.stringify(settingsFixture), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.includes("/chats/1/messages")) {
        return new Response(JSON.stringify([messageFixture]), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.includes("/chats/1/complete")) {
        await completionPromise;
        return new Response(JSON.stringify(completionFixture), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.includes("/chats")) {
        return new Response(JSON.stringify([]), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response("{}", { status: 404 });
    }));

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

    // Resolve completion
    resolveCompletion!(undefined);

    // After success, assistant message appears
    expect(
      await screen.findByText("Hi! How can I help you?"),
    ).toBeInTheDocument();

    // Thinking bubble should be gone
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  // T-57: error removes optimistic message and pushes to error store
  it("T-57: error rolls back optimistic message and pushes to error store", async () => {
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

    // Wait for error
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });

    // Optimistic message should be rolled back (not visible as a message bubble)
    // The error store should have an entry
    expect(useErrorStore.getState().errors.length).toBeGreaterThanOrEqual(1);
    expect(useErrorStore.getState().errors[0].code).toBe("openrouter_completion_error");

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
    mockFetch({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete": { body: completionFixture },
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

    // Use delayed completion to check the cleared state during pending
    let resolveCompletion: (v: unknown) => void;
    const completionPromise = new Promise((resolve) => {
      resolveCompletion = resolve;
    });

    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/settings")) {
        return new Response(JSON.stringify(settingsFixture), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.includes("/chats/1/messages")) {
        return new Response(JSON.stringify([messageFixture]), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.includes("/chats/1/complete")) {
        await completionPromise;
        return new Response(JSON.stringify(completionFixture), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.includes("/chats")) {
        return new Response(JSON.stringify([]), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response("{}", { status: 404 });
    }));

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

    // Resolve to clean up
    resolveCompletion!(undefined);
    await screen.findByText("Hi! How can I help you?");
  });
});
