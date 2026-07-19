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
