/**
 * FileDrop.test.tsx - v1.1 KUME B: drag-and-drop overlay + the file:///
 * navigation guard (B0), Files-only gating (H14), raw-file passthrough (H9),
 * gate composition (B2/H18), and the window-level net.
 *
 * jsdom has no DragEvent constructor: target handlers take a dataTransfer init
 * object via fireEvent.drag*, and window-net assertions build a plain Event +
 * Object.defineProperty(dataTransfer) and check defaultPrevented.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatCanvas } from "@/components/chat/ChatCanvas";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useUiStore } from "@/lib/store/uiStore";
import { useErrorStore } from "@/lib/errors";
import {
  mockFetchWithStreams,
  controlledSseResponse,
  jsonResponse,
  sseEventsFor,
  type StreamRoute,
} from "../helpers/streamMocks";
import { settingsFixture, messageFixture, completionFixture, modelFixture } from "../mocks/fixtures";
import type { ModelList } from "@/lib/schemas/models";
import type { ReactNode } from "react";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
    </QueryClientProvider>
  );
}

function modelList(input_modalities: string[]): ModelList {
  return { source: "user", cached: true, count: 1, models: [{ ...modelFixture, input_modalities }] };
}

function setupReadyState() {
  useUiStore.setState({
    selectedChatId: 1,
    selectedModelId: "openai/gpt-4o",
    selectedCharacterId: 1,
  });
}

function pngFile(name = "photo.png"): File {
  return new File([new Uint8Array([137, 80, 78, 71])], name, { type: "image/png" });
}

function baseRoutes(modalities = ["text", "image"]): Record<string, StreamRoute> {
  return {
    "/settings": { body: settingsFixture },
    "/models/openrouter": { body: modelList(modalities) },
    "/chats/1/messages": { body: [messageFixture] },
    "/uploads/images": {
      response: (init) =>
        (init?.method ?? "GET").toUpperCase() === "DELETE"
          ? jsonResponse({ ok: true }, 200)
          : jsonResponse(
              { id: 11, mime: "image/png", width: 100, height: 80, byte_size: 1 },
              201,
            ),
    },
    "/chats/1/complete/stream": { sse: sseEventsFor(completionFixture) },
    "/chats": { body: [] },
  };
}

/** DataTransfer stub for a target drag event (fireEvent.drag*). */
function fileDT(files: File[], itemTypes: string[]) {
  return {
    types: ["Files"],
    files,
    items: itemTypes.map((type) => ({ kind: "file", type })),
  };
}

async function renderReady(routes = baseRoutes()) {
  mockFetchWithStreams(routes);
  render(<ChatCanvas />, { wrapper });
  await waitFor(() => {
    expect(screen.getByLabelText("Message")).not.toBeDisabled();
  });
  return screen.getByRole("main");
}

/** Dispatch a raw window drag event with a stubbed dataTransfer.types. */
function dispatchWindowDrag(type: "dragover" | "drop", dtTypes: string[]): Event {
  const ev = new Event(type, { cancelable: true, bubbles: true });
  Object.defineProperty(ev, "dataTransfer", {
    value: { types: dtTypes, files: [], items: [] },
  });
  window.dispatchEvent(ev);
  return ev;
}

describe("FileDrop", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: null,
      selectedModelId: null,
      selectedCharacterId: null,
    });
    useErrorStore.getState().clearAll();
    let n = 0;
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => `blob:preview-${++n}`),
      revokeObjectURL: vi.fn(),
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("shows the overlay on a file dragEnter when the gate is open", async () => {
    setupReadyState();
    const main = await renderReady();

    fireEvent.dragEnter(main, { dataTransfer: fileDT([], ["image/png"]) });

    expect(
      await screen.findByRole("status", { name: "Drop images to attach" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Drop your image here")).toBeInTheDocument();
  });

  it("hides the overlay after dragLeave to depth 0, and after drop", async () => {
    setupReadyState();
    const main = await renderReady();

    fireEvent.dragEnter(main, { dataTransfer: fileDT([], ["image/png"]) });
    await screen.findByRole("status", { name: "Drop images to attach" });

    fireEvent.dragLeave(main, { dataTransfer: fileDT([], ["image/png"]) });
    await waitFor(() => {
      expect(
        screen.queryByRole("status", { name: "Drop images to attach" }),
      ).not.toBeInTheDocument();
    });
  });

  it("drop routes RAW files into the staging pipeline (H9)", async () => {
    setupReadyState();
    const main = await renderReady();

    fireEvent.drop(main, { dataTransfer: fileDT([pngFile()], ["image/png"]) });

    expect(await screen.findByAltText("Staged image")).toBeInTheDocument();
  });

  it("H9/FF9: dropping a GIF stages nothing and toasts", async () => {
    setupReadyState();
    const main = await renderReady();

    const gif = new File([new Uint8Array([71, 73, 70])], "a.gif", {
      type: "image/gif",
    });
    fireEvent.drop(main, { dataTransfer: fileDT([gif], ["image/gif"]) });

    await waitFor(() => {
      expect(useErrorStore.getState().errors[0]?.code).toBe("attachment_invalid");
    });
    expect(screen.queryByAltText("Staged image")).not.toBeInTheDocument();
  });

  it("H9/FF9: dropping 5 PNGs stages 4 and toasts too_many_attachments", async () => {
    setupReadyState();
    const main = await renderReady();

    const five = Array.from({ length: 5 }, (_, i) => pngFile(`p${i}.png`));
    fireEvent.drop(main, {
      dataTransfer: fileDT(five, five.map(() => "image/png")),
    });

    await waitFor(() => {
      expect(screen.getAllByAltText("Staged image")).toHaveLength(4);
    });
    expect(useErrorStore.getState().errors[0]?.code).toBe("too_many_attachments");
  });

  it("H14: a text-selection drag is untouched (window net does NOT preventDefault)", async () => {
    setupReadyState();
    await renderReady();

    const textDrag = dispatchWindowDrag("dragover", ["text/plain"]);
    expect(textDrag.defaultPrevented).toBe(false);

    const fileDrag = dispatchWindowDrag("dragover", ["Files"]);
    expect(fileDrag.defaultPrevented).toBe(true);
  });

  it("B0: the window net preventDefaults a Files drop (file:/// nav kill)", async () => {
    setupReadyState();
    await renderReady();

    const drop = dispatchWindowDrag("drop", ["Files"]);
    expect(drop.defaultPrevented).toBe(true);
  });

  it("B2: gate closed for a text-only model - no overlay, nothing staged, net still swallows", async () => {
    setupReadyState();
    const main = await renderReady(baseRoutes(["text"]));

    fireEvent.dragEnter(main, { dataTransfer: fileDT([], ["image/png"]) });
    await waitFor(() => {
      expect(
        screen.queryByRole("status", { name: "Drop images to attach" }),
      ).not.toBeInTheDocument();
    });

    fireEvent.drop(main, { dataTransfer: fileDT([pngFile()], ["image/png"]) });
    await waitFor(() => {
      expect(screen.queryByAltText("Staged image")).not.toBeInTheDocument();
    });

    // The window net still guards against file:/// navigation.
    expect(dispatchWindowDrag("drop", ["Files"]).defaultPrevented).toBe(true);
  });

  it("H18: gate closed while streaming - overlay suppressed, re-enabled after done", async () => {
    setupReadyState();
    const stream = controlledSseResponse();
    const main = await renderReady({
      ...baseRoutes(),
      "/chats/1/complete/stream": { response: () => stream.response },
    });

    // Kick off a send to enter the pending state.
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "hi" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));
    await waitFor(() => {
      expect(screen.getByLabelText("Message")).toBeDisabled();
    });

    // Drag while streaming -> no overlay (H18 gate closed).
    fireEvent.dragEnter(main, { dataTransfer: fileDT([], ["image/png"]) });
    await waitFor(() => {
      expect(
        screen.queryByRole("status", { name: "Drop images to attach" }),
      ).not.toBeInTheDocument();
    });

    // Finish the stream.
    act(() => {
      stream.emit({ type: "user_message", message: completionFixture.user_message });
      stream.emit({ type: "delta", content: "hi" });
      stream.emit({ type: "done", ...completionFixture });
      stream.close();
    });
    await waitFor(() => {
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
    });

    // Gate open again.
    fireEvent.dragEnter(main, { dataTransfer: fileDT([], ["image/png"]) });
    expect(
      await screen.findByRole("status", { name: "Drop images to attach" }),
    ).toBeInTheDocument();
  });

  it("tolerates an empty item type on dragEnter (WebView2 quirk)", async () => {
    setupReadyState();
    const main = await renderReady();

    fireEvent.dragEnter(main, { dataTransfer: fileDT([], [""]) });
    expect(
      await screen.findByRole("status", { name: "Drop images to attach" }),
    ).toBeInTheDocument();
  });
});
