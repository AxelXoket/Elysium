/**
 * Which model-written notes the user has already been shown.
 *
 * A35 asks for "Saved: … + Undo", and the reason is the one thing this
 * feature cannot do without it: with auto-accept ON there is no review step,
 * so a note the model wrote appears in the prompt having never been seen by
 * anybody. Every shipped version of this that surprised its users surprised
 * them exactly there - the write is invisible, and the surprise arrives later,
 * in an answer that used a fact nobody remembers approving.
 *
 * So the panel announces them once, offers to take them back, and remembers
 * that it announced them.
 *
 * Deliberately NOT persisted. `localStorage` may not hold anything about the
 * notebook - not the content, not the counters, not which ids exist. An
 * acknowledgement that survives a restart is worth less than that rule.
 */
import { create } from "zustand";

interface SeenNotesState {
  /** chatId -> ids the user has seen announced */
  byChat: Record<number, number[]>;
  markSeen: (chatId: number, ids: number[]) => void;
}

export const useSeenNotesStore = create<SeenNotesState>((set) => ({
  byChat: {},
  markSeen: (chatId, ids) =>
    set((s) => ({
      byChat: {
        ...s.byChat,
        [chatId]: Array.from(new Set([...(s.byChat[chatId] ?? []), ...ids])),
      },
    })),
}));
