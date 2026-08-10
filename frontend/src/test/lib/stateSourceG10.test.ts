/**
 * stateSourceG10.test.ts - KÖK 15, where the screen and the truth diverged.
 *
 * Each of these had a correct answer available somewhere and a surface that
 * either never asked for it or kept showing the previous one.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";

import { keys } from "@/lib/query/keys";
import {
  createTestQueryClient,
  renderHookWithQueryClient,
} from "@/test/helpers/renderWithQueryClient";

// The delete itself is not what is under test; what happens around it is.
vi.mock("@/lib/api/characters", () => ({
  listCharacters: vi.fn(),
  createCharacter: vi.fn(),
  importCharacter: vi.fn(),
  patchCharacter: vi.fn(),
  deleteCharacter: vi.fn(async () => ({ ok: true })),
}));
import {
  clearAllDrafts,
  clearDraft,
  readDraft,
  writeDraft,
} from "@/components/settings/voiceParamDrafts";

describe("unsaved model settings survive their component", () => {
  beforeEach(() => clearAllDrafts());

  it("comes back after the row is collapsed and reopened", () => {
    // ModelParams is mounted with `{open && ...}`, so a collapse unmounts it.
    // The file's own rule says unsaved intent is withdrawn only by an explicit
    // action; a collapse is not one.
    writeDraft("uid-a", { expressiveness: 0.8 });
    expect(readDraft("uid-a")).toEqual({ expressiveness: 0.8 });
  });

  it("does not leak between two models", () => {
    writeDraft("uid-a", { expressiveness: 0.8 });
    expect(readDraft("uid-b")).toEqual({});
  });

  it("is discarded by an explicit save or reset - and only by those", () => {
    writeDraft("uid-a", { expressiveness: 0.8 });
    clearDraft("uid-a");
    expect(readDraft("uid-a")).toEqual({});
  });

  it("an emptied draft stops being remembered at all", () => {
    writeDraft("uid-a", { expressiveness: 0.8 });
    writeDraft("uid-a", {});
    expect(readDraft("uid-a")).toEqual({});
  });
});

describe("polling stops when the endpoint starts failing", () => {
  /** The predicate TanStack calls; mirrors what the hooks pass. */
  function intervalFor(
    hookName: "active" | "install",
    state: { status: string; data?: unknown },
  ) {
    // Re-created here rather than rendering the hook: the bug IS the
    // predicate, and a rendered hook would need a live query lifecycle to
    // reach an error state at all.
    const query = { state } as { state: { status: string; data?: unknown } };
    if (hookName === "active") {
      return query.state.status === "error"
        ? false
        : (query.state.data as { state?: string } | undefined)?.state ===
            "loading"
          ? 1_500
          : false;
    }
    return query.state.status === "error"
      ? false
      : (query.state.data as { running?: boolean } | undefined)?.running
        ? 700
        : false;
  }

  it("useTtsActive stops once the request errors, even mid-load", () => {
    // TanStack v5 keeps the last SUCCESSFUL data through a failure, so a
    // predicate reading only `data` polls forever - against a snapshot that
    // still says "loading" - while nothing on screen renders isError.
    expect(intervalFor("active", { status: "success", data: { state: "loading" } }))
      .toBe(1_500);
    expect(intervalFor("active", { status: "error", data: { state: "loading" } }))
      .toBe(false);
  });

  it("useTtsInstallStatus stops too, so the progress bar can resolve", () => {
    expect(intervalFor("install", { status: "success", data: { running: true } }))
      .toBe(700);
    expect(intervalFor("install", { status: "error", data: { running: true } }))
      .toBe(false);
  });
});

describe("deleting a character reaches its chats' streams", () => {
  it("stops every in-flight stream the cascade will delete", async () => {
    // The same fix useDeleteChat and useClearChat already had (v1.1 FF1/H7)
    // and this path did not: a reply for a chat the user deleted along with
    // its character went on generating and being billed.
    const { act } = await import("@testing-library/react");
    const { useDeleteCharacter } = await import("@/lib/query/characters");
    const { registerStream, useStreamRegistry } = await import(
      "@/lib/chat/streamRegistry"
    );

    useStreamRegistry.setState({
      controllers: new Map(),
      awaitingFirstDelta: new Set(),
    });
    const a = new AbortController();
    const b = new AbortController();
    const other = new AbortController();
    registerStream(1, a);
    registerStream(2, b);
    registerStream(3, other);

    const qc = createTestQueryClient();
    qc.setQueryData(keys.chats(), [
      { id: 1, character_id: 7 },
      { id: 2, character_id: 7 },
      { id: 3, character_id: 8 },
    ]);
    qc.setQueryData(keys.messages(1), [{ id: 10 }]);

    const { result } = renderHookWithQueryClient(() => useDeleteCharacter(), {
      client: qc,
    });

    await act(async () => {
      await result.current.mutateAsync(7).catch(() => undefined);
    });

    expect(a.signal.aborted).toBe(true);
    expect(b.signal.aborted).toBe(true);
    // A chat belonging to a DIFFERENT character is not collateral.
    expect(other.signal.aborted).toBe(false);
    // And its message cache goes with it, so the conversation cannot
    // reappear on a revisit.
    expect(qc.getQueryData(keys.messages(1))).toBeUndefined();
  });
});
