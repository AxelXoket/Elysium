/**
 * attachments.ts - shared staged-attachment types and helpers for the chat
 * composer flow (ChatCanvas owns the state; Composer/AttachmentStrip render it).
 *
 * Staged attachments are in-memory only - the contract forbids persisting
 * drafts or attachments in browser storage.
 */

/** Contract cap: at most 4 images per message. */
export const MAX_ATTACHMENTS = 4;

/** Mime types the uploads endpoint accepts (contract: png/jpeg/webp). */
export const ACCEPTED_IMAGE_TYPES: ReadonlySet<string> = new Set([
  "image/png",
  "image/jpeg",
  "image/webp",
]);

/** `accept` attribute for the hidden file input - keep in sync with the set above. */
export const ACCEPTED_IMAGE_ACCEPT = "image/png,image/jpeg,image/webp";

export type StagedAttachmentStatus = "uploading" | "ready" | "error";

export interface StagedAttachment {
  /** Client-side identity - stable across the upload lifecycle. */
  key: string;
  /** Server upload id - null until the upload succeeds. */
  id: number | null;
  mime: string;
  /** Image dimensions from the upload response - null while uploading. */
  width: number | null;
  height: number | null;
  /** Local object URL for the thumbnail (revoked on consume/remove/unmount). */
  previewUrl: string;
  status: StagedAttachmentStatus;
}

export function isAcceptedImageFile(file: File): boolean {
  return ACCEPTED_IMAGE_TYPES.has(file.type);
}

/**
 * The byte ceiling, mirrored from the backend's MAX_UPLOAD_BYTES.
 *
 * K-32. Nothing on this side read File.size at all, so a 400 MB picture was
 * staged, given a preview, marked "uploading" and sent in full before the
 * server's 413 came back. The backend was never in danger - it reads
 * MAX_UPLOAD_BYTES + 1 and stops, and a body-size shield sits in front of
 * that - but the person waited for a transfer that could only ever be
 * refused, and on a slow link that is a long wait for nothing.
 *
 * A MIRROR, and the honest thing is to say so: the value lives in
 * backend/config.py and this copy can drift. It is deliberately the SAME
 * number rather than a smaller "safe" one, so the only file this refuses is
 * one the server would refuse too - a client that guessed low would reject
 * pictures the app can actually take.
 */
export const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;

export function isWithinAttachmentSizeLimit(file: File): boolean {
  return file.size <= MAX_ATTACHMENT_BYTES;
}

/**
 * Object-URL helpers. jsdom does not implement createObjectURL/revokeObjectURL
 * (tests stub them when they assert previews); real browsers always have them.
 */
export function createPreviewUrl(file: File): string {
  return typeof URL.createObjectURL === "function"
    ? URL.createObjectURL(file)
    : "";
}

export function revokePreviewUrl(url: string): void {
  if (url && typeof URL.revokeObjectURL === "function") {
    URL.revokeObjectURL(url);
  }
}
