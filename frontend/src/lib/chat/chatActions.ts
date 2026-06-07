/**
 * chatActions.ts — Chat/message action helpers for FE-5A.
 *
 * Provides:
 *  - canRegenerateMessage: determine if a message is eligible for regeneration
 *  - removeMessageAndFollowingFromCache: pure cache transform for delete+following
 *
 * These are pure functions — no side effects, no browser storage, no I/O.
 */

import type { Message } from "../schemas/chats";

/**
 * Determine whether a message can be regenerated.
 *
 * Rules (from frontend_contract.md):
 *  - Only the **latest** message in the list can be regenerated
 *  - It must have `role === "assistant"`
 *  - Returns `false` for user messages, non-latest assistant messages,
 *    empty lists, null/undefined inputs
 */
export function canRegenerateMessage(
  messages: readonly Message[] | null | undefined,
  message: Message | null | undefined,
): boolean {
  if (!messages || messages.length === 0) return false;
  if (!message) return false;
  if (message.role !== "assistant") return false;

  const lastMessage = messages[messages.length - 1];
  return lastMessage.id === message.id;
}

/**
 * Pure cache transform: remove a target message and all following messages.
 *
 * Used by `useDeleteMessageAndFollowing` onSuccess for immediate cache update.
 * Preserves all messages with `id < messageId`.
 *
 * Returns a new array — never mutates the input.
 */
export function removeMessageAndFollowingFromCache(
  messages: readonly Message[],
  messageId: number,
): Message[] {
  return messages.filter((m) => m.id < messageId);
}
