/**
 * Proves the SSE and upload transports - which bypass client.ts handleResponse
 * - still fire the vault-locked signal on a 423. Without this, a regenerate or
 * an image upload against a locked backend would strand the app on stale data
 * with no path back to the lock screen.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  setVaultLockedHandler,
  isApiError,
} from "@/lib/api/client";
import { streamCompletion } from "@/lib/api/stream";
import { uploadImage } from "@/lib/api/uploads";

function stub423(path: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes(path)) {
        return new Response(JSON.stringify({ detail: "vault_locked" }), {
          status: 423,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response("{}", { status: 404 });
    }),
  );
}

describe("vault-lock signal from bypassing transports", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    setVaultLockedHandler(null);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    setVaultLockedHandler(null);
  });

  it("streamCompletion fires the handler on a 423", async () => {
    stub423("/regenerate/stream");
    let fired = 0;
    setVaultLockedHandler(() => {
      fired += 1;
    });

    let threw: unknown = null;
    try {
      await streamCompletion(
        "/chats/1/regenerate/stream",
        { message_id: 5, model_id: "m" },
        { onEvent: () => {} },
      );
    } catch (err) {
      threw = err;
    }

    expect(fired).toBe(1);
    expect(isApiError(threw) && threw.status).toBe(423);
  });

  it("uploadImage fires the handler on a 423", async () => {
    stub423("/uploads/images");
    let fired = 0;
    setVaultLockedHandler(() => {
      fired += 1;
    });

    const file = new File([new Uint8Array([1, 2, 3])], "x.png", {
      type: "image/png",
    });
    let threw: unknown = null;
    try {
      await uploadImage(file);
    } catch (err) {
      threw = err;
    }

    expect(fired).toBe(1);
    expect(isApiError(threw) && threw.status).toBe(423);
  });
});
