/**
 * The notebook's own mutations, and the one that forgot the entries.
 *
 * Every note mutation goes through `useNotebookMutation`, which invalidates
 * the whole `notebook` namespace on purpose - a note changes the entry list
 * AND the character budget, and naming each affected key separately is how
 * one of them gets forgotten. `useSetAutoAccept` was written outside that
 * wrapper and invalidated only its own key, which is exactly the mistake the
 * wrapper exists to prevent: turning automatic acceptance on ACCEPTS what is
 * already pending, so the entries change too, and the panel went on showing
 * every proposal in the pending state the switch had just cleared.
 *
 * The keys are imported, never retyped. A test that spells a query key out by
 * hand passes against a hook that invalidates a key nothing reads.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { waitFor } from "@testing-library/react";

import {
  createTestQueryClient,
  renderHookWithQueryClient,
} from "@/test/helpers/renderWithQueryClient";
import {
  useSetAutoAccept,
  useCreateNote,
  useRunBackfill,
  useStopBackfill,
  useUndoBackfill,
} from "@/lib/query/notebook";
import { keys } from "@/lib/query/keys";
import { mockFetch } from "../mocks/api";

/** Every queryKey handed to invalidateQueries, serialised for comparison. */
function invalidated(spy: ReturnType<typeof vi.spyOn>): string[] {
  return (spy.mock.calls as unknown[][]).map((call) =>
    JSON.stringify((call[0] as { queryKey?: unknown })?.queryKey));
}

describe("the backfill job mutations", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  /** The three keys every job mutation must sweep. A job writes or
   *  removes notes (the notebook namespace), moves the worker's
   *  `backfill` block (the worker key), and changes what is unread
   *  (every preview). Naming one is how the others go stale. */
  const expected = [
    JSON.stringify(keys.notebook()),
    JSON.stringify(keys.extractionWorker()),
    JSON.stringify(keys.extractionBackfill()),
  ];

  it("useRunBackfill invalidates the notes, the worker poll and every preview", async () => {
    const qc = createTestQueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    mockFetch({ "POST /notebook/extract/run/7": {
      body: { started: true, job_id: 1, calls_planned: 2 } } });
    const { result } = renderHookWithQueryClient(() => useRunBackfill(), { client: qc });
    await result.current.mutateAsync([7, "all", null]);
    await waitFor(() => {
      for (const key of expected) expect(invalidated(spy)).toContain(key);
    });
  });

  it("useStopBackfill invalidates the same three", async () => {
    const qc = createTestQueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    mockFetch({ "POST /notebook/extract/stop/7": {
      body: { stopped: true, job_id: 1, calls_spent: 1 } } });
    const { result } = renderHookWithQueryClient(() => useStopBackfill(), { client: qc });
    await result.current.mutateAsync([7]);
    await waitFor(() => {
      for (const key of expected) expect(invalidated(spy)).toContain(key);
    });
  });

  it("useUndoBackfill invalidates the same three", async () => {
    const qc = createTestQueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    mockFetch({ "POST /notebook/extract/undo/1": {
      body: { job_id: 1, removed: 2, kept_pinned: 0, kept_edited: 0, kept_accepted: 0, kept_older: 0 } } });
    const { result } = renderHookWithQueryClient(() => useUndoBackfill(), { client: qc });
    await result.current.mutateAsync([1, 7]);
    await waitFor(() => {
      for (const key of expected) expect(invalidated(spy)).toContain(key);
    });
  });
});

describe("useSetAutoAccept", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("invalidates the notebook, not just its own switch", async () => {
    const qc = createTestQueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    mockFetch({ "/notebook/auto-accept": { body: { ok: true } } });

    const { result } = renderHookWithQueryClient(() => useSetAutoAccept(), {
      client: qc,
    });

    // GROUND CONTROL: nothing is invalidated before the switch is thrown.
    expect(invalidated(spy)).toEqual([]);

    result.current.mutate([true]);

    await waitFor(() => {
      expect(invalidated(spy)).toContain(
        JSON.stringify(["extraction", "auto-accept"]));
    });
    // The half that was missing.
    expect(invalidated(spy)).toContain(JSON.stringify(keys.notebook()));
  });
});

describe("the wrapper every other notebook mutation uses", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("still invalidates the whole namespace", async () => {
    // POSITIVE CONTROL for the assertion above: `keys.notebook()` is a real
    // key that a working mutation really does invalidate, so a test looking
    // for it is not looking for something nothing ever produces.
    const qc = createTestQueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    mockFetch({
      "/notebook/7": {
        body: {
          id: 1, chat_id: 7, position: 0, kind: "fact",
          text: "Mira is her sister.", evidence: null,
          durability: "permanent", importance: 2, pinned: 0,
          retired_at: null, superseded_by: null, excluded_reason: null,
          status: "accepted", provenance: "user", source_message_id: null,
          created_at: "2026-08-19", updated_at: "2026-08-19",
        },
      },
    });

    const { result } = renderHookWithQueryClient(() => useCreateNote(), {
      client: qc,
    });

    result.current.mutate([7, { text: "Mira is her sister.", kind: "fact" }]);

    await waitFor(() => {
      expect(invalidated(spy)).toContain(JSON.stringify(keys.notebook()));
    });
  });
});
