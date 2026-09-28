/**
 * The notes range, picked from the chat: the pure steps under the button.
 *
 * Positions are what the reader sees ("message 3 of 12"); ids are what the
 * server keys on. Every case here checks one against the other on a fixture
 * whose ids are NOT its positions, so an id leaking into a position (or the
 * reverse) reads as a wrong number instead of a coincidence.
 */
import { describe, expect, it } from "vitest";

import {
  EMPTY_RANGE,
  EXCERPT_MAX,
  applyPick,
  chatPositions,
  excerpt,
  positionOf,
  serverRange,
  useNoteRangeStore,
} from "@/lib/chat/noteRange";
import type { Message } from "@/lib/schemas/chats";

function msg(id: number, over: Partial<Message> = {}): Message {
  return {
    id, chat_id: 7, role: id % 2 ? "user" : "assistant",
    content: `row ${id}`, created_at: "2026-09-27 10:00:00",
    attachments: [], variant_group: null, active: true,
    variant_index: 0, variant_count: 1, ...over,
  } as Message;
}

/** Twelve messages, ids 103, 106, ... 136. */
const TWELVE = Array.from({ length: 12 }, (_, i) => msg(103 + i * 3));

describe("chatPositions", () => {
  it("counts the chat the way the chat draws it, one place per bubble", () => {
    const places = chatPositions(TWELVE);
    expect(places).toHaveLength(12);
    expect(places.map((p) => p.anchor)[0]).toBe(103);
    expect(positionOf(places, 136)).toBe(12);
    // GROUND: an id that is not in the chat has no place.
    expect(positionOf(places, 12)).toBeNull();
    expect(positionOf(places, null)).toBeNull();
  });

  it("gives a regenerated reply ONE place, at its first row, shown as the active one", () => {
    // Message 2 (anchor 106) was regenerated: its new row has the newest id
    // in the chat and must neither become a thirteenth place nor move.
    const regen = [
      ...TWELVE.slice(0, 1),
      msg(106, { variant_group: 106, active: false }),
      ...TWELVE.slice(2),
      msg(200, { variant_group: 106, active: true, variant_index: 1 }),
    ];
    const places = chatPositions(regen);
    expect(places).toHaveLength(12);
    expect(places[1].anchor).toBe(106);
    expect(places[1].display.id).toBe(200);
    // POSITIVE CONTROL: without the group the new row IS a place of its own.
    expect(chatPositions([...TWELVE, msg(200)])).toHaveLength(13);
  });

  it("leaves out a row the server has not stored yet", () => {
    expect(chatPositions([...TWELVE, msg(-1)])).toHaveLength(12);
  });
});

describe("serverRange", () => {
  it("sends places 1 to 12 of a 12-message chat as the first and last ids", () => {
    const places = chatPositions(TWELVE);
    expect(serverRange(places, 1, 12))
      .toEqual({ from_message_id: 103, to_message_id: 136 });
    expect(serverRange(places, 3, 7))
      .toEqual({ from_message_id: 109, to_message_id: 121 });
    expect(serverRange(places, 5, 5))
      .toEqual({ from_message_id: 115, to_message_id: 115 });
  });

  it("refuses a backwards range or a place past the end, rather than sending it", () => {
    const places = chatPositions(TWELVE);
    expect(serverRange(places, 7, 3)).toBeNull();
    expect(serverRange(places, 1, 13)).toBeNull();
    expect(serverRange(places, 0, 3)).toBeNull();
  });

  it("reaches a regenerated reply inside the range, whose id is newer than the end", () => {
    const regen = [
      ...TWELVE.slice(0, 1),
      msg(106, { variant_group: 106, active: false }),
      ...TWELVE.slice(2),
      msg(200, { variant_group: 106, active: true, variant_index: 1 }),
    ];
    const r = serverRange(chatPositions(regen), 1, 3)!;
    expect(r.from_message_id).toBe(103);
    // The active reply at place 2 is id 200; ending at place 3's id (109)
    // would leave it out of the server's BETWEEN.
    expect(r.to_message_id).toBe(200);
  });

  it("starts at the ACTIVE row when the first message was re-rolled", () => {
    // A re-rolled greeting leaves its first id inactive, and the
    // server refuses a start below the chat's lowest active id. Message 1
    // must send the active variant's id.
    const rerolled = [
      msg(1, { variant_group: 1, active: false, role: "assistant" }),
      msg(2, { variant_group: 1, active: true, variant_index: 1, role: "assistant" }),
      msg(3, { role: "user" }),
      msg(4, { role: "assistant" }),
    ];
    const places = chatPositions(rerolled);
    // GROUND: one group for the greeting, its anchor the inactive row.
    expect(places.map((p) => p.anchor)).toEqual([1, 3, 4]);
    expect(serverRange(places, 1, 3)).toEqual({ from_message_id: 2, to_message_id: 4 });
    expect(serverRange(places, 1, 1)).toEqual({ from_message_id: 2, to_message_id: 2 });
    // POSITIVE CONTROL: without a re-roll the start is the plain first id.
    expect(serverRange(chatPositions(rerolled.slice(1).map((m) =>
      ({ ...m, variant_group: null }))), 1, 3))
      .toEqual({ from_message_id: 2, to_message_id: 4 });
  });
});

describe("excerpt", () => {
  it("keeps a short message whole and cuts a long one to the limit", () => {
    expect(excerpt("  Short   one\nline ")).toBe("Short one line");
    const long = "word ".repeat(40);
    const cut = excerpt(long);
    expect(cut.length).toBeLessThanOrEqual(EXCERPT_MAX);
    expect(cut.endsWith("...")).toBe(true);
    // GROUND: the limit is not so small that it always cuts.
    expect(excerpt("x".repeat(EXCERPT_MAX))).toBe("x".repeat(EXCERPT_MAX));
  });
});

describe("applyPick, the press cycle", () => {
  const last = { anchor: 136 };

  it("a first press sets the start and fills the end with the last message", () => {
    const r = applyPick(EMPTY_RANGE, { anchor: 109, at: 3 }, last, null, 1);
    expect(r).toMatchObject({ from: 109, to: 136, next: "to", swapped: false, seq: 1 });
  });

  it("a second press below the start sets the end", () => {
    const one = applyPick(EMPTY_RANGE, { anchor: 109, at: 3 }, last, null, 1);
    const two = applyPick(one, { anchor: 121, at: 7 }, last, 3, 2);
    expect(two).toMatchObject({ from: 109, to: 121, next: "from", swapped: false });
  });

  it("a second press ABOVE the start swaps the two, and says it did", () => {
    const one = applyPick(EMPTY_RANGE, { anchor: 124, at: 8 }, last, null, 1);
    const two = applyPick(one, { anchor: 115, at: 5 }, last, 8, 2);
    expect(two).toMatchObject({ from: 115, to: 124, swapped: true, next: "from" });
  });

  it("the same message twice is a range of one", () => {
    const one = applyPick(EMPTY_RANGE, { anchor: 115, at: 5 }, last, null, 1);
    expect(applyPick(one, { anchor: 115, at: 5 }, last, 5, 2))
      .toMatchObject({ from: 115, to: 115, swapped: false });
  });

  it("a third press starts over", () => {
    const one = applyPick(EMPTY_RANGE, { anchor: 109, at: 3 }, last, null, 1);
    const two = applyPick(one, { anchor: 121, at: 7 }, last, 3, 2);
    const three = applyPick(two, { anchor: 127, at: 9 }, last, 3, 3);
    expect(three).toMatchObject({ from: 127, to: 136, next: "to", swapped: false });
  });
});

describe("the store", () => {
  it("keeps each chat's range to itself", () => {
    const { pick } = useNoteRangeStore.getState();
    pick(7, TWELVE, 109);
    const eight = [msg(900, { chat_id: 8 }), msg(903, { chat_id: 8 })];
    // GROUND: chat 8 has nothing until something is picked there.
    expect(useNoteRangeStore.getState().byChat[8]).toBeUndefined();
    pick(8, eight, 903);
    const s = useNoteRangeStore.getState();
    expect(s.byChat[7]).toMatchObject({ from: 109, to: 136 });
    expect(s.byChat[8]).toMatchObject({ from: 903, to: 903 });
    // Every pick gets a number no other pick had, across chats.
    expect(s.byChat[8].seq).toBeGreaterThan(s.byChat[7].seq);
    s.clear(7);
    expect(useNoteRangeStore.getState().byChat[7]).toBeUndefined();
    expect(useNoteRangeStore.getState().byChat[8]).toBeDefined();
  });

  it("ignores a press on a message that is not in the list it was given", () => {
    useNoteRangeStore.getState().pick(7, TWELVE, 5);
    expect(useNoteRangeStore.getState().byChat[7]).toBeUndefined();
  });
});
