/**
 * What the last turn actually carried, per chat.
 *
 * The backend computes this while assembling the payload - how many notes fit
 * inside the notebook's share of the context, and how many older messages were
 * dropped to make room - and ships it on the `done` frame. Before this existed
 * the panel counted live notes on the client and called that "sent", which is
 * a guess: it is right only while nothing is trimmed, and the whole reason the
 * number is on screen is the case where something is.
 *
 * `history_trimmed` rides along because it is the older debt. History has
 * always been trimmed oldest-first with nothing recording it; announcing the
 * notebook alone would teach the reader that dropped context gets announced,
 * which would then be false for the larger case.
 */
import { create } from "zustand";

export interface ContextNotes {
  notebook_sent: number;
  notebook_total: number;
  history_trimmed: number;
}

interface ContextNotesState {
  byChat: Record<number, ContextNotes>;
  record: (chatId: number, notes: Partial<ContextNotes>) => void;
}

export const useContextNotesStore = create<ContextNotesState>((set) => ({
  byChat: {},
  record: (chatId, notes) =>
    set((s) => {
      // An older server, or a frame that carried none, must not be recorded as
      // "zero notes were sent" - that reads as a working notebook that sent
      // nothing. Absent means unknown, and unknown falls back to the count.
      if (notes.notebook_total === undefined) return s;
      return {
        byChat: {
          ...s.byChat,
          [chatId]: {
            notebook_sent: notes.notebook_sent ?? 0,
            notebook_total: notes.notebook_total,
            history_trimmed: notes.history_trimmed ?? 0,
          },
        },
      };
    }),
}));
