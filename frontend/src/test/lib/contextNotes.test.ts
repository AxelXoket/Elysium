/**
 * G-12: the `done` frame's context report must SURVIVE the client.
 *
 * This is the guard the plan asked for and that did not exist. The backend
 * computed `notebook_sent` / `notebook_total` / `history_trimmed` and put them
 * on the frame; the client's schema did not declare them; and zod's `z.object`
 * STRIPS unknown keys rather than rejecting them. So all three were shipped
 * and silently discarded, with nothing red anywhere - a field added on one
 * side and never seen on the other.
 *
 * The parse assertions below are the positive control for that: they fail if
 * anyone removes a field from the schema, which is the only way the strip can
 * come back.
 */
import { describe, it, expect, beforeEach } from "vitest";

import { StreamEventSchema } from "@/lib/api/stream";
import { useContextNotesStore } from "@/lib/chat/contextNotes";

const MESSAGE = {
  id: 1,
  chat_id: 7,
  role: "assistant",
  content: "hello",
  created_at: "2026-08-19T00:00:00Z",
  active: true,
};

function doneFrame(extra: Record<string, unknown> = {}) {
  return {
    type: "done",
    chat_id: 7,
    model_id: "vendor/model",
    user_message: { ...MESSAGE, id: 1, role: "user" },
    assistant_message: { ...MESSAGE, id: 2 },
    ...extra,
  };
}

describe("the done frame's context report", () => {
  it("survives the schema instead of being stripped", () => {
    const parsed = StreamEventSchema.parse(doneFrame({
      notebook_sent: 3, notebook_total: 9, history_trimmed: 4,
    }));
    expect(parsed).toMatchObject({
      notebook_sent: 3, notebook_total: 9, history_trimmed: 4,
    });
  });

  it("still parses a frame that carries none of them", () => {
    // Older frames, and every path that sends no notebook at all.
    const parsed = StreamEventSchema.parse(doneFrame());
    expect(parsed.type).toBe("done");
  });
});

describe("what the last turn carried", () => {
  beforeEach(() => useContextNotesStore.setState({ byChat: {} }));

  it("records the numbers the server computed", () => {
    useContextNotesStore.getState().record(7, {
      notebook_sent: 3, notebook_total: 9, history_trimmed: 4,
    });
    expect(useContextNotesStore.getState().byChat[7]).toEqual({
      notebook_sent: 3, notebook_total: 9, history_trimmed: 4,
    });
  });

  it("records a turn where the notebook was TRIMMED, not just sent whole", () => {
    // The case the number exists for. A client-side count of live notes would
    // say 9 here, and the user would believe nine notes were in force.
    useContextNotesStore.getState().record(7, {
      notebook_sent: 2, notebook_total: 9, history_trimmed: 0,
    });
    expect(useContextNotesStore.getState().byChat[7].notebook_sent).toBe(2);
  });

  it("treats an absent report as unknown, not as zero", () => {
    // "0 of 0 sent" reads as a working notebook that sent nothing, which is a
    // different and much worse claim than "no turn has run yet".
    useContextNotesStore.getState().record(7, {});
    expect(useContextNotesStore.getState().byChat[7]).toBeUndefined();
  });

  it("keeps each chat's own numbers", () => {
    useContextNotesStore.getState().record(7, { notebook_sent: 1, notebook_total: 2 });
    useContextNotesStore.getState().record(8, { notebook_sent: 5, notebook_total: 5 });
    expect(useContextNotesStore.getState().byChat[7].notebook_total).toBe(2);
    expect(useContextNotesStore.getState().byChat[8].notebook_total).toBe(5);
  });

  it("a later turn replaces the earlier one", () => {
    useContextNotesStore.getState().record(7, { notebook_sent: 9, notebook_total: 9 });
    useContextNotesStore.getState().record(7, { notebook_sent: 2, notebook_total: 9 });
    expect(useContextNotesStore.getState().byChat[7].notebook_sent).toBe(2);
  });
});
