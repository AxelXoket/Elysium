/**
 * Suggestions the reader discarded whose delete is HELD, per chat.
 *
 * A Discard does not send the delete at once: the row says "Suggestion
 * discarded." with an Undo, and Undo can only put a suggestion back while
 * it still exists. The Notes panel sends the held deletes when it leaves
 * the chat, unmounts, or the page goes away. Kept in a store rather than in
 * the panel because the run's "Undo this run" (BackfillStrip) must send them
 * first too: otherwise its answer counted a note the reader had already
 * discarded among the ones it removed.
 *
 * Deliberately not persisted, like the other notebook stores.
 */
import { create } from "zustand";

interface HeldDiscardsState {
  byChat: Record<number, number[]>;
  hold: (chatId: number, id: number) => void;
  release: (chatId: number, id: number) => void;
  /** Hands back every held id of the chat and forgets them. */
  take: (chatId: number) => number[];
}

export const useHeldDiscards = create<HeldDiscardsState>((set, get) => ({
  byChat: {},
  hold: (chatId, id) =>
    set((s) => {
      const ids = s.byChat[chatId] ?? [];
      return ids.includes(id) ? s : { byChat: { ...s.byChat, [chatId]: [...ids, id] } };
    }),
  release: (chatId, id) =>
    set((s) => ({
      byChat: { ...s.byChat, [chatId]: (s.byChat[chatId] ?? []).filter((x) => x !== id) },
    })),
  take: (chatId) => {
    const ids = get().byChat[chatId] ?? [];
    if (ids.length) {
      set((s) => {
        const rest = { ...s.byChat };
        delete rest[chatId];
        return { byChat: rest };
      });
    }
    return ids;
  },
}));
