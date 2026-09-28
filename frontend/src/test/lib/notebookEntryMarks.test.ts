/**
 * The two exact marks on a note, `edited_at` and `hand_accepted_at`, as the
 * wire schema hands them to the panel.
 *
 * zod drops keys a schema does not name, so a schema that lost either field
 * would parse every row without complaint and the panel would quietly read
 * `undefined`: "Model wrote it, you edited it." would never show, and a note
 * the reader kept by hand would be announced again. These rows make that
 * loss visible.
 */
import { describe, expect, it } from "vitest";

import { BackfillUndoSchema, NotebookEntrySchema } from "@/lib/schemas/notebook";

const ROW = {
  id: 1, chat_id: 7, position: 0, kind: "fact", text: "Mira keeps the mill.",
  evidence: null, durability: "permanent", importance: 2, pinned: 0,
  retired_at: null, superseded_by: null, excluded_reason: null,
  status: "accepted", provenance: "model", source_message_id: null,
  created_at: "2026-09-27 10:00:00", updated_at: "2026-09-27 11:00:00",
};

describe("the two marks on the wire", () => {
  it("both stamps survive the parse", () => {
    const parsed = NotebookEntrySchema.parse({
      ...ROW, edited_at: "2026-09-27 11:00:00", hand_accepted_at: "2026-09-27 10:30:00" });
    expect(parsed.edited_at).toBe("2026-09-27 11:00:00");
    expect(parsed.hand_accepted_at).toBe("2026-09-27 10:30:00");
  });

  it("null survives as null, not as a missing key", () => {
    const parsed = NotebookEntrySchema.parse({ ...ROW, edited_at: null, hand_accepted_at: null });
    expect(parsed).toHaveProperty("edited_at", null);
    expect(parsed).toHaveProperty("hand_accepted_at", null);
  });

  it("GROUND: a row from a server without the marks still parses", () => {
    const parsed = NotebookEntrySchema.parse(ROW);
    expect(parsed.edited_at ?? null).toBeNull();
    expect(parsed.hand_accepted_at ?? null).toBeNull();
    // Positive control for the parse itself: a neighbouring field survives.
    expect(parsed.updated_at).toBe(ROW.updated_at);
  });
});

describe("the undo answer on the wire", () => {
  const BODY = { job_id: 17, removed: 1, kept_pinned: 1, kept_edited: 1 };

  it("carries all four kept counts", () => {
    const parsed = BackfillUndoSchema.parse({ ...BODY, kept_accepted: 2, kept_older: 3 });
    expect(parsed.kept_accepted).toBe(2);
    expect(parsed.kept_older).toBe(3);
  });

  it("refuses an answer without the two new counts", () => {
    // A server that still sends the old shape must not be read as "kept 0
    // you kept": the sentence would then be false.
    expect(() => BackfillUndoSchema.parse(BODY)).toThrow();
  });
});
