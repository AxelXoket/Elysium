/**
 * ChatActions.test.ts - FE-5A: Chat/message action logic and cache behavior tests.
 *
 * Covers:
 *  - canRegenerateMessage (eligibility for latest assistant only)
 *  - removeMessageAndFollowingFromCache (cascade delete semantics)
 *  - Regenerate cache reconciliation (no duplicate user, old assistant replaced)
 *  - Error store integration for all destructive mutations
 *  - DeletedCountResponse parsing
 *  - Privacy: no provider fields, no browser storage
 */

import { describe, it, expect, vi } from "vitest";
import {
  canRegenerateMessage,
  removeMessageAndFollowingFromCache,
} from "@/lib/chat";
import type { Message } from "@/lib/schemas/chats";

// ── Fixtures ─────────────────────────────────────────────────────

function msg(id: number, role: "user" | "assistant", content = ""): Message {
  return {
    id,
    chat_id: 1,
    role,
    content: content || `${role} message ${id}`,
    created_at: "2026-01-01T00:00:00Z",
  };
}

const userMsg1 = msg(1, "user");
const assistantMsg2 = msg(2, "assistant");
const userMsg3 = msg(3, "user");
const assistantMsg4 = msg(4, "assistant");
const userMsg5 = msg(5, "user");

// Standard conversation: user→assistant→user→assistant→user
const conversation = [userMsg1, assistantMsg2, userMsg3, assistantMsg4, userMsg5];

// Conversation ending with assistant (regeneratable)
const endsWithAssistant = [userMsg1, assistantMsg2, userMsg3, assistantMsg4];

// ═════════════════════════════════════════════════════════════════
// canRegenerateMessage
// ═════════════════════════════════════════════════════════════════

describe("canRegenerateMessage", () => {
  it("returns true for the latest assistant message", () => {
    expect(canRegenerateMessage(endsWithAssistant, assistantMsg4)).toBe(true);
  });

  it("returns false for a user message (even if latest)", () => {
    expect(canRegenerateMessage(conversation, userMsg5)).toBe(false);
  });

  it("returns false for a non-latest assistant message", () => {
    expect(canRegenerateMessage(endsWithAssistant, assistantMsg2)).toBe(false);
  });

  it("returns false for empty message list", () => {
    expect(canRegenerateMessage([], assistantMsg4)).toBe(false);
  });

  it("returns false for null message list", () => {
    expect(canRegenerateMessage(null, assistantMsg4)).toBe(false);
  });

  it("returns false for undefined message list", () => {
    expect(canRegenerateMessage(undefined, assistantMsg4)).toBe(false);
  });

  it("returns false for null message", () => {
    expect(canRegenerateMessage(endsWithAssistant, null)).toBe(false);
  });

  it("returns false for undefined message", () => {
    expect(canRegenerateMessage(endsWithAssistant, undefined)).toBe(false);
  });

  it("returns false for a lone assistant greeting (no preceding user turn)", () => {
    // A first_mes greeting has no user turn before it - the backend rejects
    // regenerating it (no_preceding_user_message), so the affordance is
    // hidden too.
    const single = [msg(10, "assistant")];
    expect(canRegenerateMessage(single, single[0])).toBe(false);
  });

  it("returns true once a user turn precedes the last assistant group", () => {
    const list = [msg(1, "user"), msg(2, "assistant")];
    expect(canRegenerateMessage(list, list[1])).toBe(true);
  });

  it("returns false for a user message that is the only message", () => {
    const single = [msg(10, "user")];
    expect(canRegenerateMessage(single, single[0])).toBe(false);
  });

  it("returns false when message has same role but different id than latest", () => {
    // Assistant message that's not the last one by id
    const otherAssistant = msg(99, "assistant");
    expect(canRegenerateMessage(endsWithAssistant, otherAssistant)).toBe(false);
  });
});

// ═════════════════════════════════════════════════════════════════
// removeMessageAndFollowingFromCache
// ═════════════════════════════════════════════════════════════════

describe("removeMessageAndFollowingFromCache", () => {
  it("removes target message and all following messages", () => {
    // Delete from message 3 onward: keeps 1, 2
    const result = removeMessageAndFollowingFromCache(conversation, 3);
    expect(result).toHaveLength(2);
    expect(result.map((m) => m.id)).toEqual([1, 2]);
  });

  it("preserves all messages before target", () => {
    const result = removeMessageAndFollowingFromCache(conversation, 4);
    expect(result).toHaveLength(3);
    expect(result.map((m) => m.id)).toEqual([1, 2, 3]);
  });

  it("removes all messages when target is the first", () => {
    const result = removeMessageAndFollowingFromCache(conversation, 1);
    expect(result).toHaveLength(0);
  });

  it("removes only the last message when target is the last", () => {
    const result = removeMessageAndFollowingFromCache(conversation, 5);
    expect(result).toHaveLength(4);
    expect(result.map((m) => m.id)).toEqual([1, 2, 3, 4]);
  });

  it("returns empty array for empty input", () => {
    const result = removeMessageAndFollowingFromCache([], 1);
    expect(result).toHaveLength(0);
  });

  it("does not mutate the input array", () => {
    const original = [...conversation];
    const originalLength = original.length;
    removeMessageAndFollowingFromCache(original, 3);
    expect(original).toHaveLength(originalLength);
  });

  // "preserves all messages when target id does not exist (all ids < target)"
  // stood here and was character-for-character the same case as "keeps all when
  // target id is larger than all message ids" below: same call, same argument,
  // same assertion. Deleted in KADEME 17b.

  it("keeps nothing when target id is 0 (no message has id < 0)", () => {
    const result = removeMessageAndFollowingFromCache(conversation, 0);
    expect(result).toHaveLength(0);
  });

  it("keeps all when target id is larger than all message ids", () => {
    const result = removeMessageAndFollowingFromCache(conversation, 100);
    expect(result).toHaveLength(5);
  });
});

// Deleted in KADEME 17b: describe("Regenerate cache reconciliation"), five
// tests over a HAND-WRITTEN copy of production logic named
// simulateRegenerateOnSuccess, whose own comment called it "the exact logic
// from useRegenerateMessage onSuccess".
//
// It was not, and had not been for a while. The copy did
// existingMessages.filter(m => m.id !== targetMessageId), that is, it REMOVED
// the regenerated row. The real onSuccess in lib/query/completions.ts appends
// a VARIANT: it deactivates the previous sibling in place and dedupe-appends
// the new active row, deleting nothing, because old variants stay navigable.
// So those five tests proved a behaviour the app had abandoned, and no edit to
// production could ever turn them red.
//
// The real path is covered where it actually runs, against the real hook:
// ChatMessageControls.test.tsx "regenerate calls mutation and does not
// duplicate user message" and "regenerate streams into the target bubble,
// replacing its stored text".

// ═════════════════════════════════════════════════════════════════
// DeletedCountResponse schema
// ═════════════════════════════════════════════════════════════════

describe("DeletedCountResponse parsing", () => {
  it("parses valid response", async () => {
    const { DeletedCountResponseSchema } = await import("@/lib/schemas/chats");
    const result = DeletedCountResponseSchema.safeParse({
      ok: true,
      deleted_count: 3,
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.deleted_count).toBe(3);
    }
  });

  it("parses zero deleted_count", async () => {
    const { DeletedCountResponseSchema } = await import("@/lib/schemas/chats");
    const result = DeletedCountResponseSchema.safeParse({
      ok: true,
      deleted_count: 0,
    });
    expect(result.success).toBe(true);
  });

  it("rejects response without ok", async () => {
    const { DeletedCountResponseSchema } = await import("@/lib/schemas/chats");
    const result = DeletedCountResponseSchema.safeParse({
      deleted_count: 3,
    });
    expect(result.success).toBe(false);
  });

  it("rejects response without deleted_count", async () => {
    const { DeletedCountResponseSchema } = await import("@/lib/schemas/chats");
    const result = DeletedCountResponseSchema.safeParse({
      ok: true,
    });
    expect(result.success).toBe(false);
  });
});

// ═════════════════════════════════════════════════════════════════
// Error store integration (structural)
// ═════════════════════════════════════════════════════════════════

// ═════════════════════════════════════════════════════════════════
// Deleted in KADEME 17b: nine tests that asserted only
// `typeof mod.X === "function"`.
//
// Six of them ("useDeleteChat hook is exported and is a function" and its
// five siblings, under describe blocks named "Error store integration
// (structural)" and "Chat/message action hook exports") proved nothing an
// import statement does not already prove: if the export were gone this file
// would not compile. They named the error store and never touched it; a build
// whose mutations pushed nothing to it passed all six.
//
// The other three were worse, because they carried PRIVACY names they did
// not check. They are rewritten below rather than deleted, since the promises
// are real and now have real tests.
// ═════════════════════════════════════════════════════════════════

describe("the parsed error carries the sentence the reader will get", () => {
  it("maps each destructive-action failure to its own sentence", async () => {
    // `toBeTruthy()` on a message string is satisfied by the literal "error",
    // and by every code returning the SAME text. What matters is that these
    // three failures read differently, because they call for different moves:
    // the chat is gone, the row is not the last one, there is nothing above it.
    //
    // The version this replaced passed `{ detail }` with no `status`, which
    // `isApiError` rejects, so all four of its cases fell through to
    // `unknown_error` and were checked with `toBeTruthy()`. It never once
    // exercised the codes in its own names.
    const { parseApiError } = await import("@/lib/errors/parseApiError");
    const { getErrorMessage } = await import("@/lib/errors/errorMessages");

    const cases = [
      "chat_not_found",
      "not_last_assistant_message",
      "no_preceding_user_message",
    ];
    const seen = new Set<string>();
    for (const detail of cases) {
      const parsed = parseApiError({ status: 400, detail });
      expect(parsed.detail).toBe(detail);
      expect(parsed.message).toBe(getErrorMessage(detail));
      expect(parsed.message).not.toBe("Something went wrong. Please try again.");
      seen.add(parsed.message);
    }
    expect(seen.size, "two of these failures read identically").toBe(cases.length);

    // A thrown Error is not an API error and must not be dressed up as one.
    const network = parseApiError(new Error("network failure"));
    expect(network.message).not.toContain("network failure");
  });
});

// ═════════════════════════════════════════════════════════════════
// Privacy checks
// ═════════════════════════════════════════════════════════════════

describe("Chat action privacy checks", () => {
  // "chatActions module is pure - no browser storage imports" stood here and
  // imported the module to assert two functions existed, which says nothing
  // about storage. KADEME 17b first replaced it with a real deletion pin that
  // read the module's source and asserted the storage names were absent.
  //
  // That replacement was DELETED too, and the reason is worth keeping: the
  // repo already owns this promise, repo-wide and better. static-safety.test.ts
  // scans EVERY source file for the three browser storage sinks (S-09, S-13,
  // S-14), keeps an allowlist for the one store that is allowed to persist
  // (S-09b), and carries a file-count floor so a broken glob cannot pass by
  // matching nothing (S-12). A module-scoped copy of that is strictly weaker.
  //
  // It also proved the point by hand. The deleted version listed those sink
  // names as literal strings in its own array, so the repo's gate failed on
  // the test file itself, three times, until every spelling was gone from
  // this comment too. The gate works, and it does not read comments as
  // exempt, which is the correct choice for a rule about absence.

  it("deletes a message and everything after it without sending a body", async () => {
    // The name used to promise this and the body checked that the function
    // existed. A version that posted the whole transcript passed.
    const { deleteMessageAndFollowing } = await import("@/lib/api/chats");
    // Typed as `typeof fetch` so mock.calls carries fetch's own parameter
    // tuple. The cast that used to stand here claimed `[]` was
    // `[string, RequestInit]`, which typecheck rejects outright - and it was
    // the mock's missing signature that made a cast look necessary at all.
    const fetchSpy = vi.fn<typeof fetch>(
      async () =>
        new Response(JSON.stringify({ ok: true, deleted_count: 3 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchSpy);

    await deleteMessageAndFollowing(1, 42);

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toContain("/chats/1/messages/42");
    expect(init?.method).toBe("DELETE");
    expect(init?.body, "a destructive call carried a request body").toBeUndefined();
    vi.unstubAllGlobals();
  });

  it("clears a chat without sending a body", async () => {
    const { clearChat } = await import("@/lib/api/chats");
    const fetchSpy = vi.fn<typeof fetch>(
      async () =>
        new Response(JSON.stringify({ ok: true, deleted_count: 7 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchSpy);

    await clearChat(1);

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toContain("/chats/1/clear");
    expect(init?.method).toBe("POST");
    expect(init?.body, "a destructive call carried a request body").toBeUndefined();
    vi.unstubAllGlobals();
  });
});
