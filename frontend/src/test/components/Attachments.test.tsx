/**
 * Attachments.test.tsx - staged image attachments in the composer flow.
 *
 * Covers:
 *  - attach button gating by model image input modality
 *  - staging via the hidden file input and via paste
 *  - upload lifecycle: spinner while uploading, ready thumbnail, failure
 *    toast + auto-removal
 *  - the 4-image cap (further adds quietly ignored)
 *  - manual removal (with object URL revocation)
 *  - send wiring: uploads block send, ready ids go into the request body,
 *    staged list survives a send error (retry re-sends the same ids) and
 *    clears once the message is persisted
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatCanvas } from "@/components/chat/ChatCanvas";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useUiStore } from "@/lib/store/uiStore";
import { useErrorStore } from "@/lib/errors";
import {
  mockFetchWithStreams,
  sseEventsFor,
  jsonResponse,
  controlledSseResponse,
} from "../helpers/streamMocks";
import type { StreamRoute } from "../helpers/streamMocks";
import {
  settingsFixture,
  messageFixture,
  completionFixture,
  modelFixture,
} from "../mocks/fixtures";
import type { ReactNode } from "react";
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

function modelList(input_modalities: string[]): ModelList {
  return {
    source: "user",
    cached: true,
    count: 1,
    models: [{ ...modelFixture, input_modalities }],
  };
}

function setupReadyState() {
  useUiStore.setState({
    selectedChatId: 1,
    selectedModelId: "openai/gpt-4o",
    selectedCharacterId: 1,
  });
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
        {
          id: nextId++,
          mime: "image/png",
          width: 100,
          height: 80,
          byte_size: 1234,
        },
        201,
      ),
  };
}

/** Base routes for a ready composer with an image-capable model. */
function baseRoutes(): Record<string, StreamRoute> {
  return {
    "/settings": { body: settingsFixture },
    "/models/openrouter": { body: modelList(["text", "image"]) },
    "/chats/1/messages": { body: [messageFixture] },
    "/uploads/images": uploadRoute(11),
    "/chats/1/complete/stream": { sse: sseEventsFor(completionFixture) },
    "/chats": { body: [] },
  };
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

function addFiles(files: File[]) {
  fireEvent.change(screen.getByLabelText("Attach image files"), {
    target: { files },
  });
}

function completePostBodies(
  mock: ReturnType<typeof mockFetchWithStreams>,
): Record<string, unknown>[] {
  return mock.mock.calls
    .filter(
      (call: unknown[]) =>
        typeof call[0] === "string" &&
        call[0].includes("/complete/stream") &&
        (call[1] as RequestInit)?.method === "POST",
    )
    .map((call) => JSON.parse((call[1] as RequestInit).body as string));
}

describe("Attachments", () => {
  let objectUrlCounter: number;

  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: null,
      selectedModelId: null,
      selectedCharacterId: null,
    });
    useErrorStore.getState().clearAll();
    // jsdom has no object URL support - install observable stand-ins.
    objectUrlCounter = 0;
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

  // ── Gating by model modality ───────────────────────────────────

  it("disables the attach button with a modality title for a text-only model", async () => {
    setupReadyState();
    mockFetchWithStreams({
      ...baseRoutes(),
      "/models/openrouter": { body: modelList(["text"]) },
    });
    render(<ChatCanvas />, { wrapper });
    await waitForComposerReady();

    const attachButton = screen.getByRole("button", { name: "Attach images" });
    // Stays disabled even after models load - the model is text-only.
    await waitFor(() => {
      expect(attachButton).toHaveAttribute(
        "title",
        "Selected model does not support image input",
      );
    });
    expect(attachButton).toBeDisabled();
  });

  it("enables the attach button for an image-capable model", async () => {
    setupReadyState();
    mockFetchWithStreams(baseRoutes());
    render(<ChatCanvas />, { wrapper });
    await waitForComposerReady();
    await waitForAttachReady();

    expect(
      screen.getByRole("button", { name: "Attach images" }),
    ).toHaveAttribute("title", "Attach images");
  });

  // U8: the attach title states the TRUE reason, not always the modality one.
  it("U8: attach title says to select a chat when none is selected", async () => {
    useUiStore.setState({
      selectedChatId: null,
      selectedModelId: null,
      selectedCharacterId: null,
    });
    mockFetchWithStreams({ "/settings": { body: settingsFixture } });
    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Attach images" }),
      ).toHaveAttribute("title", "Select a chat to attach images");
    });
  });

  it("U8: attach title says to select a model when a chat but no model is set", async () => {
    useUiStore.setState({
      selectedChatId: 1,
      selectedModelId: null,
      selectedCharacterId: 1,
    });
    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/chats/1/messages": { body: [messageFixture] },
      "/chats": { body: [] },
    });
    render(<ChatCanvas />, { wrapper });

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Attach images" }),
      ).toHaveAttribute("title", "Select a model to attach images");
    });
  });

  // U2: image-only send is blocked (backend needs text) - say why, not silence.
  it("U2: send hint asks for a message when images are staged but text is empty", async () => {
    setupReadyState();
    mockFetchWithStreams(baseRoutes());
    render(<ChatCanvas />, { wrapper });
    await waitForComposerReady();
    await waitForAttachReady();

    addFiles([pngFile()]);
    await waitFor(() => {
      expect(screen.getByAltText("Staged image")).toBeInTheDocument();
      expect(
        screen.queryByRole("status", { name: "Uploading image" }),
      ).not.toBeInTheDocument();
    });

    const sendButton = screen.getByRole("button", { name: /send message/i });
    expect(sendButton).toBeDisabled();
    expect(sendButton).toHaveAttribute(
      "title",
      "Add a message to send with your images",
    );
  });

  // ── Staging lifecycle ──────────────────────────────────────────

  it("stages a file from the file input: spinner while uploading, then ready", async () => {
    setupReadyState();
    let releaseUpload!: () => void;
    const uploadGate = new Promise<void>((resolve) => {
      releaseUpload = resolve;
    });
    mockFetchWithStreams({
      ...baseRoutes(),
      "/uploads/images": {
        response: async () => {
          await uploadGate;
          return jsonResponse(
            { id: 11, mime: "image/png", width: 100, height: 80, byte_size: 9 },
            201,
          );
        },
      },
    });
    render(<ChatCanvas />, { wrapper });
    await waitForComposerReady();
    await waitForAttachReady();

    addFiles([pngFile()]);

    // Thumbnail with a local preview + uploading spinner
    expect(await screen.findByAltText("Staged image")).toHaveAttribute(
      "src",
      "blob:preview-1",
    );
    expect(
      screen.getByRole("status", { name: "Uploading image" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/1\/4/)).toBeInTheDocument();

    releaseUpload();
    await waitFor(() => {
      expect(
        screen.queryByRole("status", { name: "Uploading image" }),
      ).not.toBeInTheDocument();
    });
    expect(screen.getByAltText("Staged image")).toBeInTheDocument();
  });

  it("stages image files pasted into the textarea", async () => {
    setupReadyState();
    mockFetchWithStreams(baseRoutes());
    render(<ChatCanvas />, { wrapper });
    await waitForComposerReady();
    await waitForAttachReady();

    fireEvent.paste(screen.getByLabelText("Message"), {
      clipboardData: { files: [pngFile("pasted.png")] },
    });

    expect(await screen.findByAltText("Staged image")).toBeInTheDocument();
    expect(screen.getByText(/1\/4/)).toBeInTheDocument();
  });

  it("ignores pasted non-image files", async () => {
    setupReadyState();
    mockFetchWithStreams(baseRoutes());
    render(<ChatCanvas />, { wrapper });
    await waitForComposerReady();
    await waitForAttachReady();

    const textFile = new File(["hello"], "notes.txt", { type: "text/plain" });
    fireEvent.paste(screen.getByLabelText("Message"), {
      clipboardData: { files: [textFile] },
    });

    await waitFor(() => {
      expect(screen.queryByAltText("Staged image")).not.toBeInTheDocument();
    });
  });

  it("caps staged images at 4 and quietly ignores further adds", async () => {
    setupReadyState();
    mockFetchWithStreams(baseRoutes());
    render(<ChatCanvas />, { wrapper });
    await waitForComposerReady();
    await waitForAttachReady();

    addFiles([
      pngFile("a.png"),
      pngFile("b.png"),
      pngFile("c.png"),
      pngFile("d.png"),
      pngFile("e.png"),
    ]);

    await waitFor(() => {
      expect(screen.getAllByAltText("Staged image")).toHaveLength(4);
    });
    expect(screen.getByText(/4\/4/)).toBeInTheDocument();
    expect(screen.getByText(/up to 4 images per message/i)).toBeInTheDocument();

    // Further adds are ignored without any error surface
    addFiles([pngFile("f.png")]);
    await waitFor(() => {
      expect(screen.getAllByAltText("Staged image")).toHaveLength(4);
    });
    expect(useErrorStore.getState().errors).toHaveLength(0);
  });

  it("remove drops the staged entry and revokes its preview URL", async () => {
    setupReadyState();
    mockFetchWithStreams(baseRoutes());
    const user = userEvent.setup();
    render(<ChatCanvas />, { wrapper });
    await waitForComposerReady();
    await waitForAttachReady();

    addFiles([pngFile()]);
    await screen.findByAltText("Staged image");
    await waitFor(() => {
      expect(
        screen.queryByRole("status", { name: "Uploading image" }),
      ).not.toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "Remove attachment 1" }));

    await waitFor(() => {
      expect(screen.queryByAltText("Staged image")).not.toBeInTheDocument();
    });
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview-1");
  });

  it("failed upload pushes a toast and auto-removes the thumbnail", async () => {
    setupReadyState();
    mockFetchWithStreams({
      ...baseRoutes(),
      "/uploads/images": {
        status: 400,
        body: { detail: "attachment_too_large" },
      },
    });
    render(<ChatCanvas />, { wrapper });
    await waitForComposerReady();
    await waitForAttachReady();

    addFiles([pngFile()]);

    await waitFor(() => {
      expect(useErrorStore.getState().errors).toHaveLength(1);
    });
    expect(useErrorStore.getState().errors[0].code).toBe(
      "attachment_too_large",
    );

    // Error thumbnail auto-removes shortly after (and revokes its preview)
    await waitFor(
      () => {
        expect(screen.queryByAltText("Staged image")).not.toBeInTheDocument();
      },
      { timeout: 4000 },
    );
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview-1");
  });

  // ── Send wiring ────────────────────────────────────────────────

  it("blocks send while an upload is in flight", async () => {
    setupReadyState();
    const user = userEvent.setup();
    let releaseUpload!: () => void;
    const uploadGate = new Promise<void>((resolve) => {
      releaseUpload = resolve;
    });
    mockFetchWithStreams({
      ...baseRoutes(),
      "/uploads/images": {
        response: async () => {
          await uploadGate;
          return jsonResponse(
            { id: 11, mime: "image/png", width: 100, height: 80, byte_size: 9 },
            201,
          );
        },
      },
    });
    render(<ChatCanvas />, { wrapper });
    await waitForComposerReady();
    await waitForAttachReady();

    await user.type(screen.getByLabelText("Message"), "With image");
    addFiles([pngFile()]);
    await screen.findByAltText("Staged image");

    const sendButton = screen.getByRole("button", { name: /send message/i });
    expect(sendButton).toBeDisabled();
    expect(sendButton).toHaveAttribute("title", "Uploading image…");

    releaseUpload();
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /send message/i }),
      ).not.toBeDisabled();
    });
  });

  it("sends ready attachment ids in the body and clears the strip on done", async () => {
    setupReadyState();
    const user = userEvent.setup();
    const mock = mockFetchWithStreams(baseRoutes());
    render(<ChatCanvas />, { wrapper });
    await waitForComposerReady();
    await waitForAttachReady();

    addFiles([pngFile("a.png"), pngFile("b.png")]);
    await waitFor(() => {
      expect(screen.getAllByAltText("Staged image")).toHaveLength(2);
      expect(
        screen.queryByRole("status", { name: "Uploading image" }),
      ).not.toBeInTheDocument();
    });

    await user.type(screen.getByLabelText("Message"), "Look at these");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      const bodies = completePostBodies(mock);
      expect(bodies).toHaveLength(1);
      expect(bodies[0].attachments).toEqual([11, 12]);
      expect(bodies[0].message).toBe("Look at these");
    });

    // Persisted (done event) → staged strip cleared, previews revoked
    await waitFor(() => {
      expect(screen.queryByAltText("Staged image")).not.toBeInTheDocument();
    });
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview-1");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview-2");
  });

  it("clears the staged strip at user_message time, while still streaming", async () => {
    // The image renders inside the sent bubble from the user_message event on;
    // the staged thumbnail must not sit above the composer as a duplicate for
    // the whole (possibly long) stream.
    setupReadyState();
    const user = userEvent.setup();
    const controlled = controlledSseResponse();
    mockFetchWithStreams({
      ...baseRoutes(),
      "/chats/1/complete/stream": { response: () => controlled.response },
    });
    render(<ChatCanvas />, { wrapper });
    await waitForComposerReady();
    await waitForAttachReady();

    addFiles([pngFile()]);
    await waitFor(() => {
      expect(screen.getByAltText("Staged image")).toBeInTheDocument();
      expect(
        screen.queryByRole("status", { name: "Uploading image" }),
      ).not.toBeInTheDocument();
    });

    await user.type(screen.getByLabelText("Message"), "Look");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    // Backend persists the user row and emits ONLY user_message - the
    // stream stays open (no done yet).
    controlled.emit({
      type: "user_message",
      message: completionFixture.user_message,
    });

    // Strip clears immediately; the stream is still running (Stop visible).
    await waitFor(() => {
      expect(screen.queryByAltText("Staged image")).not.toBeInTheDocument();
    });
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:preview-1");
    expect(
      screen.getByRole("button", { name: /stop generating/i }),
    ).toBeInTheDocument();

    // Finish cleanly so the test ends in a settled state.
    controlled.emit({ type: "delta", content: "hi" });
    controlled.emit({ type: "done", ...completionFixture });
    controlled.close();
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /send message/i }),
      ).toBeInTheDocument();
    });
  });

  it("sends no attachments key when nothing is staged", async () => {
    setupReadyState();
    const user = userEvent.setup();
    const mock = mockFetchWithStreams(baseRoutes());
    render(<ChatCanvas />, { wrapper });
    await waitForComposerReady();

    await user.type(screen.getByLabelText("Message"), "Plain text");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      const bodies = completePostBodies(mock);
      expect(bodies).toHaveLength(1);
      expect(bodies[0]).not.toHaveProperty("attachments");
    });
  });

  it("restores staged entries when the stream errors AFTER user_message", async () => {
    // The strip clears at user_message time; a later provider error unlinks
    // the ids server-side - the entries must come back (without a preview
    // bitmap) so the retry still carries the images.
    setupReadyState();
    const user = userEvent.setup();
    const controlled = controlledSseResponse();
    const mock = mockFetchWithStreams({
      ...baseRoutes(),
      "/chats/1/complete/stream": { response: () => controlled.response },
    });
    render(<ChatCanvas />, { wrapper });
    await waitForComposerReady();
    await waitForAttachReady();

    addFiles([pngFile()]);
    await waitFor(() => {
      expect(screen.getByAltText("Staged image")).toBeInTheDocument();
      expect(
        screen.queryByRole("status", { name: "Uploading image" }),
      ).not.toBeInTheDocument();
    });

    await user.type(screen.getByLabelText("Message"), "Doomed later");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    // user_message persists → strip clears...
    controlled.emit({
      type: "user_message",
      message: completionFixture.user_message,
    });
    await waitFor(() => {
      expect(screen.queryByAltText("Staged image")).not.toBeInTheDocument();
    });

    // ...then the provider fails mid-stream.
    controlled.emit({ type: "error", status: 502, code: "network_error" });
    controlled.close();

    // Error banner + the staged entry is BACK (blank tile: preview revoked,
    // but the remove button proves the entry exists with its id).
    await waitFor(() => {
      expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
      expect(
        screen.getByRole("button", { name: "Remove attachment 1" }),
      ).toBeInTheDocument();
    });

    // Retry re-sends the SAME attachment id.
    const textarea = screen.getByLabelText("Message") as HTMLTextAreaElement;
    await waitFor(() => expect(textarea.value).toBe("Doomed later"));
    await user.click(screen.getByRole("button", { name: /send message/i }));
    await waitFor(() => {
      const bodies = completePostBodies(mock);
      expect(bodies).toHaveLength(2);
      expect(bodies[1].attachments).toEqual([11]);
    });
  });

  it("keeps staged attachments on send error so a retry re-sends the same ids", async () => {
    setupReadyState();
    const user = userEvent.setup();
    const mock = mockFetchWithStreams({
      ...baseRoutes(),
      "/chats/1/complete/stream": {
        response: () => jsonResponse({ detail: "model_no_image_input" }, 400),
      },
    });
    render(<ChatCanvas />, { wrapper });
    await waitForComposerReady();
    await waitForAttachReady();

    addFiles([pngFile()]);
    await waitFor(() => {
      expect(screen.getByAltText("Staged image")).toBeInTheDocument();
      expect(
        screen.queryByRole("status", { name: "Uploading image" }),
      ).not.toBeInTheDocument();
    });

    const textarea = screen.getByLabelText("Message") as HTMLTextAreaElement;
    await user.type(textarea, "Doomed send");
    await user.click(screen.getByRole("button", { name: /send message/i }));

    // Error banner with the mapped message; draft restored; strip intact
    await waitFor(() => {
      expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
    });
    expect(
      screen.getAllByText(/does not support image input/i).length,
    ).toBeGreaterThan(0);
    await waitFor(() => {
      expect(textarea.value).toBe("Doomed send");
    });
    expect(screen.getByAltText("Staged image")).toBeInTheDocument();
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();

    // Retry re-sends the SAME upload id (backend unlinked it on failure)
    await user.click(screen.getByRole("button", { name: /send message/i }));
    await waitFor(() => {
      const bodies = completePostBodies(mock);
      expect(bodies).toHaveLength(2);
      expect(bodies[0].attachments).toEqual([11]);
      expect(bodies[1].attachments).toEqual([11]);
    });
    expect(screen.getByAltText("Staged image")).toBeInTheDocument();
  });
});
