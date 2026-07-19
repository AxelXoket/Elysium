import type { ZodType } from "zod/v4";

import { API_BASE as BASE } from "./base";

// Normalized error shape - never expose raw backend stack traces or Zod internals
export type ApiError = { status: number; detail: string; message: string };

export function isApiError(err: unknown): err is ApiError {
  return (
    typeof err === "object" &&
    err !== null &&
    "status" in err &&
    "detail" in err
  );
}

const NETWORK_ERROR: ApiError = {
  status: 0,
  detail: "network_error",
  message: "Could not reach the server. Please check your connection.",
};

/**
 * Vault-lock signal: any data endpoint answering 423 means the vault locked
 * out from under us (e.g. backend restarted). The registered handler lets
 * the boot gate refetch vault status and drop to the lock screen without
 * this module importing any React/query code.
 */
let onVaultLocked: (() => void) | null = null;

/** Coalescing latch: when the vault locks under a busy screen, EVERY in-flight
 * query 423s at once and each would re-fire the handler - whose vault-status
 * invalidation cancels and restarts the previous refetch (TanStack refetches
 * with cancelRefetch), delaying the lock screen by several aborted round
 * trips. One signal per burst is enough; the boot gate takes over from there. */
let vaultLockedLatch: ReturnType<typeof setTimeout> | null = null;
const VAULT_LOCKED_COALESCE_MS = 250;

export function setVaultLockedHandler(handler: (() => void) | null): void {
  onVaultLocked = handler;
  // A handler change is a lifecycle boundary (gate mount/unmount): a stale
  // coalescing window from the previous lifecycle must never swallow the new
  // handler's first signal.
  if (vaultLockedLatch != null) {
    clearTimeout(vaultLockedLatch);
    vaultLockedLatch = null;
  }
}

/** Fire the vault-locked signal. Exported so the SSE and upload transports -
 * which don't route through handleResponse - can report a 423 too. */
export function notifyVaultLocked(): void {
  if (vaultLockedLatch != null) return;
  vaultLockedLatch = setTimeout(() => {
    vaultLockedLatch = null;
  }, VAULT_LOCKED_COALESCE_MS);
  onVaultLocked?.();
}

/**
 * Shared response handling for request/rawRequest.
 *
 * - Non-OK responses are normalized into ApiError with the backend detail code.
 * - Schema parse failures keep the REAL response status (never a fake 200)
 *   with detail "invalid_response_shape".
 * - Note: the backend currently never returns 204/no-body responses; every
 *   endpoint returns JSON, so an unconditional res.json() here is safe.
 */
async function handleResponse<T>(
  res: Response,
  schema: ZodType<T>,
  path?: string,
): Promise<T> {
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    // Never fire the lock signal for a 423 from a vault route itself - that
    // would invalidate vault-status → refetch → 423 → loop. (The backend
    // exempts /vault/* today; this is belt-and-suspenders.)
    if (res.status === 423 && !path?.startsWith("/vault/")) onVaultLocked?.();
    const detail =
      res.status === 422 && Array.isArray((json as Record<string, unknown>)?.detail)
        ? "invalid_generation_params"
        : ((json as Record<string, unknown>)?.detail as string) ?? "unknown_error";
    const err: ApiError = {
      status: res.status,
      detail,
      message: `Request failed: ${res.status}`,
    };
    throw err;
  }
  // ZodError is caught here and normalized - raw Zod stack traces never reach the UI.
  try {
    return schema.parse(json) as T;
  } catch {
    const err: ApiError = {
      status: res.status,
      detail: "invalid_response_shape",
      message: "Unexpected response format from server.",
    };
    throw err;
  }
}

export async function request<T>(
  path: string,
  schema: ZodType<T>,
  init?: RequestInit,
): Promise<T> {
  let res: Response;
  try {
    // init spreads FIRST so a caller-passed headers object cannot silently
    // replace the merged headers (which would drop Content-Type).
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    // Network failure (offline, DNS, CORS preflight, etc.)
    throw { ...NETWORK_ERROR };
  }
  return handleResponse(res, schema, path);
}

/**
 * Raw fetch for endpoints where the body must be sent as-is (e.g. character import).
 * Does not go through the generic request() helper.
 */
export async function rawRequest<T>(
  path: string,
  schema: ZodType<T>,
  init: RequestInit,
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, init);
  } catch {
    throw { ...NETWORK_ERROR };
  }
  return handleResponse(res, schema, path);
}
