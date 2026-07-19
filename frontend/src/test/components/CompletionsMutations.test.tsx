/**
 * CompletionsMutations.test.tsx - hook-level race behavior for
 * useSendMessage / useRegenerateMessage.
 *
 * Covers:
 *  - onSuccess removes ONLY the mutation's own optimistic message
 *  - onError removes ONLY the mutation's own optimistic message (no snapshot
 *    restore that would clobber a concurrent send's committed state)
 *  - send errors do NOT push a toast (Composer banner is the single surface)
 *  - regenerate request body goes through buildRegeneratePayload
 *    (filtered by model support, clamped, persona + context budget included)
 *  - regenerate makes NO optimistic cache change while pending
 *  - regenerate errors push a toast and leave the cache intact
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useSendMessage, useRegenerateMessage } from "@/lib/query/completions";
import { keys } from "@/lib/query/keys";
import { useErrorStore } from "@/lib/errors";
import { modelFixture, personaFixture } from "../mocks/fixtures";
import type { Message } from "@/lib/schemas/chats";
import type { ReactNode } from "react";

function msg(id: number, role: "user" | "assistant", content: string): Message {
  return {
    id,
    chat_id: 1,
    role,
    content,
    created_at: "2026-01-01T00:00:00Z",
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function completionResponse(userId: number, assistantId: number, text: string) {
  return {
    chat_id: 1,
    model_id: "openai/gpt-4o",
    user_message: msg(userId, "user", text),
    assistant_message: msg(assistantId, "assistant", `reply to ${text}`),
  };
}

/**
 * Stubs fetch so that each POST to `matcher` stays pending until resolved by
 * the test. Requests are keyed by their JSON body `message` field (or the URL
 * when no message is present) for deterministic targeting.
 */
function deferredFetch(matcher: string) {
  const pending = new Map<string, (res: Response) => void>();
  const bodies = new Map<string, unknown>();
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes(matcher)) {
      const body = init?.body ? JSON.parse(init.body as string) : {};
      const key = typeof body.message === "string" ? body.message : url;
      bodies.set(key, body);
      return new Promise<Response>((resolve) => {
        pending.set(key, resolve);
      });
    }
    return jsonResponse([]);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, pending, bodies };
}

function createWrapper(qc: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

function newQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function messagesInCache(qc: QueryClient): Message[] {
  return qc.getQueryData<Message[]>(keys.messages(1)) ?? [];
}

const seedMessage = msg(1, "assistant", "greeting");

describe("useSendMessage race behavior", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("onSuccess removes only its own optimistic message", async () => {
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedMessage]);
    const { pending } = deferredFetch("/chats/1/complete");

    const { result } = renderHook(() => useSendMessage(), {
      wrapper: createWrapper(qc),
    });

    await act(async () => {
      result.current.mutate({ chatId: 1, message: "first", modelId: "m" });
    });
    await act(async () => {
      result.current.mutate({ chatId: 1, message: "second", modelId: "m" });
    });

    // Both optimistic user messages appear (negative ids), both requests in flight
    await waitFor(() => {
      expect(messagesInCache(qc).filter((m) => m.id < 0)).toHaveLength(2);
      expect(pending.size).toBe(2);
    });
    const firstOptimisticId = messagesInCache(qc).find(
      (m) => m.content === "first",
    )!.id;
    const secondOptimisticId = messagesInCache(qc).find(
      (m) => m.content === "second",
    )!.id;

    // First send succeeds while second is still in flight
    pending.get("first")!(jsonResponse(completionResponse(10, 11, "first")));

    await waitFor(() => {
      expect(messagesInCache(qc).some((m) => m.id === 11)).toBe(true);
    });
    const afterFirst = messagesInCache(qc);
    // Own optimistic removed, server pair appended
    expect(afterFirst.some((m) => m.id === firstOptimisticId)).toBe(false);
    expect(afterFirst.some((m) => m.id === 10)).toBe(true);
    // Concurrent send's optimistic message SURVIVES
    expect(afterFirst.some((m) => m.id === secondOptimisticId)).toBe(true);

    // Second send settles cleanly too
    pending.get("second")!(jsonResponse(completionResponse(12, 13, "second")));
    await waitFor(() => {
      expect(messagesInCache(qc).filter((m) => m.id < 0)).toHaveLength(0);
    });
    expect(messagesInCache(qc).map((m) => m.id)).toEqual(
      expect.arrayContaining([1, 10, 11, 12, 13]),
    );
  });

  it("onError removes only its own optimistic message and pushes no toast", async () => {
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), [seedMessage]);
    const { pending } = deferredFetch("/chats/1/complete");

    const { result } = renderHook(() => useSendMessage(), {
      wrapper: createWrapper(qc),
    });

    await act(async () => {
      result.current.mutate({ chatId: 1, message: "first", modelId: "m" });
    });
    await act(async () => {
      result.current.mutate({ chatId: 1, message: "second", modelId: "m" });
    });

    await waitFor(() => {
      expect(messagesInCache(qc).filter((m) => m.id < 0)).toHaveLength(2);
      expect(pending.size).toBe(2);
    });
    const firstOptimisticId = messagesInCache(qc).find(
      (m) => m.content === "first",
    )!.id;
    const secondOptimisticId = messagesInCache(qc).find(
      (m) => m.content === "second",
    )!.id;

    // Second send commits first
    pending.get("second")!(jsonResponse(completionResponse(20, 21, "second")));
    await waitFor(() => {
      expect(messagesInCache(qc).some((m) => m.id === 21)).toBe(true);
    });

    // Now the first send fails - a full snapshot restore would wrongly
    // resurrect the pre-send state and erase the committed second send.
    pending.get("first")!(
      jsonResponse({ detail: "openrouter_completion_error" }, 502),
    );

    await waitFor(() => {
      expect(
        messagesInCache(qc).some((m) => m.id === firstOptimisticId),
      ).toBe(false);
    });
    const afterError = messagesInCache(qc);
    // Committed messages from the concurrent send are untouched
    expect(afterError.some((m) => m.id === 20)).toBe(true);
    expect(afterError.some((m) => m.id === 21)).toBe(true);
    expect(afterError.some((m) => m.id === secondOptimisticId)).toBe(false);
    expect(afterError.some((m) => m.id === seedMessage.id)).toBe(true);

    // Single error surface for send: no toast in the error store
    expect(useErrorStore.getState().errors).toHaveLength(0);
  });
});

describe("useRegenerateMessage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const chatMessages = [msg(1, "user", "prompt"), msg(2, "assistant", "old answer")];

  it("builds the body via buildRegeneratePayload and keeps the old message while pending", async () => {
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), chatMessages);
    const { fetchMock, pending, bodies } = deferredFetch("/regenerate");

    const { result } = renderHook(() => useRegenerateMessage(), {
      wrapper: createWrapper(qc),
    });

    await act(async () => {
      result.current.mutate({
        chatId: 1,
        messageId: 2,
        modelId: "openai/gpt-4o",
        generationParams: {
          temperature: 1.1,
          top_p: 0.5,
          max_tokens: 999999,
        },
        personaId: personaFixture.id,
        contextBudgetTokens: 999999,
        model: { ...modelFixture, supported_parameters: ["temperature", "max_tokens"] },
      });
    });

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) =>
          String(url).includes("/chats/1/messages/2/regenerate"),
        ),
      ).toBe(true);
    });

    // NO optimistic change: the old assistant message stays visible
    expect(messagesInCache(qc)).toEqual(chatMessages);

    // Body was assembled by buildRegeneratePayload: pruned, filtered, clamped
    const requestKey = [...bodies.keys()].find((k) => k.includes("/regenerate"))!;
    const body = bodies.get(requestKey) as Record<string, unknown>;
    expect(body).toEqual({
      model_id: "openai/gpt-4o",
      generation_params: {
        temperature: 1.1,
        max_tokens: 16384, // clamped to model max_completion_tokens
      },
      persona_id: personaFixture.id,
      context_budget_tokens: 128000, // clamped to model context_length
    });

    // Settle (variant contract): old assistant flips inactive in place, the
    // new active row is appended - sorted, no duplicates, nothing removed.
    pending.get(requestKey)!(
      jsonResponse({
        chat_id: 1,
        model_id: "openai/gpt-4o",
        user_message: msg(1, "user", "prompt"),
        assistant_message: {
          ...msg(3, "assistant", "new answer"),
          variant_group: 2,
          active: true,
          variant_index: 1,
          variant_count: 2,
        },
        deactivated_message_id: 2,
      }),
    );

    await waitFor(() => {
      expect(messagesInCache(qc).map((m) => m.id)).toEqual([1, 2, 3]);
    });
    const rows = messagesInCache(qc);
    expect(rows.find((m) => m.id === 2)!.active).toBe(false);
    expect(rows.find((m) => m.id === 3)!.active).toBe(true);
  });

  it("pushes a toast and leaves the cache intact on error", async () => {
    const qc = newQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), chatMessages);
    const { pending } = deferredFetch("/regenerate");

    const { result } = renderHook(() => useRegenerateMessage(), {
      wrapper: createWrapper(qc),
    });

    await act(async () => {
      result.current.mutate({
        chatId: 1,
        messageId: 2,
        modelId: "openai/gpt-4o",
      });
    });

    await waitFor(() => {
      expect(pending.size).toBe(1);
    });
    const requestKey = [...pending.keys()][0];
    pending.get(requestKey)!(
      jsonResponse({ detail: "not_last_assistant_message" }, 422),
    );

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    // Regenerate errors go to the toast stack (single surface for regenerate)
    expect(useErrorStore.getState().errors).toHaveLength(1);
    expect(useErrorStore.getState().errors[0].code).toBe(
      "not_last_assistant_message",
    );

    // Cache untouched - the old assistant message is still there
    expect(messagesInCache(qc)).toEqual(chatMessages);
  });
});
