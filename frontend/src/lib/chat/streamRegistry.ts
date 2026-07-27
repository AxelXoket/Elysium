/**
 * streamRegistry - module-level registry of in-flight completion streams.
 *
 * useStreamingCompletion's controller map is hook-local: ChatList (a
 * different component tree) cannot reach it, so Clear/Delete used to leave
 * the stream running - tokens kept burning and `done` wrote the reply back
 * into the just-emptied cache, "resurrecting" the chat. (v1.1 FF1 + H7.)
 *
 * A tiny zustand store gives both worlds: mutations call stopChat(id)
 * imperatively at click time, and menus subscribe reactively to disable
 * Clear/Delete while that chat streams.
 */

import { create } from "zustand";

interface StreamRegistryState {
  /** chatId -> AbortController of that chat's in-flight stream. */
  controllers: ReadonlyMap<number, AbortController>;
  /**
   * Chats whose in-flight stream has not produced an assistant delta yet.
   *
   * This is the window decision S16 is about: the user has sent, the reply
   * has not started arriving, and turning "speak replies aloud" on should
   * apply to the reply that is about to come - not to the next one. Once a
   * delta has landed the window closes, and that is S15: switching on
   * mid-reply must not go back and read what was already on screen.
   *
   * The two rules only exist as a pair, and nothing in the code could
   * distinguish the two states, so S16 was unreachable and S15 got the
   * credit for both.
   */
  awaitingFirstDelta: ReadonlySet<number>;
}

export const useStreamRegistry = create<StreamRegistryState>(() => ({
  controllers: new Map(),
  awaitingFirstDelta: new Set(),
}));

export function registerStream(chatId: number, controller: AbortController): void {
  useStreamRegistry.setState((s) => {
    const next = new Map(s.controllers);
    next.set(chatId, controller);
    const waiting = new Set(s.awaitingFirstDelta);
    waiting.add(chatId);
    return { controllers: next, awaitingFirstDelta: waiting };
  });
}

/** Identity-checked: a stale finally-block must not evict a newer stream. */
export function unregisterStream(chatId: number, controller: AbortController): void {
  useStreamRegistry.setState((s) => {
    if (s.controllers.get(chatId) !== controller) return s;
    const next = new Map(s.controllers);
    next.delete(chatId);
    const waiting = new Set(s.awaitingFirstDelta);
    waiting.delete(chatId);
    return { controllers: next, awaitingFirstDelta: waiting };
  });
}

/** The reply has started arriving: the S16 window is closed for this chat. */
export function noteFirstDelta(chatId: number): void {
  useStreamRegistry.setState((s) => {
    if (!s.awaitingFirstDelta.has(chatId)) return s;
    const waiting = new Set(s.awaitingFirstDelta);
    waiting.delete(chatId);
    return { awaitingFirstDelta: waiting };
  });
}

/** True while a stream is in flight for this chat and has shown nothing yet. */
export function isAwaitingFirstDelta(chatId: number): boolean {
  return useStreamRegistry.getState().awaitingFirstDelta.has(chatId);
}

/** Abort the chat's in-flight stream, if any (safe no-op otherwise). */
export function stopChat(chatId: number): void {
  useStreamRegistry.getState().controllers.get(chatId)?.abort();
}
