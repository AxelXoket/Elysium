/**
 * chatActions.ts - Chat/message action helpers for FE-5A.
 *
 * Provides:
 *  - canRegenerateMessage: determine if a message is eligible for regeneration
 *  - removeMessageAndFollowingFromCache: pure cache transform for delete+following
 *
 * These are pure functions - no side effects, no browser storage, no I/O.
 */

import type { Message } from "../schemas/chats";

/** Group key of a message: its variant anchor (first sibling's id). */
export function messageAnchor(
  message: Pick<Message, "id" | "variant_group">,
): number {
  return message.variant_group ?? message.id;
}

/** Treat a missing `active` (optimistic rows, old fixtures) as active. */
export function isMessageActive(message: Pick<Message, "active">): boolean {
  return message.active !== false;
}

/**
 * Determine whether a message can be regenerated.
 *
 * Rules (variant-aware):
 *  - It must have `role === "assistant"`
 *  - Its variant GROUP must be the chat's last ACTIVE group. Comparing the
 *    raw last array entry would break once inactive variant siblings exist -
 *    the newest id may belong to a deactivated variant.
 *  - Returns `false` for user messages, earlier groups, empty lists,
 *    null/undefined inputs
 */
export function canRegenerateMessage(
  messages: readonly Message[] | null | undefined,
  message: Message | null | undefined,
): boolean {
  if (!messages || messages.length === 0) return false;
  if (!message) return false;
  if (message.role !== "assistant") return false;

  const anchor = messageAnchor(message);
  // A greeting (first_mes) has no preceding user turn - the backend rejects
  // regenerating it (no_preceding_user_message), so the affordance must not
  // show either.
  const hasPrecedingUser = messages.some(
    (m) => m.role === "user" && isMessageActive(m) && m.id < anchor && m.id > 0,
  );
  if (!hasPrecedingUser) return false;

  for (let i = messages.length - 1; i >= 0; i--) {
    const candidate = messages[i];
    if (!isMessageActive(candidate)) continue;
    return messageAnchor(candidate) === anchor;
  }
  return false;
}

/**
 * Pure cache transform: remove a target message and all following messages.
 *
 * Variant-aware: deleting any sibling removes its WHOLE group (the sweep
 * starts at the group anchor - the smallest id in the group - mirroring the
 * backend), plus everything after.
 *
 * Returns a new array - never mutates the input.
 */
export function removeMessageAndFollowingFromCache(
  messages: readonly Message[],
  messageId: number,
): Message[] {
  const target = messages.find((m) => m.id === messageId);
  const start = target ? messageAnchor(target) : messageId;
  return messages.filter((m) => m.id < start);
}
