import type { ZodType } from "zod/v4";

const BASE =
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8787/api/v1";

// Normalized error shape — never expose raw backend stack traces or Zod internals
export type ApiError = { status: number; detail: string; message: string };

export function isApiError(err: unknown): err is ApiError {
  return (
    typeof err === "object" &&
    err !== null &&
    "status" in err &&
    "detail" in err
  );
}

export async function request<T>(
  path: string,
  schema: ZodType<T>,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err: ApiError = {
      status: res.status,
      detail: (json as Record<string, unknown>)?.detail as string ?? "unknown_error",
      message: `Request failed: ${res.status}`,
    };
    throw err;
  }
  // ZodError is caught here and normalized — raw Zod stack traces never reach the UI.
  try {
    return schema.parse(json) as T;
  } catch {
    const err: ApiError = {
      status: 200,
      detail: "invalid_response_shape",
      message: "Unexpected response format from server.",
    };
    throw err;
  }
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
  const res = await fetch(`${BASE}${path}`, init);
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err: ApiError = {
      status: res.status,
      detail: (json as Record<string, unknown>)?.detail as string ?? "unknown_error",
      message: `Request failed: ${res.status}`,
    };
    throw err;
  }
  try {
    return schema.parse(json) as T;
  } catch {
    const err: ApiError = {
      status: 200,
      detail: "invalid_response_shape",
      message: "Unexpected response format from server.",
    };
    throw err;
  }
}
