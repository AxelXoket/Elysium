import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect } from "react";
import { MessageList } from "@/components/chat/MessageList";
import { ChatCanvas } from "@/components/chat/ChatCanvas";
import {
  GenerationSettingsProvider,
  useGenerationSettings,
} from "@/components/generation/GenerationSettingsContext";
import { useModels } from "@/lib/query/models";
import { useUiStore } from "@/lib/store/uiStore";
import { mockFetch } from "../mocks/api";
import {
  mockFetchWithStreams,
  controlledSseResponse,
} from "../helpers/streamMocks";
import {
  settingsFixture,
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

function modelList(supported_parameters: string[]): ModelList {
  return {
    source: "user",
    cached: true,
    count: 1,
    models: [{ ...modelFixture, supported_parameters }],
  };
}

/** Seeds out-of-range values to prove buildRegeneratePayload clamps them. */
function SeedGenerationSettings() {
  const { setSetting } = useGenerationSettings();

  useEffect(() => {
    setSetting("temperature", 1.1);
    setSetting("top_p", 0.5);
    setSetting("max_tokens", 999999);
    setSetting("context_budget_tokens", 999999);
  }, [setSetting]);

  return null;
}

function ModelsReady() {
  const { data } = useModels();
  return data ? <span data-testid="models-ready" /> : null;
}

function message(
  id: number,
  role: "user" | "assistant",
  content: string,
): Message {
  return {
    id,
    chat_id: 1,
    role,
    content,
    created_at: `2026-01-01T00:0${id}:00Z`,
  };
}

function getBubbleByText(text: string): HTMLElement {
  const bubble = screen.getByText(text).closest(".message-bubble-shell");
  expect(bubble).not.toBeNull();
  return bubble as HTMLElement;
}

describe("ChatMessageControls", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: 1,
      selectedCharacterId: 1,
      selectedModelId: "openai/gpt-4o",
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("FE-5B: delete stays in the action container; regenerate is the side chevron", async () => {
    mockFetch({
      "/chats/1/messages": {
        body: [
          message(1, "user", "User question"),
          message(2, "assistant", "Latest answer"),
        ],
      },
    });

    render(<MessageList chatId={1} />, { wrapper });

    await screen.findByText("Latest answer");
    const bubble = getBubbleByText("Latest answer");
    expect(bubble.querySelector(".message-actions")).toBeInTheDocument();
    expect(
      within(bubble).getByRole("button", { name: "Delete message" }),
    ).toBeInTheDocument();
    // The regenerate affordance is now the right chevron at the bubble edge.
    expect(
      screen.getByRole("button", { name: "Generate a new reply" }),
    ).toBeInTheDocument();
  });

  it("FE-5B: chevron only next to the latest assistant; user shows Delete only", async () => {
    mockFetch({
      "/chats/1/messages": {
        body: [
          message(1, "user", "Original question"),
          message(2, "assistant", "Latest answer"),
        ],
      },
    });

    render(<MessageList chatId={1} />, { wrapper });

    await screen.findByText("Latest answer");
    const userBubble = getBubbleByText("Original question");
    const assistantBubble = getBubbleByText("Latest answer");

    expect(
      within(userBubble).getByRole("button", { name: "Delete message" }),
    ).toBeInTheDocument();
    expect(
      within(assistantBubble).getByRole("button", { name: "Delete message" }),
    ).toBeInTheDocument();
    // Exactly one generate chevron in the list, sitting beside the last
    // assistant bubble (a sibling of the shell, not inside it).
    expect(
      screen.getAllByRole("button", { name: "Generate a new reply" }),
    ).toHaveLength(1);
    expect(
      within(userBubble.parentElement as HTMLElement).queryByRole("button", {
        name: "Generate a new reply",
      }),
    ).not.toBeInTheDocument();
  });

  it("FE-5B: non-latest assistant message has no generate chevron", async () => {
    mockFetch({
      "/chats/1/messages": {
        body: [
          message(1, "assistant", "Older assistant"),
          message(2, "user", "Follow-up"),
          message(3, "assistant", "Newest assistant"),
        ],
      },
    });

    render(<MessageList chatId={1} />, { wrapper });

    await screen.findByText("Newest assistant");
    const olderBubble = getBubbleByText("Older assistant");

    expect(
      screen.getAllByRole("button", { name: "Generate a new reply" }),
    ).toHaveLength(1);
    expect(
      within(olderBubble.parentElement as HTMLElement).queryByRole("button", {
        name: "Generate a new reply",
      }),
    ).not.toBeInTheDocument();
  });

  it("FE-5B: delete message asks confirmation and cancel does not mutate", async () => {
    const user = userEvent.setup();
    const mock = mockFetch({
      "/chats/1/messages/2": { body: { ok: true, deleted_count: 1 } },
      "/chats/1/messages": {
        body: [
          message(1, "user", "Question to keep"),
          message(2, "assistant", "Answer to delete"),
        ],
      },
    });

    render(<MessageList chatId={1} />, { wrapper });

    await screen.findByText("Answer to delete");
    const bubble = getBubbleByText("Answer to delete");
    await user.click(within(bubble).getByRole("button", { name: "Delete message" }));

    expect(
      screen.getByText("Delete this message and everything after it?"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(
      mock.mock.calls.some(
        ([url, init]) =>
          String(url).includes("/chats/1/messages/2") &&
          (init as RequestInit | undefined)?.method === "DELETE",
      ),
    ).toBe(false);
  });

  it("FE-5B: confirm delete calls delete-message-and-following mutation", async () => {
    const user = userEvent.setup();
    const mock = mockFetch({
      "/chats/1/messages/2": { body: { ok: true, deleted_count: 1 } },
      "/chats/1/messages": {
        body: [
          message(1, "user", "Question before delete"),
          message(2, "assistant", "Answer to delete"),
        ],
      },
    });

    render(<MessageList chatId={1} />, { wrapper });

    await screen.findByText("Answer to delete");
    const bubble = getBubbleByText("Answer to delete");
    await user.click(within(bubble).getByRole("button", { name: "Delete message" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(
        mock.mock.calls.some(
          ([url, init]) =>
            String(url).includes("/chats/1/messages/2") &&
            (init as RequestInit | undefined)?.method === "DELETE",
        ),
      ).toBe(true);
    });
  });

  it("FE-5B: regenerate calls mutation and does not duplicate user message", async () => {
    const user = userEvent.setup();
    const userMessage = message(1, "user", "Original prompt");
    const oldAssistant = message(2, "assistant", "Old answer");
    const newAssistant = {
      ...message(3, "assistant", "New regenerated answer"),
      variant_group: 2,
      active: true,
      variant_index: 1,
      variant_count: 2,
    };
    const mock = mockFetchWithStreams({
      "/regenerate": {
        sse: [
          { type: "user_message", message: userMessage },
          { type: "delta", content: "New regenerated answer" },
          {
            type: "done",
            chat_id: 1,
            model_id: "openai/gpt-4o",
            user_message: userMessage,
            assistant_message: newAssistant,
            deactivated_message_id: 2,
          },
        ],
      },
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [userMessage, oldAssistant] },
      "/chats": { body: [] },
    });

    render(<ChatCanvas />, { wrapper });

    await screen.findByText("Old answer");
    await user.click(
      screen.getByRole("button", { name: "Generate a new reply" }),
    );

    expect(await screen.findByText("New regenerated answer")).toBeInTheDocument();
    // The old variant is deactivated (kept in cache, not rendered) - the
    // exiting carousel pane may linger for the slide, so wait it out.
    await waitFor(() => {
      expect(screen.queryByText("Old answer")).not.toBeInTheDocument();
    });
    expect(screen.getAllByText("Original prompt")).toHaveLength(1);

    const regenerateCall = mock.mock.calls.find(
      ([url, init]) =>
        String(url).includes("/chats/1/messages/2/regenerate") &&
        (init as RequestInit | undefined)?.method === "POST",
    );
    expect(regenerateCall).toBeTruthy();
    const body = JSON.parse((regenerateCall?.[1] as RequestInit).body as string);
    expect(body.model_id).toBe("openai/gpt-4o");
    expect(body).not.toHaveProperty("provider");
    expect(body).not.toHaveProperty("zdr");
    expect(body).not.toHaveProperty("data_collection");
    expect(body).not.toHaveProperty("allow_fallbacks");
  });

  it("regenerate body carries generation params, persona id and context budget", async () => {
    const user = userEvent.setup();
    const userMessage = message(1, "user", "Original prompt");
    const oldAssistant = message(2, "assistant", "Old answer");
    const newAssistant = {
      ...message(3, "assistant", "New regenerated answer"),
      variant_group: 2,
      active: true,
      variant_index: 1,
      variant_count: 2,
    };
    const mock = mockFetchWithStreams({
      "/regenerate": {
        sse: [
          { type: "user_message", message: userMessage },
          { type: "delta", content: "New regenerated answer" },
          {
            type: "done",
            chat_id: 1,
            model_id: "openai/gpt-4o",
            user_message: userMessage,
            assistant_message: newAssistant,
            deactivated_message_id: 2,
          },
        ],
      },
      "/settings": { body: settingsFixture },
      "/personas": { body: [personaFixture] },
      "/models/openrouter": { body: modelList(["temperature", "max_tokens"]) },
      "/chats/1/messages": { body: [userMessage, oldAssistant] },
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
      expect(
        mock.mock.calls.some(([url]) => String(url).includes("/personas")),
      ).toBe(true);
    });
    await screen.findByText("Old answer");

    await user.click(
      screen.getByRole("button", { name: "Generate a new reply" }),
    );

    expect(await screen.findByText("New regenerated answer")).toBeInTheDocument();

    const regenerateCall = mock.mock.calls.find(
      ([url, init]) =>
        String(url).includes("/chats/1/messages/2/regenerate") &&
        (init as RequestInit | undefined)?.method === "POST",
    );
    expect(regenerateCall).toBeTruthy();
    const body = JSON.parse((regenerateCall?.[1] as RequestInit).body as string);
    // Same assembly as send: filtered by supported_parameters, clamped by model
    expect(body.model_id).toBe("openai/gpt-4o");
    expect(body.generation_params).toEqual({
      temperature: 1.1,
      max_tokens: 16384, // clamped from 999999 to model max_completion_tokens
    });
    expect(body.generation_params).not.toHaveProperty("top_p"); // unsupported
    expect(body.persona_id).toBe(personaFixture.id);
    expect(body.context_budget_tokens).toBe(128000); // clamped to context_length
    expect(body).not.toHaveProperty("message");
    expect(body).not.toHaveProperty("provider");
    expect(body).not.toHaveProperty("zdr");
    expect(body).not.toHaveProperty("data_collection");
    expect(body).not.toHaveProperty("allow_fallbacks");
  });

  it("regenerate streams into the target bubble, replacing its stored text", async () => {
    const user = userEvent.setup();
    const userMessage = message(1, "user", "Original prompt");
    const oldAssistant = message(2, "assistant", "Old answer");
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/regenerate": { response: () => stream.response },
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [userMessage, oldAssistant] },
      "/chats": { body: [] },
    });

    render(<ChatCanvas />, { wrapper });

    await screen.findByText("Old answer");
    await user.click(
      screen.getByRole("button", { name: "Generate a new reply" }),
    );

    // The generation happens IN PLACE: the old pane flips out and from the
    // first delta the same bubble renders the accumulating text.
    stream.emit({ type: "user_message", message: userMessage });
    stream.emit({ type: "delta", content: "Fresh partial" });
    expect(await screen.findByText(/Fresh partial/)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByText("Old answer")).not.toBeInTheDocument();
    });

    // Variant append at done
    stream.emit({
      type: "done",
      chat_id: 1,
      model_id: "openai/gpt-4o",
      user_message: userMessage,
      assistant_message: {
        ...message(3, "assistant", "Fresh full answer"),
        variant_group: 2,
        active: true,
        variant_index: 1,
        variant_count: 2,
      },
      deactivated_message_id: 2,
    });
    stream.close();

    expect(await screen.findByText("Fresh full answer")).toBeInTheDocument();
    expect(screen.queryByText("Old answer")).not.toBeInTheDocument();
    expect(screen.getAllByText("Original prompt")).toHaveLength(1);
  });

  it("clicking the generate chevron invokes onRegenerate with the message id", async () => {
    const user = userEvent.setup();
    const onRegenerate = vi.fn();
    mockFetch({
      "/chats/1/messages": {
        body: [
          message(1, "user", "Callback question"),
          message(2, "assistant", "Callback answer"),
        ],
      },
    });

    render(<MessageList chatId={1} onRegenerate={onRegenerate} />, { wrapper });

    await screen.findByText("Callback answer");
    await user.click(
      screen.getByRole("button", { name: "Generate a new reply" }),
    );

    expect(onRegenerate).toHaveBeenCalledTimes(1);
    expect(onRegenerate).toHaveBeenCalledWith(2);
  });

  it("message actions are disabled and spinner shows while the chat is pending", async () => {
    mockFetch({
      "/chats/1/messages": {
        body: [
          message(1, "user", "Pending question"),
          message(2, "assistant", "Pending answer"),
        ],
      },
    });

    render(
      <MessageList
        chatId={1}
        isPending
        regenerating
        onRegenerate={vi.fn()}
      />,
      { wrapper },
    );

    await screen.findByText("Pending answer");
    const bubble = getBubbleByText("Pending answer");
    // Mutual exclusion: send/regenerate in flight for this chat disables actions
    expect(
      screen.getByRole("button", { name: "Generate a new reply" }),
    ).toBeDisabled();
    expect(
      within(bubble).getByRole("button", { name: "Delete message" }),
    ).toBeDisabled();
  });

  it("FE-5B: generate chevron is disabled when no model is selected", async () => {
    useUiStore.setState({ selectedModelId: null });
    mockFetch({
      "/chats/1/messages": {
        body: [
          message(1, "user", "Question without model"),
          message(2, "assistant", "Latest without model"),
        ],
      },
    });

    render(<MessageList chatId={1} />, { wrapper });

    await screen.findByText("Latest without model");
    // With no model the chevron's accessible name explains why it can't run.
    expect(
      screen.getByRole("button", { name: "Select a model to generate" }),
    ).toBeDisabled();
  });

  it("FE-5B: message load error uses safe mapped message", async () => {
    mockFetch({
      "/chats/1/messages": {
        status: 500,
        body: { detail: "UPSTREAM_RAW_SECRET" },
      },
    });

    render(<MessageList chatId={1} />, { wrapper });

    expect(
      await screen.findByText("Something went wrong. Please try again."),
    ).toBeInTheDocument();
    expect(screen.queryByText("UPSTREAM_RAW_SECRET")).not.toBeInTheDocument();
  });

  it("single-variant bubble shows the generate chevron but no prev arrow or counter", async () => {
    mockFetch({
      "/chats/1/messages": {
        body: [
          message(1, "user", "Plain question"),
          message(2, "assistant", "Plain answer"),
        ],
      },
    });

    render(<MessageList chatId={1} />, { wrapper });

    await screen.findByText("Plain answer");
    // No siblings yet: the Previous button stays MOUNTED (unmounting it
    // would shift the bubble and drop keyboard focus) but disabled, and
    // there is no position counter.
    expect(
      screen.getByRole("button", { name: "Previous reply" }),
    ).toBeDisabled();
    expect(screen.queryByText("1/1")).not.toBeInTheDocument();
    // The forward affordance (generate) is present on the last assistant.
    expect(
      screen.getByRole("button", { name: "Generate a new reply" }),
    ).toBeInTheDocument();
  });

  it("variant siblings render arrows, counter, and activate on navigation", async () => {
    const user = userEvent.setup();
    const activated: string[] = [];
    mockFetch({
      "/chats/1/messages": {
        body: [
          message(1, "user", "Swipe question"),
          {
            ...message(2, "assistant", "First reply"),
            variant_group: 2,
            active: false,
            variant_index: 0,
            variant_count: 2,
          },
          {
            ...message(3, "assistant", "Second reply"),
            variant_group: 2,
            active: true,
            variant_index: 1,
            variant_count: 2,
          },
        ],
      },
    });
    const onActivateVariant = vi.fn((id: number) => {
      activated.push(String(id));
    });

    render(
      <MessageList chatId={1} onActivateVariant={onActivateVariant} />,
      { wrapper },
    );

    // The ACTIVE variant renders; the inactive sibling does not.
    await screen.findByText("Second reply");
    expect(screen.queryByText("First reply")).not.toBeInTheDocument();
    // Counter shows the group position.
    expect(screen.getByText("2/2")).toBeInTheDocument();

    // Left arrow activates the previous sibling.
    await user.click(screen.getByRole("button", { name: "Previous reply" }));
    expect(onActivateVariant).toHaveBeenCalledWith(2);
    expect(activated).toEqual(["2"]);
  });

  it("right arrow on an OLDER variant activates the next sibling (no generate)", async () => {
    const user = userEvent.setup();
    mockFetch({
      "/chats/1/messages": {
        body: [
          message(1, "user", "Swipe question"),
          {
            ...message(2, "assistant", "First reply"),
            variant_group: 2,
            active: true, // older sibling currently active
            variant_index: 0,
            variant_count: 2,
          },
          {
            ...message(3, "assistant", "Second reply"),
            variant_group: 2,
            active: false,
            variant_index: 1,
            variant_count: 2,
          },
        ],
      },
    });
    const onActivateVariant = vi.fn();
    const onRegenerate = vi.fn();

    render(
      <MessageList
        chatId={1}
        onActivateVariant={onActivateVariant}
        onRegenerate={onRegenerate}
      />,
      { wrapper },
    );

    await screen.findByText("First reply");
    expect(screen.getByText("1/2")).toBeInTheDocument();
    // Not on the newest → the right arrow navigates, never generates.
    await user.click(screen.getByRole("button", { name: "Next reply" }));
    expect(onActivateVariant).toHaveBeenCalledWith(3);
    expect(onRegenerate).not.toHaveBeenCalled();
  });

  it("greeting-only chat (no user turn) shows no variant chevrons at all", async () => {
    mockFetch({
      "/chats/1/messages": {
        body: [message(1, "assistant", "Greeting first_mes")],
      },
    });

    render(<MessageList chatId={1} />, { wrapper });

    await screen.findByText("Greeting first_mes");
    // The backend 422s regenerating a greeting (no preceding user message) -
    // the affordance must not exist either.
    expect(
      screen.queryByRole("button", { name: "Generate a new reply" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Previous reply" }),
    ).not.toBeInTheDocument();
  });
});
