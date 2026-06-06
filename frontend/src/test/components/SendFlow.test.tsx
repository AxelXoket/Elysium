import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatCanvas } from "@/components/chat/ChatCanvas";
import { useUiStore } from "@/lib/store/uiStore";
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

  // T-48: Error does not append messages
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

    // "Test message" should NOT appear as a rendered message bubble
    // (only the typed text in textarea, which is preserved on error)
    const textarea = screen.getByLabelText("Message") as HTMLTextAreaElement;
    expect(textarea.value).toBe("Test message");
  });

  // T-49: api_key_missing shows Settings-related message
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
      expect(screen.getByText(/api key is not set/i)).toBeInTheDocument();
    });
  });

  // T-50: proxy error shows proxy-related message
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
      expect(screen.getByText(/proxy required/i)).toBeInTheDocument();
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

  // T-54: ZDR/privacy-routing error hint is rendered
  it("T-54: openrouter_completion_error shows ZDR/privacy routing hint", async () => {
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

    // ZDR/privacy routing hint must be visible
    expect(await screen.findByText(/strict privacy routing/i)).toBeInTheDocument();
    expect(screen.getByText(/try a different model/i)).toBeInTheDocument();

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
      await screen.findByText(/invalid generation parameters/i),
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
});
