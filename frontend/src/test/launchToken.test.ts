/**
 * The secret that separates this window from every other program on the
 * machine.
 *
 * Loopback is not a permission boundary: without this, anything running as the
 * user could read the whole conversation over HTTP while the app is open - and
 * open is exactly when the vault is unlocked.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { z } from "zod/v4";

import {
  LAUNCH_TOKEN_HEADER,
  launchTokenHeader,
  readLaunchToken,
  __setLaunchTokenForTest,
} from "@/lib/api/launchToken";
import { getErrorMessage } from "@/lib/errors/errorMessages";

describe("launch token", () => {
  beforeEach(() => {
    __setLaunchTokenForTest(null);
    window.history.replaceState(null, "", "/");
  });

  it("reads the token out of the fragment", () => {
    window.history.replaceState(null, "", "/#elysium-token=abc123");
    readLaunchToken();
    expect(launchTokenHeader()).toEqual({ [LAUNCH_TOKEN_HEADER]: "abc123" });
  });

  it("strips it from the address once read", () => {
    // Otherwise it sits in the visible URL and in the history entry, and the
    // point of a fragment was that it goes nowhere.
    window.history.replaceState(null, "", "/#elysium-token=abc123");
    readLaunchToken();
    expect(window.location.hash).toBe("");
  });

  it("keeps the path and query when stripping", () => {
    window.history.replaceState(null, "", "/chats?x=1#elysium-token=abc123");
    readLaunchToken();
    expect(window.location.pathname).toBe("/chats");
    expect(window.location.search).toBe("?x=1");
  });

  it("sends nothing when no token was issued", () => {
    // Development: the page is served by Vite and no launch happened. The
    // backend gate is unarmed in exactly the same case.
    readLaunchToken();
    expect(launchTokenHeader()).toEqual({});
  });

  it("ignores a fragment that is not ours", () => {
    window.history.replaceState(null, "", "/#section=appearance");
    readLaunchToken();
    expect(launchTokenHeader()).toEqual({});
  });

  it("leaves an unrelated fragment in place", () => {
    // Stripping every fragment would break any in-page anchor the app grows.
    window.history.replaceState(null, "", "/#section=appearance");
    readLaunchToken();
    expect(window.location.hash).toBe("#section=appearance");
  });

  it("never writes the token to browser storage", () => {
    // Browser storage is readable from the profile directory on disk and
    // outlives the launch this token belongs to.
    window.history.replaceState(null, "", "/#elysium-token=abc123");
    readLaunchToken();
    const all = [
      ...Object.values(localStorage),
      ...Object.values(sessionStorage),
    ].join(" ");
    expect(all).not.toContain("abc123");
  });
});

/**
 * What a reload actually does, which is the question the strip above raises
 * and nothing here answered.
 *
 * The token arrives once, in the fragment, and is stripped from the address
 * the moment it is read. A reload re-fetches THAT address and starts every
 * module again from nothing, so both copies of the secret are gone at once -
 * the one in the URL and the one in the module. Nothing hands it back:
 * run_app.py puts the fragment on the window's first navigation only, and
 * there is no route, no bridge and no storage that re-issues it.
 *
 * The consequence is not cosmetic. main.py's `launch_token_gate` answers 403
 * `launch_token_invalid` to every /api/v1 request without the header, while
 * the shell HTML and the assets are outside the gate and still load - so a
 * reloaded window looks alive and can do nothing. That is why the crash card
 * in components/errors/ErrorBoundary.tsx no longer offers one.
 */
describe("a reload after the strip", () => {
  /** A fresh start of the renderer: module state gone, address as it stands.
   *  `resetModules` is what makes this a real measurement rather than a
   *  description of one - the module-level `let token` is the thing under
   *  test, and this is the only way to lose it the way a reload does. */
  async function bootRenderer() {
    vi.resetModules();
    const mod = await import("@/lib/api/launchToken");
    mod.readLaunchToken();
    return mod;
  }

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("comes back with no token, because the address no longer carries one", async () => {
    window.history.replaceState(null, "", "/#elysium-token=abc123");
    const launched = await bootRenderer();
    expect(launched.launchTokenHeader()).toEqual({
      [launched.LAUNCH_TOKEN_HEADER]: "abc123",
    });
    expect(window.location.hash).toBe("");

    const reloaded = await bootRenderer();
    expect(reloaded.launchTokenHeader()).toEqual({});
  });

  it("GROUND: the same restart DOES find a token when the address still has one", async () => {
    // Without this the check above would pass on a bootRenderer() that had
    // quietly stopped reading anything at all.
    window.history.replaceState(null, "", "/#elysium-token=abc123");
    await bootRenderer();
    window.history.replaceState(null, "", "/#elysium-token=abc123");
    const reloaded = await bootRenderer();
    expect(reloaded.launchTokenHeader()).toEqual({
      [reloaded.LAUNCH_TOKEN_HEADER]: "abc123",
    });
  });

  it("leaves every API request unauthenticated, and the gate refuses them", async () => {
    const Ok = z.object({ ok: z.boolean() });
    const reply = (status: number, body: unknown) => ({
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    });

    window.history.replaceState(null, "", "/#elysium-token=abc123");
    await bootRenderer();

    // GROUND: before the reload the same call carries the header, so the
    // absence below is the reload's doing and not the harness's.
    const served = vi.fn().mockResolvedValue(reply(200, { ok: true }));
    vi.stubGlobal("fetch", served);
    const launchedClient = await import("@/lib/api/client");
    await launchedClient.request("/chats", Ok);
    expect(served.mock.calls[0][1].headers).toHaveProperty(LAUNCH_TOKEN_HEADER);

    // The reload. Same address, nothing kept.
    await bootRenderer();
    const refused = vi
      .fn()
      .mockResolvedValue(reply(403, { detail: "launch_token_invalid" }));
    vi.stubGlobal("fetch", refused);
    const reloadedClient = await import("@/lib/api/client");
    await expect(reloadedClient.request("/chats", Ok)).rejects.toMatchObject({
      status: 403,
      detail: "launch_token_invalid",
    });
    expect(refused.mock.calls[0][1].headers).not.toHaveProperty(
      LAUNCH_TOKEN_HEADER,
    );

    // And what the reader is left looking at. The sentence is right, but it
    // was written for a stale tab: reaching it from a reload means the window
    // Elysium itself opened has become one.
    expect(getErrorMessage("launch_token_invalid")).toMatch(
      /start Elysium again/i,
    );
  });
});
