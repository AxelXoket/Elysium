/**
 * streamTail.ts - the row a finished stream hands its typewriter over to.
 *
 * THE PROBLEM. `done` writes the persisted assistant row into the query cache
 * and clears the streaming entry in the SAME batch. The transient streaming
 * bubble vanishes and the real row appears with its full text, so whatever the
 * typewriter had not shown yet arrives in one frame. Measured on an 800
 * character reply delivered as a single delta: 526 characters had been typed
 * when `done` landed, so 274 of them - 34 percent - appeared at once. That is
 * "type, then a wall of text", which is worse than the wall of text alone.
 *
 * THE HANDOVER. At the instant the entry clears, the row that replaces it
 * already holds a superset of the text the typewriter was working through, so
 * the two are continuous: the buffer is a PREFIX of the row's content. Feeding
 * the row's content to the pacing hook instead of the vanished buffer is a
 * plain prefix extension, which the hook already knows how to keep typing
 * through. Nothing about persistence, abort or voice moves.
 *
 * WHY THIS IS A PURE FUNCTION. Two guards live here and both are easy to get
 * wrong inside a component: the text must not be resurrected when the server
 * ROLLED BACK (an abort or an error leaves no matching row, so there is
 * nothing to adopt and the answer is null), and adoption must never pick a row
 * belonging to some other exchange. Keeping them in one pure function means
 * they can be tested without rendering anything.
 */
import type { Message } from "@/lib/schemas/chats";

export interface StreamTailRow {
  /** The persisted row that continues the typewriter. */
  id: number;
  /** Its full content - a prefix-extension of the buffer handed in. */
  text: string;
}

/**
 * The row a just-finished stream should keep typing into, or null.
 *
 * `buffer` is the text the streaming entry held at the moment it cleared.
 * `messages` is the message list as the client store holds it AT THAT INSTANT
 * - read it from the query client, not from a subscribed snapshot: `done`
 * writes the cache synchronously and clears the entry in the same batch, so
 * the store provably has the row while a subscribed observer only has it if
 * its notification happened to land in the same React commit.
 *
 * Returns null when nothing matches, which is the correct answer for every
 * failure: an aborted stream, an error rollback, or a `done` whose row has not
 * been written. Null means the row renders its own content immediately, so the
 * worst case of this whole mechanism is the behaviour we had before it.
 */
export function adoptTail(
  buffer: string,
  messages: readonly Message[] | undefined,
): StreamTailRow | null {
  if (buffer.length === 0 || !messages || messages.length === 0) return null;

  // Highest id wins: assistant rows are appended, so the newest one is the
  // reply that just finished. The `active` flag is deliberately NOT consulted.
  // Only the regenerate path touches active flags, and it deactivates the
  // SIBLING rather than setting the fresh row - depending on the server always
  // returning active=true on a new row would be an unstated invariant, and one
  // that fails silently by disabling the whole handover.
  let best: StreamTailRow | null = null;
  for (const m of messages) {
    if (m.role !== "assistant") continue;
    if (m.id <= 0) continue; // optimistic placeholder, not a persisted row
    // The prefix test is the identity check. A row whose content does not
    // start with what was on the wire belongs to a different exchange, or the
    // server rewrote the text, and continuing into it would show the reader
    // something they were never streamed.
    if (!m.content.startsWith(buffer)) continue;
    if (best == null || m.id > best.id) {
      best = { id: m.id, text: m.content };
    }
  }
  // Deliberately NOT rejected when the row equals the buffer. That is the
  // COMMON case - the row holds exactly what was streamed - and it was the
  // first version's bug: "nothing left to type" was decided against the
  // BUFFER, when the thing that is behind is the DISPLAY. A reply can arrive
  // complete in one delta and still be only half typed out. Whether there is
  // anything left to show is the caller's question, because only the caller
  // knows how much has been painted.
  return best;
}
