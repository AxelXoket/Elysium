/**
 * ActivateVariant.test.tsx - hook-level behavior of useActivateVariant.
 *
 * Covers:
 *  - optimistic GROUP-WIDE flag flip on mutate (one active row per group)
 *  - onSuccess re-applies the flip group-wide (a single-row patch could
 *    resurrect a stale active flag beside a newer optimistic flip)
 *  - onSuccess is SKIPPED while another activate is still pending - the
 *    last mutation settles the final state (arrow-mash race)
 *  - onError rolls back ONLY the mutation's group (targeted, no snapshot
 *    restore) and pushes a toast
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { waitFor, act } from "@testing-library/react";
import type { QueryClient } from "@tanstack/react-query";
import {
  createTestQueryClient,
  renderHookWithQueryClient,
} from "@/test/helpers/renderWithQueryClient";
import { useActivateVariant } from "@/lib/query/chats";
import { keys } from "@/lib/query/keys";
import { useErrorStore } from "@/lib/errors";
import type { Message } from "@/lib/schemas/chats";

function variantMsg(
  id: number,
  group: number | null,
  active: boolean,
): Message {
  return {
    id,
    chat_id: 1,
    role: "assistant",
    content: `variant ${id}`,
    created_at: "2026-01-01T00:00:00Z",
    variant_group: group,
    active,
  };
}

/** Group of three variants anchored at 10; row 12 currently active. */
function seedGroup(): Message[] {
  return [
    { ...variantMsg(1, null, true), role: "user", content: "prompt" },
    variantMsg(10, 10, false),
    variantMsg(11, 10, false),
    variantMsg(12, 10, true),
  ];
}

function activateResponse(id: number, deactivated: number | null) {
  return {
    ok: true,
    chat_id: 1,
    variant_group: 10,
    message: {
      ...variantMsg(id, 10, true),
      variant_index: id - 10,
      variant_count: 3,
    },
    deactivated_message_id: deactivated,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * The fields only the SERVER knows, read back off the row.
 *
 * The optimistic flip sets `active` itself, so active ids alone cannot tell
 * a settled response from an unsettled one - which is exactly how the
 * `isMutating(...) > 1` guard above onSuccess could be loosened to `>= 1`,
 * skipping every settle, with the whole frontend suite still green.
 */
function settled(qc: QueryClient, id: number) {
  const row = (qc.getQueryData<Message[]>(keys.messages(1)) ?? []).find(
    (m) => m.id === id,
  );
  return {
    variant_index: row?.variant_index,
    variant_count: row?.variant_count,
  };
}

function activeIds(qc: QueryClient): number[] {
  const rows = qc.getQueryData<Message[]>(keys.messages(1)) ?? [];
  return rows
    .filter((m) => m.role === "assistant" && m.active !== false)
    .map((m) => m.id);
}

describe("useActivateVariant", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useErrorStore.getState().clearAll();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("flips the group optimistically and settles from the response", async () => {
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), seedGroup());
    let release!: (res: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () => new Promise<Response>((resolve) => (release = resolve)),
      ),
    );

    const { result } = renderHookWithQueryClient(() => useActivateVariant(), {
      client: qc,
    });

    act(() => {
      result.current.mutate({ chatId: 1, messageId: 10 });
    });

    // Optimistic: exactly one active row in the group, the pressed one.
    await waitFor(() => {
      expect(activeIds(qc)).toEqual([10]);
    });

    release(jsonResponse(activateResponse(10, 12)));
    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    expect(activeIds(qc)).toEqual([10]);
    expect(settled(qc, 10), "the response never reached the cache").toEqual({
      variant_index: 0,
      variant_count: 3,
    });
    expect(useErrorStore.getState().errors).toHaveLength(0);
  });

  it("skips a stale onSuccess while a newer activate is pending", async () => {
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), seedGroup());
    const pending: Array<(res: Response) => void> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () => new Promise<Response>((resolve) => pending.push(resolve)),
      ),
    );

    const { result } = renderHookWithQueryClient(() => useActivateVariant(), {
      client: qc,
    });

    // Arrow-mash: activate(11), then activate(10) before the first settles.
    act(() => {
      result.current.mutate({ chatId: 1, messageId: 11 });
    });
    act(() => {
      result.current.mutate({ chatId: 1, messageId: 10 });
    });
    await waitFor(() => {
      expect(activeIds(qc)).toEqual([10]); // newest optimistic flip wins
    });

    // First (older) response lands while the second is still pending - its
    // onSuccess must NOT resurrect row 11.
    pending[0](jsonResponse(activateResponse(11, 12)));
    await waitFor(() => {
      expect(
        (fetch as ReturnType<typeof vi.fn>).mock.calls.length,
      ).toBe(2);
    });
    expect(activeIds(qc)).toEqual([10]);

    // Second response settles the final state - and settling means the
    // server's own fields arrive, not just the flag the optimistic flip
    // already set. Without this the guard could skip EVERY settle and every
    // assertion here would still pass.
    pending[1](jsonResponse(activateResponse(10, 11)));
    await waitFor(() => {
      expect(settled(qc, 10)).toEqual({ variant_index: 0, variant_count: 3 });
    });
    expect(activeIds(qc)).toEqual([10]);
  });

  it("rolls back ONLY its group on error and pushes a toast", async () => {
    const qc = createTestQueryClient();
    qc.setQueryData<Message[]>(keys.messages(1), seedGroup());
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ detail: "variant_group_not_last" }, 409),
      ),
    );

    const { result } = renderHookWithQueryClient(() => useActivateVariant(), {
      client: qc,
    });

    act(() => {
      result.current.mutate({ chatId: 1, messageId: 10 });
    });

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
    // Rolled back to the previously-active sibling.
    expect(activeIds(qc)).toEqual([12]);
    expect(useErrorStore.getState().errors).toHaveLength(1);
  });
});
