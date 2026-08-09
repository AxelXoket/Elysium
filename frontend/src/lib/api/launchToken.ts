/**
 * The secret this window was launched with, read once and kept in memory.
 *
 * The desktop app opens the page at `#elysium-token=<secret>`, and every API
 * request carries it back in a header. Without it the local server would
 * answer any program running as this user - loopback is not a permission
 * boundary, and while Elysium is open the vault is unlocked by definition.
 *
 * Three deliberate choices:
 *
 *   - read from the FRAGMENT, which browsers never send to a server and never
 *     put in a request log or a Referer;
 *   - stripped from the address immediately, so it is not sitting in the
 *     visible URL or in the session history entry;
 *   - kept in a module variable, never in localStorage or sessionStorage.
 *     Browser storage is readable from the profile directory on disk and
 *     outlives the launch this token belongs to, which is the whole point of
 *     it being per-launch.
 *
 * Absent in development, where the page is served by Vite and no token was
 * issued. The backend's gate is unarmed in exactly the same case, so the two
 * halves agree without either needing to know which mode it is in.
 */
let token: string | null = null;

/** The header name. Custom headers cannot be set by a cross-origin form. */
export const LAUNCH_TOKEN_HEADER = "X-Elysium-Token";

export function readLaunchToken(): void {
  if (typeof window === "undefined") return;
  const fragment = window.location.hash.replace(/^#/, "");
  if (!fragment) return;
  const found = new URLSearchParams(fragment).get("elysium-token");
  if (!found) return;
  token = found;
  // Take it out of the address bar and out of this history entry. replaceState
  // rather than assigning location.hash: the latter pushes a new entry and
  // leaves the old one, token and all, one Back away.
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
}

export function launchTokenHeader(): Record<string, string> {
  return token ? { [LAUNCH_TOKEN_HEADER]: token } : {};
}

/** Test seam. Never called by the app. */
export function __setLaunchTokenForTest(value: string | null): void {
  token = value;
}
