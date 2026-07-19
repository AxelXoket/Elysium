/**
 * ChatLayerBugFixes.test.tsx - regression tests for confirmed chat/attachment
 * layer bugs (adversarial audit).
 *
 *  F1 - live composer draft must be PER-CHAT (privacy leak: a draft typed in
 *       chat A bled into chat B and could be sent there).
 *  F2 - staged images must not be sendable after switching to a text-only
 *       model (gating hole → backend 400 + wasted round-trip).
 *  F3 - staged preview object URLs must be revoked when a chat leaves the
 *       chats list (deleted elsewhere) - otherwise the blob leaks.
 *  F4 - in-flight SSE streams must be aborted when the hook host unmounts.
 *  F5 - the 4-image cap must hold when two adds land in one tick (the cap was
 *       computed from a stale render closure).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, act, renderHook } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatCanvas } from "@/components/chat/ChatCanvas";
import { useStreamingCompletion } from "@/lib/chat/useStreamingCompletion";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useUiStore } from "@/lib/store/uiStore";
import { useErrorStore } from "@/lib/errors";
import { keys } from "@/lib/query/keys";
import {
  mockFetchWithStreams,
  sseEventsFor,
  controlledSseResponse,
  jsonResponse,
} from "../helpers/streamMocks";
import type { StreamRoute } from "../helpers/streamMocks";
import {
  settingsFixture,
  messageFixture,
  completionFixture,
  modelFixture,
  chatFixture,
} from "../mocks/fixtures";
import type { ReactNode } from "react";
import type { Chat } from "@/lib/schemas/chats";
import type { ModelList } from "@/lib/schemas/models";

// Capture the QueryClient a render uses so F3 can mutate the chats query.
let lastQueryClient: QueryClient;

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  lastQueryClient = qc;
  return (
    <QueryClientProvider client={qc}>
      <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
    </QueryClientProvider>
  );
}

function hookWrapper(qc: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

function setupReadyState() {
  useUiStore.setState({
    selectedChatId: 1,
    selectedModelId: "openai/gpt-4o",
    selectedCharacterId: 1,
  });
}

function imageModelList(): ModelList {
  return {
    source: "user",
    cached: true,
    count: 1,
    models: [{ ...modelFixture, input_modalities: ["text", "image"] }],
  };
}

/** A vision model plus a text-only model, for the F2 model switch. */
function visionAndTextModels(): ModelList {
  return {
    source: "user",
    cached: true,
    count: 2,
    models: [
      { ...modelFixture, id: "openai/gpt-4o", input_modalities: ["text", "image"] },
      {
        ...modelFixture,
        id: "meta/text-only",
        name: "Text Only",
        input_modalities: ["text"],
      },
    ],
  };
}

function pngFile(name = "photo.png"): File {
  return new File([new Uint8Array([137, 80, 78, 71])], name, {
    type: "image/png",
  });
}

/** Upload route resolving 201 with incrementing ids starting at `firstId`. */
function uploadRoute(firstId: number): StreamRoute {
  let nextId = firstId;
  return {
    response: () =>
      jsonResponse(
        { id: nextId++, mime: "image/png", width: 100, height: 80, byte_size: 1234 },
        201,
      ),
  };
}

function addFiles(files: File[]) {
  fireEvent.change(screen.getByLabelText("Attach image files"), {
    target: { files },
  });
}

async function waitForComposerReady() {
  await waitFor(() => {
    expect(screen.getByLabelText("Message")).not.toBeDisabled();
  });
}

async function waitForAttachReady() {
  await waitFor(() => {
    expect(
      screen.getByRole("button", { name: "Attach images" }),
    ).not.toBeDisabled();
  });
}

function completePostBodies(
  mock: ReturnType<typeof mockFetchWithStreams>,
): { url: string; body: Record<string, unknown> }[] {
  return mock.mock.calls
    .filter(
      (call: unknown[]) =>
        typeof call[0] === "string" &&
        call[0].includes("/complete/stream") &&
        (call[1] as RequestInit)?.method === "POST",
    )
    .map((call) => ({
      url: call[0] as string,
      body: JSON.parse((call[1] as RequestInit).body as string),
    }));
}

describe("Chat layer bug fixes", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: null,
      selectedModelId: null,
      selectedCharacterId: null,
    });
    useErrorStore.getState().clearAll();
    // jsdom has no object URL support - install observable stand-ins.
    let objectUrlCounter = 0;
    URL.createObjectURL = vi.fn(() => {
      objectUrlCounter += 1;
      return `blob:preview-${objectUrlCounter}`;
    });
    URL.revokeObjectURL = vi.fn();
  });

  afterEach(() => {
    Reflect.deleteProperty(URL, "createObjectURL");
    Reflect.deleteProperty(URL, "revokeObjectURL");
    vi.restoreAllMocks();
  });

  // ── F1: per-chat live draft ────────────────────────────────────

  it("F1: a live draft does not bleed into another chat and is restored on return", async () => {
    const user = userEvent.setup();
    setupReadyState();
    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/2/messages": { body: [] },
      "/chats": { body: [] },
    });
    render(<ChatCanvas />, { wrapper });
    await waitForComposerReady();

    const textarea = () =>
      screen.getByLabelText("Message") as HTMLTextAreaElement;

    await user.type(textarea(), "chat one secret");
    expect(textarea().value).toBe("chat one secret");

    // Switch to chat 2 - its composer must be empty, not chat 1's text.
    act(() => useUiStore.setState({ selectedChatId: 2 }));
    await waitFor(() => expect(textarea()).not.toBeDisabled());
    expect(textarea().value).toBe("");

    // Switch back - chat 1's unsent text is restored.
    act(() => useUiStore.setState({ selectedChatId: 1 }));
    await waitFor(() => expect(textarea().value).toBe("chat one secret"));
  });

  it("F1: sending from another chat uses that chat's draft, never the first chat's", async () => {
    const user = userEvent.setup();
    setupReadyState();
    const mock = mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/2/messages": { body: [] },
      "/chats/1/complete/stream": { sse: sseEventsFor(completionFixture) },
      "/chats/2/complete/stream": { sse: sseEventsFor(completionFixture) },
      "/chats": { body: [] },
    });
    render(<ChatCanvas />, { wrapper });
    await waitForComposerReady();

    const textarea = () =>
      screen.getByLabelText("Message") as HTMLTextAreaElement;

    await user.type(textarea(), "leak me to chat one");

    act(() => useUiStore.setState({ selectedChatId: 2 }));
    await waitFor(() => expect(textarea()).not.toBeDisabled());
    // Chat 2 starts empty and gets its own text.
    expect(textarea().value).toBe("");
    await user.type(textarea(), "chat two message");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      const posts = completePostBodies(mock);
      expect(posts).toHaveLength(1);
    });
    const posts = completePostBodies(mock);
    // The send targeted chat 2 with chat 2's text - chat 1's draft never left.
    expect(posts[0].url).toContain("/chats/2/complete/stream");
    expect(posts[0].body.message).toBe("chat two message");
    expect(posts[0].body.message).not.toBe("leak me to chat one");
  });

  // ── F2: staged images + text-only model after a switch ─────────

  it("F2: staged images cannot be sent after switching to a text-only model", async () => {
    const user = userEvent.setup();
    setupReadyState();
    const mock = mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/models/openrouter": { body: visionAndTextModels() },
      "/chats/1/messages": { body: [messageFixture] },
      "/uploads/images": uploadRoute(11),
      "/chats/1/complete/stream": { sse: sseEventsFor(completionFixture) },
      "/chats": { body: [] },
    });
    render(<ChatCanvas />, { wrapper });
    await waitForComposerReady();
    await waitForAttachReady();

    await user.type(screen.getByLabelText("Message"), "See this image");
    addFiles([pngFile()]);
    await waitFor(() => {
      expect(screen.getByAltText("Staged image")).toBeInTheDocument();
      expect(
        screen.queryByRole("status", { name: "Uploading image" }),
      ).not.toBeInTheDocument();
    });

    // On the vision model the send is available.
    expect(
      screen.getByRole("button", { name: /send message/i }),
    ).not.toBeDisabled();

    // Switch to a text-only model - send must lock with a clear reason.
    act(() => useUiStore.setState({ selectedModelId: "meta/text-only" }));
    const sendButton = screen.getByRole("button", { name: /send message/i });
    await waitFor(() => expect(sendButton).toBeDisabled());
    expect(sendButton).toHaveAttribute(
      "title",
      "Selected model does not support images - remove them or switch models",
    );

    // Attempting Enter must not fire a completion POST.
    fireEvent.keyDown(screen.getByLabelText("Message"), {
      key: "Enter",
      code: "Enter",
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(completePostBodies(mock)).toHaveLength(0);
  });

  // ── F3: revoke previews when a chat leaves the list ────────────

  it("F3: staged previews are revoked when their chat leaves the chats list", async () => {
    setupReadyState();
    const chat2: Chat = { ...chatFixture, id: 2, title: "Chat Two" };
    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/models/openrouter": { body: imageModelList() },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats/2/messages": { body: [] },
      "/uploads/images": uploadRoute(11),
      "/chats": { body: [chatFixture, chat2] },
    });
    render(<ChatCanvas />, { wrapper });
    await waitForComposerReady();
    await waitForAttachReady();

    addFiles([pngFile()]);
    await screen.findByAltText("Staged image");
    // Let the chats query settle so the reconcile effect has the real list.
    await waitFor(() => {
      expect(lastQueryClient.getQueryData(keys.chats())).toBeDefined();
    });
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();

    // Leave chat 1 so its previews are no longer "in use".
    act(() => useUiStore.setState({ selectedChatId: 2 }));
    await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
    // Still present while chat 1 remains in the list.
    expect(URL.revokeObjectURL).not.toHaveBeenCalledWith("blob:preview-1");

    // Chat 1 disappears from the list (deleted elsewhere) → its blob is freed.
    act(() => {
      lastQueryClient.setQueryData(keys.chats(), [chat2]);
    });
    await waitFor(() => {
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview-1");
    });
  });

  it("F3: the currently-viewed chat's previews are NOT revoked", async () => {
    setupReadyState();
    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/models/openrouter": { body: imageModelList() },
      "/chats/1/messages": { body: [messageFixture] },
      "/uploads/images": uploadRoute(11),
      "/chats": { body: [chatFixture] },
    });
    render(<ChatCanvas />, { wrapper });
    await waitForComposerReady();
    await waitForAttachReady();

    addFiles([pngFile()]);
    await screen.findByAltText("Staged image");

    // Even if chat 1 is absent from the list, it is the selected chat - its
    // in-use previews must survive.
    act(() => {
      lastQueryClient.setQueryData(keys.chats(), []);
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(URL.revokeObjectURL).not.toHaveBeenCalledWith("blob:preview-1");
    expect(screen.getByAltText("Staged image")).toBeInTheDocument();
  });

  // ── F4: abort in-flight streams on unmount ─────────────────────

  it("F4: unmounting the hook host aborts in-flight streams", async () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const stream = controlledSseResponse();
    mockFetchWithStreams({
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    const abortSpy = vi.spyOn(AbortController.prototype, "abort");
    const { result, unmount } = renderHook(() => useStreamingCompletion(), {
      wrapper: hookWrapper(qc),
    });

    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.startSend({
        chatId: 1,
        message: "stream me",
        modelId: "m",
      });
    });
    await waitFor(() => {
      expect(result.current.streamingByChat.has(1)).toBe(true);
    });
    expect(abortSpy).not.toHaveBeenCalled();

    unmount();
    expect(abortSpy).toHaveBeenCalled();

    // Let the aborted request settle so no dangling promise remains. The abort
    // already tore down the stream, so there is nothing more to close here.
    await act(() => sendPromise);
  });

  // ── F5: cap holds for two adds in one tick ─────────────────────

  it("F5: two synchronous adds respect the 4-image cap", async () => {
    setupReadyState();
    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/models/openrouter": { body: imageModelList() },
      "/chats/1/messages": { body: [messageFixture] },
      "/uploads/images": uploadRoute(11),
      "/chats": { body: [] },
    });
    render(<ChatCanvas />, { wrapper });
    await waitForComposerReady();
    await waitForAttachReady();

    const input = screen.getByLabelText("Attach image files");
    // Two adds of three files, batched into one commit (no re-render between).
    // The stale-closure bug would stage 6; the cap must hold at 4.
    act(() => {
      fireEvent.change(input, {
        target: { files: [pngFile("a.png"), pngFile("b.png"), pngFile("c.png")] },
      });
      fireEvent.change(input, {
        target: { files: [pngFile("d.png"), pngFile("e.png"), pngFile("f.png")] },
      });
    });

    await waitFor(() => {
      expect(screen.getAllByAltText("Staged image")).toHaveLength(4);
    });
    expect(screen.getByText(/4\/4/)).toBeInTheDocument();
    expect(useErrorStore.getState().errors).toHaveLength(0);
  });
});
