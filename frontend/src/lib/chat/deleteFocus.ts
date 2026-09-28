/**
 * deleteFocus - where the keyboard goes when a delete empties the chat.
 *
 * Deleting a message deletes everything after it, so deleting the FIRST
 * message empties the chat: there is no earlier message to land on, and the
 * composer may be off. The keyboard then goes to the chat's empty state
 * ("No messages yet"), which is named by its heading and draws its ring
 * inside its own box. The empty state only exists once the delete has
 * succeeded, so the bubble asks for it here and the empty state takes it
 * when it mounts. A failed delete drops the ask (the bubble puts the
 * keyboard back on its Delete button). Never the sidebar, never an unnamed
 * `main`.
 */
let wanted: number | null = null;

/** The bubble asks: once chat `chatId` shows its empty state, focus it. */
export function wantEmptyStateFocus(chatId: number): void {
  wanted = chatId;
}

/** The empty state asks once, on mount: was it asked for? */
export function takeEmptyStateFocus(chatId: number): boolean {
  if (wanted !== chatId) return false;
  wanted = null;
  return true;
}

/** The delete failed: nothing is waiting any more. */
export function dropEmptyStateFocus(): void {
  wanted = null;
}
