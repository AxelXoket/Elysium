/**
 * The secret that separates this window from every other program on the
 * machine.
 *
 * Loopback is not a permission boundary: without this, anything running as the
 * user could read the whole conversation over HTTP while the app is open - and
 * open is exactly when the vault is unlocked.
 */
import { beforeEach, describe, expect, it } from "vitest";

import {
  LAUNCH_TOKEN_HEADER,
  launchTokenHeader,
  readLaunchToken,
  __setLaunchTokenForTest,
} from "@/lib/api/launchToken";

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
