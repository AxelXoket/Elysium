/**
 * StreamTailHandover.test.tsx - the typewriter must survive `done`.
 *
 * A hook-level test cannot see this bug. `done` writes the persisted row into
 * the query cache and clears the streaming entry in the SAME batch, so the
 * transient bubble vanishes and the real row appears holding its full text.
 * Whatever the typewriter had not shown yet lands in one frame. Measured on an
 * 800 character reply delivered as a single delta: 526 characters had been
 * typed, so 274 of them - 34 percent - appeared at once.
 *
 * So the assertion has to span the hook AND the teardown, which means driving
 * the real components through a real stream.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { screen, waitFor, act } from "@testing-library/react";
import type { ReactNode } from "react";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { ChatCanvas } from "@/components/chat/ChatCanvas";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { useUiStore } from "@/lib/store/uiStore";
import { useErrorStore } from "@/lib/errors";
import {
  mockFetchWithStreams,
  controlledSseResponse,
  jsonResponse,
} from "../helpers/streamMocks";
import { settingsFixture, modelListFixture } from "../mocks/fixtures";
import type { Message } from "@/lib/schemas/chats";

function wrapper({ children }: { children: ReactNode }) {
  return <GenerationSettingsProvider>{children}</GenerationSettingsProvider>;
}

function msg(
  id: number,
  role: "user" | "assistant",
  content: string,
): Message {
  return {
    id,
    chat_id: 1,
    role,
    content,
    created_at: "2026-01-01T00:00:00Z",
  };
}

/** A long reply, so the typewriter is provably still behind when done lands. */
const REPLY = "Sea water moves in long slow bands. ".repeat(24);

describe("stream tail handover", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: 1,
      selectedModelId: "openai/gpt-4o",
      selectedCharacterId: 1,
    });
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    useUiStore.setState({
      selectedChatId: null,
      selectedModelId: null,
      selectedCharacterId: null,
    });
  });

  /**
   * How much of REPLY is on screen right now.
   *
   * Measured as the longest PREFIX of REPLY present in the bubble rather than
   * the bubble's own text length: the shell also carries a timestamp and the
   * action labels, so a raw length overshoots by a handful of characters and
   * an assertion built on it compares the reply against the chrome around it.
   */
  function shownReplyLength(): number {
    const nodes = [...document.querySelectorAll(".message-bubble-shell")];
    const last = nodes[nodes.length - 1];
    const text = last ? last.textContent ?? "" : "";
    let lo = 0;
    let hi = REPLY.length;
    while (lo < hi) {
      const mid = Math.ceil((lo + hi) / 2);
      if (text.includes(REPLY.slice(0, mid))) lo = mid;
      else hi = mid - 1;
    }
    return lo;
  }

  async function startStream() {
    const stream = controlledSseResponse();
    let sent = false;
    mockFetchWithStreams({
      "/settings": { body: settingsFixture },
      "/models": { body: modelListFixture },
      "/chats/1/messages": {
        response: () =>
          jsonResponse(
            sent
              ? [msg(1, "user", "tell me about the sea"), msg(2, "assistant", REPLY)]
              : [msg(1, "user", "tell me about the sea")],
          ),
      },
      "/chats/1/complete/stream": { response: () => stream.response },
      "/chats": { body: [] },
    });

    renderWithQueryClient(<ChatCanvas />, { wrapper });
    await waitFor(() =>
      expect(screen.getByLabelText("Message")).not.toBeDisabled(),
    );

    const box = screen.getByLabelText("Message") as HTMLTextAreaElement;
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype,
      "value",
    )!.set!;
    act(() => {
      setter.call(box, "tell me about the sea");
      box.dispatchEvent(new Event("input", { bubbles: true }));
    });
    act(() => {
      screen.getByRole("button", { name: "Send message" }).click();
    });

    stream.emit({ type: "user_message", message: msg(1, "user", "tell me about the sea") });
    // The whole reply in ONE delta - the fast-model shape.
    stream.emit({ type: "delta", content: REPLY });
    await waitFor(() => expect(shownReplyLength()).toBeGreaterThan(0));

    return {
      stream,
      finish: () => {
        sent = true;
        stream.emit({
          type: "done",
          chat_id: 1,
          model_id: "openai/gpt-4o",
          user_message: msg(1, "user", "tell me about the sea"),
          assistant_message: msg(2, "assistant", REPLY),
        });
        stream.close();
      },
    };
  }

  it("does not dump the rest of the reply when done arrives", async () => {
    const { finish } = await startStream();

    // Mid-stream: the typewriter is deliberately behind the buffer.
    const before = shownReplyLength();
    expect(
      before,
      "the reply was already fully shown, so this test proves nothing",
    ).toBeLessThan(REPLY.length);

    await act(async () => {
      finish();
      await new Promise((r) => setTimeout(r, 30));
    });

    // The whole point: the persisted row must NOT appear in full the moment
    // the entry clears. The bound is a property, not a retyped frame budget -
    // any per-frame number here would be the same class of problem as pacing
    // constants that tests carry their own copy of.
    expect(
      shownReplyLength(),
      "done painted the remainder of the reply in one frame",
    ).toBeLessThan(REPLY.length);
  });

  it("still finishes the reply", async () => {
    // Positive control for the test above. Without it, a hook that simply
    // never showed anything would also satisfy "less than the full length".
    const { finish } = await startStream();
    await act(async () => {
      finish();
      await new Promise((r) => setTimeout(r, 30));
    });

    await waitFor(
      () => expect(shownReplyLength()).toBe(REPLY.length),
      { timeout: 4000 },
    );
  });

  it("re-enables the composer at done, while the text is still catching up", () => {
    // Permanently forbids the "keep the StreamingEntry alive" class of fix:
    // the reply is saved, so the reader must be able to type again and the
    // Stop button must be gone, even though the typewriter is still running.
    return (async () => {
      const { finish } = await startStream();
      await act(async () => {
        finish();
        await new Promise((r) => setTimeout(r, 30));
      });

      expect(shownReplyLength()).toBeLessThan(REPLY.length);
      expect(screen.getByLabelText("Message")).not.toBeDisabled();
      expect(
        screen.queryByRole("button", { name: /stop/i }),
        "Stop was still offered on a reply that is already saved",
      ).not.toBeInTheDocument();
    })();
  });
});
