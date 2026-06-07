import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
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

/** Set up valid state: chat + model selected, settings OK */
function setupReadyState() {
  useUiStore.setState({
    selectedChatId: 1,
    selectedModelId: "openai/gpt-4o",
    selectedCharacterId: 1,
  });
}

describe("Composer", () => {
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

  // T-34: Composer disabled when no chat selected
  it("T-34: disabled when no chat selected", () => {
    mockFetch({ "/settings": { body: settingsFixture } });
    render(<ChatCanvas />, { wrapper });
    const textarea = screen.getByLabelText("Message");
    expect(textarea).toBeDisabled();
  });

  // T-35: Composer disabled when no model selected
  it("T-35: disabled when no model selected", async () => {
    useUiStore.setState({ selectedChatId: 1, selectedModelId: null });
    mockFetch({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
    });
    render(<ChatCanvas />, { wrapper });

    // Wait for settings to load so the model-missing helper shows
    await waitFor(() => {
      expect(screen.getByText(/select a model/i)).toBeInTheDocument();
    });

    const textarea = screen.getByLabelText("Message");
    expect(textarea).toBeDisabled();
  });

  // T-36: Composer disabled when api_key_set=false
  it("T-36: disabled when api_key_set=false", async () => {
    setupReadyState();
    mockFetch({
      "/settings": { body: { ...settingsFixture, api_key_set: false } },
      "/chats/1/messages": { body: [messageFixture] },
    });
    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText(/api key is not set/i)).toBeInTheDocument();
    });

    const textarea = screen.getByLabelText("Message");
    expect(textarea).toBeDisabled();
  });

  // T-37: Composer disabled when proxy_required but not configured
  it("T-37: disabled when proxy_required + not configured", async () => {
    setupReadyState();
    mockFetch({
      "/settings": {
        body: { ...settingsFixture, proxy_required: true, proxy_configured: false },
      },
      "/chats/1/messages": { body: [messageFixture] },
    });
    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText(/proxy is required/i)).toBeInTheDocument();
    });

    const textarea = screen.getByLabelText("Message");
    expect(textarea).toBeDisabled();
  });

  // T-38: Empty/whitespace text cannot be sent
  it("T-38: send button disabled when text is empty", async () => {
    setupReadyState();
    mockFetch({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
    });
    render(<ChatCanvas />, { wrapper });

    // Wait for settings to load
    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    const sendBtn = screen.getByRole("button", { name: /send message/i });
    expect(sendBtn).toBeDisabled();
  });

  // T-39: Enter key sends when enabled
  it("T-39: Enter key sends when enabled", async () => {
    const user = userEvent.setup();
    setupReadyState();
    const mock = mockFetch({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete": { body: completionFixture },
      "/chats": { body: [] },
    });
    render(<ChatCanvas />, { wrapper });

    // Wait for textarea to be enabled
    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    const textarea = screen.getByLabelText("Message");
    await user.type(textarea, "Hello there");
    // Simulate Enter to send
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" });

    await waitFor(() => {
      const postCalls = mock.mock.calls.filter(
        (call: unknown[]) =>
          typeof call[0] === "string" &&
          call[0].includes("/complete") &&
          (call[1] as RequestInit)?.method === "POST",
      );
      expect(postCalls.length).toBeGreaterThanOrEqual(1);
    });
  });

  // T-40: Shift+Enter inserts newline, does not send
  it("T-40: Shift+Enter does not send", async () => {
    const user = userEvent.setup();
    setupReadyState();
    const mock = mockFetch({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
    });
    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    const textarea = screen.getByLabelText("Message");
    await user.type(textarea, "Hello");
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter", shiftKey: true });

    // Wait a tick to confirm no send
    await new Promise((r) => setTimeout(r, 100));

    const postCalls = mock.mock.calls.filter(
      (call: unknown[]) =>
        typeof call[0] === "string" && call[0].includes("/complete"),
    );
    expect(postCalls).toHaveLength(0);
  });

  // T-41: Input clears on success
  it("T-41: input clears after successful send", async () => {
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

    const textarea = screen.getByLabelText("Message") as HTMLTextAreaElement;
    await user.type(textarea, "Hello there");
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" });

    await waitFor(() => {
      expect(textarea.value).toBe("");
    });
  });

  // T-42: Input preserved on error
  it("T-42: input preserved on error", async () => {
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

    const textarea = screen.getByLabelText("Message") as HTMLTextAreaElement;
    await user.type(textarea, "Hello there");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      expect(screen.getByText(/api key/i)).toBeInTheDocument();
    });

    // Input should NOT be cleared on error
    expect(textarea.value).toBe("Hello there");
  });

  // T-43: Pending state disables duplicate sends
  it("T-43: button disabled during pending", async () => {
    const user = userEvent.setup();
    setupReadyState();

    // Create a slow response to keep pending state
    let resolveComplete: ((v: Response) => void) | null = null;
    const mock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/complete")) {
        return new Promise<Response>((resolve) => {
          resolveComplete = resolve;
        });
      }
      if (url.includes("/settings")) {
        return new Response(JSON.stringify(settingsFixture), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/messages")) {
        return new Response(JSON.stringify([messageFixture]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ detail: "not found" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", mock);

    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    const textarea = screen.getByLabelText("Message") as HTMLTextAreaElement;
    await user.type(textarea, "Hello there");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    // While pending, button should be disabled
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /send message/i }),
      ).toBeDisabled();
    });

    // Resolve to clean up
    resolveComplete?.(
      new Response(JSON.stringify(completionFixture), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
});
