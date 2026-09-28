/**
 * The message range the Notes tab reads again, picked from the chat itself.
 *
 * The range used to be two number fields that asked for database ids, and
 * no id is shown anywhere in the app: the field could not be filled in
 * without devtools, and "1" to "12" was refused or read a different stretch
 * than the one meant. Now the reader picks on the message: one button in
 * each bubble's action row, "Take notes from here", then (optionally) the
 * same button, now reading "Take notes to here", on another message.
 *
 * THE CYCLE is the one a date-range picker uses, so it needs no explaining:
 *   1. a "from" pick sets the start and fills the end with the chat's last
 *      message, so one press already names a range ("from here to the end");
 *   2. a "to" pick sets the end; if it lies ABOVE the start the two swap,
 *      and the strip says so in words (never a silent backwards range, and
 *      never an error the reader can only fix by clicking again);
 *   3. the next press starts over from step 1.
 *
 * WHAT IS STORED is a message's ANCHOR (its variant group's first id), not
 * a position and not the row on screen. Positions move when a message is
 * deleted, and the displayed row changes when another reply variant is
 * shown; the anchor is the one thing that stays put. Positions ("message 3
 * of 12") and the two ids the server takes are both derived from it, here,
 * from the chat's own message list.
 *
 * KEYED BY CHAT, so a pick in one chat can never reach another chat's strip.
 * Deliberately not persisted, like the other notebook stores: nothing about
 * the notebook goes to localStorage.
 */
import { create } from "zustand";

import type { Message } from "@/lib/schemas/chats";
import { isMessageActive, messageAnchor } from "./chatActions";

/** Every word this feature puts in the chat, in one place so the tests
 *  import them instead of retyping them. The button's name and its tooltip
 *  are the same string (label in name); the marks sit in the bubble's
 *  bottom row beside the time, as words with an icon, never colour alone. */
export const NOTE_RANGE_WORDS = {
  pickFrom: "Take notes from here",
  pickTo: "Take notes to here",
  markFrom: "Notes start here",
  markTo: "Notes end here",
  markOnly: "Notes start and end here",
} as const;

/** Which end the next press of the message button sets. */
export type RangeEnd = "from" | "to";

export interface NoteRange {
  /** Anchor of the first message, or null while none is chosen. */
  from: number | null;
  /** Anchor of the last message, or null while none is chosen. */
  to: number | null;
  /** What the message button offers next ("from here" / "to here"). */
  next: RangeEnd;
  /** The last "to" pick lay above the start and the two were swapped. */
  swapped: boolean;
  /** Set by every pick made FROM THE CHAT to a number no earlier pick in
   *  this process had, in any chat, so the strip can open and come into
   *  view for each one; typing in the strip does not change it. Global
   *  rather than per range, because a cleared range starting again from 1
   *  would repeat a number the strip has already answered. */
  seq: number;
  /** The last `seq` the strip has opened for. Kept HERE, not in the strip,
   *  because the strip unmounts when the reader changes tab: with the mark
   *  in its own state, coming back to the tab re-opened a pick already
   *  answered and forced the scope back to "range". */
  acked: number;
}

export const EMPTY_RANGE: NoteRange = {
  from: null, to: null, next: "from", swapped: false, seq: 0, acked: 0,
};

/** One message as the chat shows it: the group's anchor and its row on
 *  screen (the active variant). */
export interface ChatPosition {
  anchor: number;
  display: Message;
}

/** The chat's messages in the order the chat shows them, one per variant
 *  group - the same grouping MessageList renders, so "message 3" here is
 *  the third bubble there. Optimistic rows (negative ids) are left out:
 *  they are not on the server yet and cannot be read. */
export function chatPositions(messages: readonly Message[]): ChatPosition[] {
  const byAnchor = new Map<number, Message[]>();
  const order: number[] = [];
  for (const msg of messages) {
    if (msg.id <= 0) continue;
    const anchor = messageAnchor(msg);
    let rows = byAnchor.get(anchor);
    if (!rows) {
      rows = [];
      byAnchor.set(anchor, rows);
      order.push(anchor);
    }
    rows.push(msg);
  }
  return order.map((anchor) => {
    const rows = byAnchor.get(anchor)!;
    return { anchor, display: rows.find(isMessageActive) ?? rows[rows.length - 1] };
  });
}

/** 1-based position of an anchor in the chat, or null when it is gone. */
export function positionOf(
  positions: readonly ChatPosition[], anchor: number | null,
): number | null {
  if (anchor == null) return null;
  const i = positions.findIndex((p) => p.anchor === anchor);
  return i < 0 ? null : i + 1;
}

/** The two ids the server's range takes, for positions `a` to `b`.
 *
 *  The server counts ACTIVE rows whose id lies between the two, and refuses
 *  a start below the chat's lowest active id. So both ends are ACTIVE ids:
 *  the lowest and the highest active id among the chosen groups. Not the
 *  first group's anchor: after a re-rolled greeting the anchor is the
 *  inactive first variant, and the server refused "message 1" in every
 *  chat whose opening message had been re-rolled. Not the last group's
 *  anchor either: a regenerated reply is a newer row than the messages
 *  under it. */
export function serverRange(
  positions: readonly ChatPosition[], a: number, b: number,
): { from_message_id: number; to_message_id: number } | null {
  if (a < 1 || b < a || b > positions.length) return null;
  const ids = positions.slice(a - 1, b).map((p) => p.display.id);
  return {
    from_message_id: Math.min(...ids),
    to_message_id: Math.max(...ids),
  };
}

/** How long an excerpt may be, the ellipsis included. */
export const EXCERPT_MAX = 60;

/** A message's first words on one line, at most EXCERPT_MAX characters. */
export function excerpt(text: string): string {
  const flat = text.replace(/\s+/g, " ").trim();
  if (flat.length <= EXCERPT_MAX) return flat;
  return `${flat.slice(0, EXCERPT_MAX - 3).trimEnd()}...`;
}

/** One press of the message button, as a pure step of the cycle above.
 *
 *  `at` is the pressed message's position and `fromAt` / `toAt` the current
 *  ends' (null when there is none); positions, not anchors, decide "above".
 *
 *  WITH EXACTLY ONE END SET, a press fills the MISSING end (turned round if
 *  it lands on the wrong side) and never restarts the range: the strip's
 *  hint then says "Pick the last message with the book icon", and the book
 *  must do exactly that. `toAt` undefined means "not asked": the end counts
 *  as set when `range.to` is. */
export function applyPick(
  range: NoteRange,
  pressed: { anchor: number; at: number },
  last: { anchor: number },
  fromAt: number | null,
  seq: number,
  toAt?: number | null,
): NoteRange {
  const acked = range.acked;
  const hasFrom = range.from != null && fromAt != null;
  const hasTo = range.to != null && toAt !== null;
  if (hasFrom && !hasTo) {
    return pressed.at < fromAt!
      ? { from: pressed.anchor, to: range.from, next: "from", swapped: true, seq, acked }
      : { from: range.from, to: pressed.anchor, next: "from", swapped: false, seq, acked };
  }
  if (!hasFrom && hasTo && toAt != null) {
    return pressed.at > toAt
      ? { from: range.to, to: pressed.anchor, next: "from", swapped: true, seq, acked }
      : { from: pressed.anchor, to: range.to, next: "from", swapped: false, seq, acked };
  }
  if (range.next === "from" || range.from == null || fromAt == null) {
    return { from: pressed.anchor, to: last.anchor, next: "to", swapped: false, seq, acked };
  }
  if (pressed.at < fromAt) {
    return { from: pressed.anchor, to: range.from, next: "from", swapped: true, seq, acked };
  }
  return { ...range, to: pressed.anchor, next: "from", swapped: false, seq };
}

interface NoteRangeState {
  byChat: Record<number, NoteRange>;
  /** The last `seq` handed out. */
  picks: number;
  /** A press of the message button in `chatId`. */
  pick: (chatId: number, messages: readonly Message[], anchor: number) => void;
  /** Typed in the strip: sets one end, changes nothing else. */
  setEnd: (chatId: number, end: RangeEnd, anchor: number | null) => void;
  /** The strip has opened for pick `seq`; it does not open for it again. */
  ack: (chatId: number, seq: number) => void;
  /** Cancel or a started run. A chat change does NOT clear: the range is
   *  keyed by chat, so a half-picked range waits for the reader to return. */
  clear: (chatId: number) => void;
}

export const useNoteRangeStore = create<NoteRangeState>((set) => ({
  byChat: {},
  picks: 0,
  pick: (chatId, messages, anchor) =>
    set((s) => {
      const positions = chatPositions(messages);
      const at = positionOf(positions, anchor);
      if (at == null || positions.length === 0) return s;
      const range = s.byChat[chatId] ?? EMPTY_RANGE;
      const seq = s.picks + 1;
      const next = applyPick(
        range,
        { anchor, at },
        { anchor: positions[positions.length - 1].anchor },
        positionOf(positions, range.from),
        seq,
        positionOf(positions, range.to),
      );
      return { picks: seq, byChat: { ...s.byChat, [chatId]: next } };
    }),
  setEnd: (chatId, end, anchor) =>
    set((s) => {
      const range = s.byChat[chatId] ?? EMPTY_RANGE;
      return {
        byChat: {
          ...s.byChat,
          // A cleared end is the one the book sets next, so the message
          // button's name ("from here" / "to here") matches the hint.
          [chatId]: { ...range, [end]: anchor, swapped: false,
            ...(anchor == null ? { next: end } : {}) },
        },
      };
    }),
  ack: (chatId, seq) =>
    set((s) => {
      const range = s.byChat[chatId];
      if (!range || range.acked === seq) return s;
      return { byChat: { ...s.byChat, [chatId]: { ...range, acked: seq } } };
    }),
  clear: (chatId) =>
    set((s) => {
      if (!(chatId in s.byChat)) return s;
      const rest = { ...s.byChat };
      delete rest[chatId];
      return { byChat: rest };
    }),
}));
