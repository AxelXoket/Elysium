/**
 * streamTail.test.ts - which row a finished stream may keep typing into.
 *
 * The guards here are the ones that are easy to get wrong inside a component
 * and expensive to get wrong in front of a reader: never resurrect text the
 * server rolled back, and never continue into a row from another exchange.
 */
import { describe, it, expect } from "vitest";
import { adoptTail } from "@/lib/chat/streamTail";
import type { Message } from "@/lib/schemas/chats";

function msg(
  id: number,
  role: "user" | "assistant",
  content: string,
  extra: Partial<Message> = {},
): Message {
  return {
    id,
    chat_id: 1,
    role,
    content,
    created_at: "2026-01-01T00:00:00Z",
    ...extra,
  } as Message;
}

describe("adoptTail", () => {
  it("adopts the row that continues the buffer", () => {
    const rows = [
      msg(1, "user", "question"),
      msg(2, "assistant", "the full reply, all of it"),
    ];
    expect(adoptTail("the full", rows)).toEqual({
      id: 2,
      text: "the full reply, all of it",
    });
  });

  it("refuses a row that does not continue the buffer", () => {
    // An error rollback or a rewrite: the row on screen is not the text that
    // was on the wire, and typing into it would show the reader something they
    // were never streamed.
    const rows = [msg(2, "assistant", "a completely different answer")];
    expect(adoptTail("the full", rows)).toBeNull();
  });

  it("refuses when there is no row at all", () => {
    // The abort path. Nothing was persisted, so there is nothing to continue
    // into, and the correct answer is to stop rather than to invent one.
    expect(adoptTail("half a reply", [])).toBeNull();
    expect(adoptTail("half a reply", undefined)).toBeNull();
  });

  it("ignores optimistic placeholder rows", () => {
    // Optimistic ids are negative. Adopting one would pin the typewriter to a
    // row that is about to be replaced.
    const rows = [msg(-5, "assistant", "half a reply and more")];
    expect(adoptTail("half a reply", rows)).toBeNull();
  });

  it("ignores user rows even when they would match", () => {
    const rows = [msg(9, "user", "half a reply and more")];
    expect(adoptTail("half a reply", rows)).toBeNull();
  });

  it("takes the newest matching assistant row", () => {
    const rows = [
      msg(2, "assistant", "prefix and an old reply"),
      msg(7, "assistant", "prefix and the new reply"),
    ];
    expect(adoptTail("prefix", rows)?.id).toBe(7);
  });

  it("does NOT consult the active flag", () => {
    // Only the regenerate path touches active flags, and it deactivates the
    // sibling rather than setting the fresh row. Requiring active=true would
    // be an unstated invariant that fails by silently disabling the hand-over,
    // which is invisible in every test that does not assert on it.
    const rows = [msg(4, "assistant", "prefix and the reply", { active: false })];
    expect(adoptTail("prefix", rows)?.id).toBe(4);
  });

  it("adopts a row that EQUALS the buffer", () => {
    // The common case, and the one the first version got wrong: it rejected
    // this as "nothing left to type". A reply can arrive complete in a single
    // delta and still be only half painted, so the row must be adopted and the
    // caller decides when the display has caught up.
    const rows = [msg(3, "assistant", "all of it")];
    expect(adoptTail("all of it", rows)).toEqual({ id: 3, text: "all of it" });
  });

  it("refuses an empty buffer", () => {
    const rows = [msg(3, "assistant", "a reply")];
    expect(adoptTail("", rows)).toBeNull();
  });
});
