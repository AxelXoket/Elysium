/**
 * A held Discard asks the server to delete the note ONLY while it is still
 * a suggestion. The server checks again in the same transaction
 * (backend tests/test_notebook_held_discard.py); this holds the client to
 * asking for that check, and to reading the "kept" answer.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";

import { sendHeldDiscards } from "@/lib/query/notebook";
import { keys } from "@/lib/query/keys";
import { DiscardOkSchema } from "@/lib/schemas/notebook";
import { mockFetch } from "../mocks/api";

describe("sending a held discard", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("asks for the suggestion-only delete", async () => {
    const fetchMock = mockFetch({ "DELETE /notebook/entries/5": { body: { ok: true } } });
    const qc = new QueryClient();
    qc.setQueryData(keys.notebookEntries(7), { entries: [{ id: 5, status: "proposed" }] });
    await sendHeldDiscards(qc, [5], 7);
    const urls = fetchMock.mock.calls
      .filter((c: unknown[]) => (c[1] as RequestInit | undefined)?.method === "DELETE")
      .map((c: unknown[]) => String(c[0]));
    expect(urls).toHaveLength(1);
    expect(urls[0]).toContain("chat_id=7");
    expect(urls[0]).toContain("only_suggestion=true");
  });

  it("GROUND: a note already kept on hand is not asked about at all", async () => {
    const fetchMock = mockFetch({ "DELETE /notebook/entries/5": { body: { ok: true } } });
    const qc = new QueryClient();
    qc.setQueryData(keys.notebookEntries(7), { entries: [{ id: 5, status: "accepted" }] });
    await sendHeldDiscards(qc, [5], 7);
    expect(fetchMock.mock.calls.filter((c: unknown[]) =>
      (c[1] as RequestInit | undefined)?.method === "DELETE")).toHaveLength(0);
  });

  it("reads the server's 'kept' answer, and its absence as false", () => {
    expect(DiscardOkSchema.parse({ ok: true, kept: true }).kept).toBe(true);
    expect(DiscardOkSchema.parse({ ok: true }).kept).toBe(false);
  });
});
