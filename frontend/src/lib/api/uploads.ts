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
import { launchTokenHeader } from "./launchToken";

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
 * URL of an uploaded image binary (GET /uploads/images/{id}).
 *
 * NOT an `<img src>`. It used to be, and that is exactly what forced the
 * launch-token gate to accept a browser signal in the token's place for this
 * route: an element load cannot carry a custom header. The signal it took,
 * `Sec-Fetch-Site`, is forbidden to page script but free to any process, so
 * one curl flag walked in and sequential ids made every stored picture
 * enumerable. Use fetchImageObjectUrl below, which presents the token.
 */
export function imageUrl(id: number): string {
  return `${BASE}/uploads/images/${id}`;
}

/**
 * Fetch an uploaded image WITH the launch token and hand back a blob: URL an
 * `<img src>` can use.
 *
 * The caller owns the returned URL and must revoke it when the element goes
 * away, or the blob stays pinned for the life of the document. Rejects on any
 * non-2xx, so the "image unavailable" placeholder both call sites already
 * render still covers a 404 the way it did when the browser did the fetching.
 */
export async function fetchImageObjectUrl(id: number): Promise<string> {
  const res = await fetch(imageUrl(id), {
    credentials: "same-origin",
    headers: { ...launchTokenHeader() },
  });
  if (!res.ok) {
    if (res.status === 423) notifyVaultLocked();
    throw new Error(`image ${id}: HTTP ${res.status}`);
  }
  return URL.createObjectURL(await res.blob());
}

/** Revoke a URL from fetchImageObjectUrl. jsdom does not implement
 *  revokeObjectURL, so the guard keeps component teardown from throwing in
 *  tests that never stubbed it. */
export function revokeImageObjectUrl(url: string | null): void {
  if (url && typeof URL.revokeObjectURL === "function") {
    URL.revokeObjectURL(url);
  }
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
      // Still no Content-Type - fetch derives multipart/form-data + boundary
      // from the FormData body, and setting it by hand loses the boundary.
      headers: { ...launchTokenHeader() },
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
    // Keep in sync with client.ts handleResponse and stream.ts: backend
    // detail code or a safe fallback - raw upstream text never leaves this
    // module.
    //
    // The 422 array branch is the half this module was missing. A `detail`
    // that is an ARRAY is FastAPI's own unhandled validation shape rather
    // than one of our codes, and the other two transports collapse it to a
    // code the message map has a sentence for. Here it fell through to the
    // generic fallback instead, so the same rejected request read as two
    // different failures depending on which transport happened to send it.
    const body = json as Record<string, unknown>;
    const detail =
      res.status === 422 && Array.isArray(body?.detail)
        ? "invalid_generation_params"
        : typeof body?.detail === "string" && body.detail.length > 0
          ? (body.detail as string)
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

/**
 * Best-effort unstage (DELETE /uploads/images/{id}) for a staged upload the
 * user removed before sending (v1.1 FB8). 404 (already purged), 409 (already
 * linked) and network failures are all EXPECTED terminal states - the 24h +
 * opportunistic server purge is the fallback - so this NEVER throws and never
 * surfaces an error. Resolves true only when the row was actually removed.
 */
export async function unstageImage(id: number): Promise<boolean> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/uploads/images/${id}`, {
      method: "DELETE",
      headers: { ...launchTokenHeader() },
    });
  } catch {
    return false;
  }
  if (res.status === 423) notifyVaultLocked();
  return res.ok;
}
