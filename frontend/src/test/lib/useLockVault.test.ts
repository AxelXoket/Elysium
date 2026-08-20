/**
 * useLockVault.test.ts - the audio-left warning store, wired to the real
 * mutation.
 *
 * LockOverlay.test.tsx proves the OVERLAY reacts correctly to the store
 * (GROUND / POSITIVE CONTROL / the pending wait). It never calls
 * useLockVault itself - LockOverlay cannot, since SidebarHeader owns the
 * mutation call and only ever hands the animation a bare `commit` callback
 * (lib/vaultLockUi.ts). So the other half - that a REAL /vault/lock response
 * actually reaches the store - has to be proven here, at the hook.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { waitFor, act } from "@testing-library/react";

import { renderHookWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { useLockVault, useLockAudioWarningStore } from "@/lib/query/vault";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("useLockVault - settling the audio-left store", () => {
  beforeEach(() => {
    useLockAudioWarningStore.setState({ pending: false, audioLeft: [] });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("GROUND: a clean /vault/lock settles the store empty", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ ok: true })),
    );
    const { result } = renderHookWithQueryClient(() => useLockVault());

    act(() => {
      result.current.mutate();
    });
    expect(useLockAudioWarningStore.getState().pending, "onMutate must flip pending before the response lands").toBe(true);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(useLockAudioWarningStore.getState().pending).toBe(false);
    expect(useLockAudioWarningStore.getState().audioLeft).toEqual([]);
  });

  it("POSITIVE CONTROL: audio_left in the response reaches the store", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ ok: true, audio_left: ["reply_9.wav"] })),
    );
    const { result } = renderHookWithQueryClient(() => useLockVault());

    act(() => {
      result.current.mutate();
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(useLockAudioWarningStore.getState().pending).toBe(false);
    expect(useLockAudioWarningStore.getState().audioLeft).toEqual(["reply_9.wav"]);
  });

  it("a refused lock still settles the store, so the overlay is never left waiting", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: "vault_locked" }, 500)),
    );
    const { result } = renderHookWithQueryClient(() => useLockVault());

    act(() => {
      result.current.mutate();
    });
    await waitFor(() => expect(result.current.isError).toBe(true));

    expect(useLockAudioWarningStore.getState().pending).toBe(false);
    expect(useLockAudioWarningStore.getState().audioLeft).toEqual([]);
  });
});
