/**
 * uploads.ts - image upload API (POST /uploads/images).
 *
 * Multipart endpoint, so it cannot go through the JSON client helpers:
 * fetch must receive a raw FormData body WITHOUT a manual Content-Type
 * header (the browser sets the multipart boundary itself).
 *
 * Errors are normalized to the same ApiError shape client.ts produces
 * ({status, detail, message}) - keep the error handling here in sync with
 * client.ts (owned elsewhere, so its helpers are not importable from here).
 *
 * Privacy: image bytes go only to the local backend; nothing is persisted
 * in browser storage.
 */

import { z } from "zod/v4";
import { getErrorMessage } from "../errors";
import type { ApiError } from "./client";
import { notifyVaultLocked } from "./client";
import { API_BASE as BASE } from "./base";

// Exact match of the uploads contract 201 body
export const UploadedImageSchema = z.object({
  id: z.number(),
  mime: z.string(),
  width: z.number(),
  height: z.number(),
  byte_size: z.number(),
});

export type UploadedImage = z.infer<typeof UploadedImageSchema>;

/**
 * URL of an uploaded image binary (GET /uploads/images/{id}) - usable
 * directly as an <img src> (plain GET, correct Content-Type, no auth).
 */
export function imageUrl(id: number): string {
  return `${BASE}/uploads/images/${id}`;
}

/**
 * Upload one image file. Resolves with the stored image metadata; rejects
 * with an ApiError (attachment_invalid, attachment_too_large, network_error,
 * invalid_response_shape, …) - never a raw fetch/Zod error.
 */
export async function uploadImage(file: File): Promise<UploadedImage> {
  const form = new FormData();
  form.append("file", file);

  let res: Response;
  try {
    // No Content-Type header - fetch derives multipart/form-data + boundary
    // from the FormData body.
    res = await fetch(`${BASE}/uploads/images`, {
      method: "POST",
      body: form,
    });
  } catch {
    // Network failure - same shape as client.ts NETWORK_ERROR (keep in sync).
    const err: ApiError = {
      status: 0,
      detail: "network_error",
      message: getErrorMessage("network_error"),
    };
    throw err;
  }

  const json = await res.json().catch(() => ({}));

  if (!res.ok) {
    if (res.status === 423) notifyVaultLocked();
    // Keep in sync with client.ts handleResponse: backend detail code or a
    // safe fallback - raw upstream text never leaves this module.
    const detail =
      typeof (json as Record<string, unknown>)?.detail === "string" &&
      ((json as Record<string, unknown>).detail as string).length > 0
        ? ((json as Record<string, unknown>).detail as string)
        : "unknown_error";
    const err: ApiError = {
      status: res.status,
      detail,
      message: getErrorMessage(detail),
    };
    throw err;
  }

  // ZodError is caught and normalized - raw Zod stack traces never reach the UI.
  const parsed = UploadedImageSchema.safeParse(json);
  if (!parsed.success) {
    const err: ApiError = {
      status: res.status,
      detail: "invalid_response_shape",
      message: getErrorMessage("invalid_response_shape"),
    };
    throw err;
  }
  return parsed.data;
}
