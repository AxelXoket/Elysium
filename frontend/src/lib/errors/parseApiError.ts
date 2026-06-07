/**
 * parseApiError.ts — Safe parser for any thrown value into a normalized ApiError.
 *
 * Handles:
 *  - ApiError objects (from client.ts request/rawRequest)
 *  - FastAPI { detail: "error_code" } shapes
 *  - FastAPI validation errors where detail is an array/object
 *  - fetch TypeError / network errors
 *  - unknown thrown values (strings, numbers, undefined)
 *
 * Never exposes raw upstream/provider text to the caller.
 */

import type { ApiError } from "@/lib/api/client";
import { isApiError } from "@/lib/api/client";
import { getErrorMessage } from "./errorMessages";

/**
 * Normalize any thrown value into a safe ApiError with a user-facing message.
 *
 * The returned `message` is always a safe, user-friendly string from the
 * error code map. The `detail` is the backend error code string (or a
 * synthetic code for network/unknown errors). Raw upstream bodies, Zod
 * internals, and provider response text are never propagated.
 */
export function parseApiError(err: unknown): ApiError {
  // Already an ApiError from client.ts
  if (isApiError(err)) {
    const detail = normalizeDetail(err.detail);
    return {
      status: err.status,
      detail,
      message: getErrorMessage(detail),
    };
  }

  // Network / fetch failure (TypeError: Failed to fetch)
  if (err instanceof TypeError) {
    return {
      status: 0,
      detail: "network_error",
      message: getErrorMessage("network_error"),
    };
  }

  // Generic Error with a message (but not a network error)
  if (err instanceof Error) {
    return {
      status: 0,
      detail: "unknown_error",
      message: getErrorMessage("unknown_error"),
    };
  }

  // Completely unknown thrown value
  return {
    status: 0,
    detail: "unknown_error",
    message: getErrorMessage("unknown_error"),
  };
}

/**
 * Normalize the `detail` field from a backend response.
 *
 * FastAPI validation errors can return `detail` as an array of objects
 * (e.g., Pydantic validation). We collapse these to a safe code string
 * so raw field-level validation internals are never shown to the user.
 */
function normalizeDetail(detail: unknown): string {
  if (typeof detail === "string" && detail.length > 0) {
    return detail;
  }

  // FastAPI validation array → synthetic code
  if (Array.isArray(detail)) {
    return "invalid_generation_params";
  }

  // Object detail → synthetic code
  if (typeof detail === "object" && detail !== null) {
    return "invalid_generation_params";
  }

  return "unknown_error";
}
