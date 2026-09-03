/**
 * The two routes a browser element used to load, and the reason they stopped.
 *
 * A stored picture and a spoken reply were fetched by the BROWSER - `<img src>`
 * and an audio element - and an element load cannot carry a custom header. So
 * the backend's launch-token gate accepted `Sec-Fetch-Site: same-origin` in the
 * token's place on exactly those two routes. That header is a forbidden header
 * name for page script, which is why it looked safe; it is nothing of the kind
 * for a PROCESS. One `curl -H` typed it, and because upload ids are sequential
 * every picture in an unlocked vault could be walked by counting upward.
 *
 * The fix is on this side of the wire: fetch both routes with the token and
 * hand the bytes to a blob: URL. These cases measure the ARGUMENT the loaders
 * pass to fetch, never the text of a source file.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchImageObjectUrl, revokeImageObjectUrl } from "@/lib/api/uploads";
import { fetchTtsObjectUrl, revokeTtsObjectUrl } from "@/lib/api/tts";
import {
  LAUNCH_TOKEN_HEADER,
  __setLaunchTokenForTest,
} from "@/lib/api/launchToken";

const SECRET = "token-for-this-launch";

/** The bytes the server would send back, so the blob handed to
 *  createObjectURL can be identified as THIS response's body. */
const PAYLOAD = "not-really-a-png";

function serve(status = 200) {
  // Typed with the signature fetch really has: the whole point of these cases
  // is to read the SECOND argument back, and a mock declared with no
  // parameters records a call tuple that has no second element to read.
  const mock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(
    async () =>
      new Response(PAYLOAD, {
        status,
        headers: { "Content-Type": "image/png" },
      }),
  );
  vi.stubGlobal("fetch", mock);
  return mock;
}

function headersOf(mock: ReturnType<typeof serve>): Record<string, string> {
  const init = mock.mock.calls[0]?.[1] as RequestInit | undefined;
  return (init?.headers ?? {}) as Record<string, string>;
}

describe("the routes a browser element used to load", () => {
  let created: Blob[];

  beforeEach(() => {
    created = [];
    // jsdom implements neither half of the object-URL API.
    URL.createObjectURL = vi.fn((blob: Blob) => {
      created.push(blob);
      return `blob:made-${created.length}`;
    });
    URL.revokeObjectURL = vi.fn();
    __setLaunchTokenForTest(SECRET);
  });

  afterEach(() => {
    __setLaunchTokenForTest(null);
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    Reflect.deleteProperty(URL, "createObjectURL");
    Reflect.deleteProperty(URL, "revokeObjectURL");
  });

  describe("the stored picture", () => {
    it("is fetched with the launch token", async () => {
      const fetchMock = serve();

      await fetchImageObjectUrl(42);

      expect(String(fetchMock.mock.calls[0][0])).toContain(
        "/uploads/images/42",
      );
      expect(headersOf(fetchMock)[LAUNCH_TOKEN_HEADER]).toBe(SECRET);
    });

    it("sends no token header when no launch issued one", async () => {
      // GROUND CONTROL for the case above. The header is read out of the
      // token module rather than being a literal this file writes on both
      // sides of the assertion, so with the module empty it disappears. A
      // developer running Vite by hand is in exactly this state, and the
      // backend gate is unarmed to match.
      __setLaunchTokenForTest(null);
      const fetchMock = serve();

      await fetchImageObjectUrl(42);

      expect(String(fetchMock.mock.calls[0][0])).toContain(
        "/uploads/images/42",
      );
      expect(headersOf(fetchMock)[LAUNCH_TOKEN_HEADER]).toBeUndefined();
    });

    it("hands the response body to createObjectURL and returns that url", async () => {
      serve();

      const url = await fetchImageObjectUrl(42);

      expect(url).toBe("blob:made-1");
      expect(created).toHaveLength(1);
      // The blob is the RESPONSE's bytes, not an empty placeholder that
      // happens to satisfy the call.
      await expect(created[0].text()).resolves.toBe(PAYLOAD);
    });

    it("rejects when the server refuses, so the placeholder still shows", async () => {
      // The graceful "image unavailable" box used to be reached through the
      // element's onError. The fetch has to reject for it to still be
      // reachable, or a 404 renders as a permanently blank frame.
      serve(404);

      await expect(fetchImageObjectUrl(42)).rejects.toThrow();
      expect(URL.createObjectURL).not.toHaveBeenCalled();
    });
  });

  describe("the spoken reply", () => {
    it("is fetched with the launch token", async () => {
      const fetchMock = serve();

      await fetchTtsObjectUrl("clip-1");

      expect(String(fetchMock.mock.calls[0][0])).toContain(
        "/tts/audio/clip-1",
      );
      expect(headersOf(fetchMock)[LAUNCH_TOKEN_HEADER]).toBe(SECRET);
    });

    it("sends no token header when no launch issued one", async () => {
      __setLaunchTokenForTest(null);
      const fetchMock = serve();

      await fetchTtsObjectUrl("clip-1");

      expect(headersOf(fetchMock)[LAUNCH_TOKEN_HEADER]).toBeUndefined();
    });

    it("hands the response body to createObjectURL and returns that url", async () => {
      serve();

      const url = await fetchTtsObjectUrl("clip-1");

      expect(url).toBe("blob:made-1");
      await expect(created[0].text()).resolves.toBe(PAYLOAD);
    });

    it("rejects when the server refuses", async () => {
      serve(404);

      await expect(fetchTtsObjectUrl("clip-1")).rejects.toThrow();
      expect(URL.createObjectURL).not.toHaveBeenCalled();
    });
  });

  describe("the blob lifetime is closed", () => {
    it("revoking passes the url straight through", () => {
      revokeImageObjectUrl("blob:made-1");
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:made-1");

      revokeTtsObjectUrl("blob:made-2");
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:made-2");
    });

    it("revoking nothing is not an error", () => {
      // The components call this from a cleanup that can run before the fetch
      // ever resolved, so null is a normal argument rather than a bug.
      revokeImageObjectUrl(null);
      revokeTtsObjectUrl(null);
      expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    });
  });
});
