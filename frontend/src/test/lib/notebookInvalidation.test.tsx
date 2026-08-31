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
import { useSetAutoAccept, useCreateNote } from "@/lib/query/notebook";
import { keys } from "@/lib/query/keys";
import { mockFetch } from "../mocks/api";

/** Every queryKey handed to invalidateQueries, serialised for comparison. */
function invalidated(spy: ReturnType<typeof vi.spyOn>): string[] {
  return (spy.mock.calls as unknown[][]).map((call) =>
    JSON.stringify((call[0] as { queryKey?: unknown })?.queryKey));
}

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
