import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatCanvas } from "@/components/chat/ChatCanvas";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "../mocks/api";
import {
  mockFetchWithStreams,
  sseEventsFor,
  controlledSseResponse,
} from "../helpers/streamMocks";
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
  return (
    <QueryClientProvider client={qc}>
      <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
    </QueryClientProvider>
  );
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
    const mock = mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete/stream": { sse: sseEventsFor(completionFixture) },
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

  // T-43: Pending state prevents duplicate sends - while streaming, the send
  // button is replaced by an enabled Stop button and the input is disabled.
  it("T-43: send is unavailable during pending; Stop button takes its place", async () => {
    const user = userEvent.setup();
    setupReadyState();

    const stream = controlledSseResponse();
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
    await user.type(textarea, "Hello there");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    // While streaming: no send button, Stop button present, input disabled
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /stop generating/i }),
      ).toBeInTheDocument();
    });
    expect(
      screen.queryByRole("button", { name: /send message/i }),
    ).not.toBeInTheDocument();
    expect(textarea).toBeDisabled();

    // Finish the stream to clean up - send button returns
    for (const event of sseEventsFor(completionFixture)) {
      stream.emit(event);
    }
    stream.close();
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /send message/i }),
      ).toBeInTheDocument();
    });
  });

  // ── A11y: banners linked to the textarea ──────────────────────

  it("links the preflight helper to the textarea via aria-describedby", async () => {
    mockFetch({ "/settings": { body: settingsFixture } });
    render(<ChatCanvas />, { wrapper });

    const textarea = screen.getByLabelText("Message");
    await waitFor(() => {
      expect(
        screen.getByText(/select a character and chat/i),
      ).toBeInTheDocument();
    });

    const describedBy = textarea.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    const described = describedBy!
      .split(" ")
      .map((id) => document.getElementById(id)?.textContent ?? "")
      .join(" ");
    expect(described).toMatch(/select a character and chat/i);
  });

  it("links the error banner to the textarea via aria-describedby", async () => {
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

    await user.type(screen.getByLabelText("Message"), "Hello there");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
    });

    const describedBy = screen
      .getByLabelText("Message")
      .getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    const described = describedBy!
      .split(" ")
      .map((id) => document.getElementById(id)?.textContent ?? "")
      .join(" ");
    expect(described).toMatch(/api key/i);
  });
});
