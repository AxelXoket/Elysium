import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { ChatCanvas } from "@/components/chat/ChatCanvas";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "../mocks/api";
import { getErrorMessage } from "@/lib/errors/errorMessages";
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
  return (
    
      <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
    
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
    renderWithQueryClient(<ChatCanvas />, { wrapper });
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
    renderWithQueryClient(<ChatCanvas />, { wrapper });

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
    renderWithQueryClient(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText(/no api key is set yet/i)).toBeInTheDocument();
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
    renderWithQueryClient(<ChatCanvas />, { wrapper });

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
    renderWithQueryClient(<ChatCanvas />, { wrapper });

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
    renderWithQueryClient(<ChatCanvas />, { wrapper });

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
    renderWithQueryClient(<ChatCanvas />, { wrapper });

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
    renderWithQueryClient(<ChatCanvas />, { wrapper });

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
    renderWithQueryClient(<ChatCanvas />, { wrapper });

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

    renderWithQueryClient(<ChatCanvas />, { wrapper });

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
    renderWithQueryClient(<ChatCanvas />, { wrapper });

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
    renderWithQueryClient(<ChatCanvas />, { wrapper });

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

  // ── v1.1 FF3/H3: focus recovery + Escape-stop ────────────────────────

  it("FF3: focus returns to the textarea when pending ends", async () => {
    const user = userEvent.setup();
    setupReadyState();
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete/stream": { response: () => stream.response },
      "/chats": { body: [] },
    });
    renderWithQueryClient(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    await user.type(screen.getByLabelText("Message"), "Focus test");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    // Pending: the textarea is disabled - focus fell to body.
    await waitFor(() => {
      expect(screen.getByLabelText("Message")).toBeDisabled();
    });

    stream.emit({
      type: "user_message",
      message: completionFixture.user_message,
    });
    stream.emit({ type: "delta", content: "Hi" });
    stream.emit({ type: "done", ...completionFixture });
    stream.close();

    // Pending ends → focus recovered without a mouse trip (FF3).
    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
      expect(screen.getByLabelText("Message")).toHaveFocus();
    });
  });

  it("H3: Escape stops the stream (window-level - textarea is disabled)", async () => {
    const user = userEvent.setup();
    setupReadyState();
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete/stream": { response: () => stream.response },
      "/chats": { body: [] },
    });
    renderWithQueryClient(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    await user.type(screen.getByLabelText("Message"), "Escape test");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    // Streaming state: the Stop button is on screen.
    stream.emit({
      type: "user_message",
      message: completionFixture.user_message,
    });
    await screen.findByRole("button", { name: /stop generating/i });

    // Escape from ANYWHERE (focus is on body - the textarea is disabled).
    await user.keyboard("{Escape}");

    // The stream aborts: Stop disappears, composer returns to sendable state.
    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: /stop generating/i }),
      ).not.toBeInTheDocument();
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });
  });

  // ── v1.1 E1: send/stop button restyle (class, not inline paint) ──────

  it("E1: send button carries composer-send-button and no inline paint", async () => {
    setupReadyState();
    mockFetch({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats": { body: [] },
    });
    renderWithQueryClient(<ChatCanvas />, { wrapper });
    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    const send = screen.getByRole("button", { name: /send message/i });
    expect(send.className).toContain("composer-send-button");
    expect(send.className).not.toContain("btn-sage-glow");
    // The paint now lives in the stylesheet, not an inline style.
    expect(send.style.backgroundColor).toBe("");
    expect(send.style.color).toBe("");
  });

  it("E1: stop button carries the same class mid-stream", async () => {
    setupReadyState();
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/1/complete/stream": { response: () => stream.response },
      "/chats": { body: [] },
    });
    const user = userEvent.setup();
    renderWithQueryClient(<ChatCanvas />, { wrapper });
    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    await user.type(screen.getByLabelText("Message"), "hi");
    await user.click(screen.getByRole("button", { name: /send message/i }));
    stream.emit({ type: "user_message", message: completionFixture.user_message });

    const stop = await screen.findByRole("button", { name: /stop generating/i });
    expect(stop.className).toContain("composer-send-button");
    expect(stop.className).not.toContain("btn-sage-glow");
    expect(stop.style.backgroundColor).toBe("");

    stream.emit({ type: "delta", content: "x" });
    stream.emit({ type: "done", ...completionFixture });
    stream.close();
  });

  // ── v1.1 E3: composer font tracks the reader setting ─────────────────

  it("E3: textarea consumes the reader font var, not a fixed text-sm", async () => {
    setupReadyState();
    mockFetch({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats": { body: [] },
    });
    renderWithQueryClient(<ChatCanvas />, { wrapper });
    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    const ta = screen.getByLabelText("Message") as HTMLTextAreaElement;
    expect(ta.className).not.toContain("text-sm");
    expect(ta.style.fontSize).toBe("var(--msg-fs, 0.875rem)");
    // jsdom never resolves min()/calc() - assert the literal formula pieces.
    expect(ta.style.maxHeight).toContain("min(11rem");
    expect(ta.style.maxHeight).toContain("0.75rem");
  });
});


/**
 * The preflight helper line, read as copy rather than as state.
 *
 * All four sentences below were audited as factually wrong: three named a
 * "Secrets" tab that no control is labelled with (RightPanel prints
 * "Security"; only the STORED tab value is still "secrets"), and the fourth
 * asked the reader whether the backend was running, in an app whose backend
 * they have never started by hand. VaultGate had already been through this
 * exact argument and fixed it by routing through the shared error map.
 *
 * These assert the wording BEHAVIOURALLY - through a rendered composer, from
 * the state that produces each line - so a future edit that puts "Secrets"
 * back, or that re-hardcodes a guess about the backend, fails here rather
 * than being caught by a reader.
 */
describe("Composer preflight copy", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: null,
      selectedModelId: null,
      selectedCharacterId: null,
      activeRightPanelTab: "models",
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("names the Security tab for a missing API key, never a Secrets tab", async () => {
    setupReadyState();
    mockFetch({
      "/settings": { body: { ...settingsFixture, api_key_set: false } },
      "/chats/1/messages": { body: [messageFixture] },
    });
    renderWithQueryClient(<ChatCanvas />, { wrapper });

    const line = await screen.findByText(/no api key is set yet/i);
    expect(line).toHaveTextContent(/Security tab/);
    // The bug this replaces: a tab name that is printed nowhere on screen.
    expect(line).not.toHaveTextContent(/Secrets/i);
  });

  it("names the Security tab for a missing proxy, never a Secrets tab", async () => {
    setupReadyState();
    mockFetch({
      "/settings": {
        body: { ...settingsFixture, proxy_required: true, proxy_configured: false },
      },
      "/chats/1/messages": { body: [messageFixture] },
    });
    renderWithQueryClient(<ChatCanvas />, { wrapper });

    const line = await screen.findByText(/a proxy is required/i);
    expect(line).toHaveTextContent(/Security tab/);
    expect(line).not.toHaveTextContent(/Secrets/i);
  });

  it("the helper's shortcut is labelled Go to Security and still opens the secrets-valued tab", async () => {
    setupReadyState();
    mockFetch({
      "/settings": { body: { ...settingsFixture, api_key_set: false } },
      "/chats/1/messages": { body: [messageFixture] },
    });
    renderWithQueryClient(<ChatCanvas />, { wrapper });

    const cta = await screen.findByRole("button", { name: "Go to Security" });
    expect(screen.queryByRole("button", { name: "Go to Secrets" })).toBeNull();

    await userEvent.click(cta);
    // The LABEL changed; the stored value did not. Renaming the value would
    // cost a persist version bump for nothing, so this pins the split.
    expect(useUiStore.getState().activeRightPanelTab).toBe("secrets");
  });

  it("a broken settings load reports the real cause instead of asking about the backend", async () => {
    setupReadyState();
    mockFetch({
      "/settings": { status: 500, body: { detail: "internal_error" } },
      "/chats/1/messages": { body: [messageFixture] },
    });
    renderWithQueryClient(<ChatCanvas />, { wrapper });

    const line = await screen.findByText(/settings could not be loaded/i);
    // Routed through the shared map, exactly as VaultGate does - so the code
    // that arrived is the sentence that shows, and a new code needs no edit
    // here to be reported properly.
    expect(line).toHaveTextContent(getErrorMessage("internal_error"));
    expect(line).not.toHaveTextContent(/backend running/i);
    // The line still has to say what it means for the composer.
    expect(screen.getByLabelText("Message")).toBeDisabled();
  });
});
