import { Loader2, AlertCircle, X } from "lucide-react";
import { MAX_ATTACHMENTS } from "./attachments";
import type { StagedAttachment } from "./attachments";

interface AttachmentStripProps {
  attachments: readonly StagedAttachment[];
  /** Removes one staged attachment by its client key. */
  onRemove: (key: string) => void;
  /** When true, the staged images cannot be sent (e.g. the selected model is
   * text-only). The strip is dimmed and the counter is replaced with a note. */
  inactive?: boolean;
}

/**
 * Thumbnail strip for staged image attachments, rendered above the composer
 * textarea. Uploading entries show a spinner overlay, failed entries an
 * error overlay (they auto-remove shortly after), and every entry has a
 * remove button.
 */
export function AttachmentStrip({
  attachments,
  onRemove,
  inactive,
}: AttachmentStripProps) {
  if (attachments.length === 0) return null;

  const atCap = attachments.length >= MAX_ATTACHMENTS;
  const total = attachments.length;

  return (
    <div
      className="mb-3 flex flex-wrap items-center gap-2"
      aria-label="Staged attachments"
      style={inactive ? { opacity: 0.5 } : undefined}
    >
      {attachments.map((attachment, index) => (
        <div
          key={attachment.key}
          className="group relative h-14 w-14 shrink-0 overflow-hidden rounded-xl"
          style={{
            backgroundColor: "rgba(28, 38, 50, 0.08)",
            border: "1px solid rgba(28, 38, 50, 0.14)",
          }}
        >
          {attachment.previewUrl !== "" && (
            <img
              src={attachment.previewUrl}
              alt="Staged image"
              className="h-full w-full object-cover"
              style={{
                opacity: attachment.status === "ready" ? 1 : 0.45,
              }}
            />
          )}

          {attachment.status === "uploading" && (
            <span
              className="absolute inset-0 flex items-center justify-center"
              role="status"
              aria-label="Uploading image"
            >
              <Loader2
                size={16}
                className="animate-spin"
                style={{ color: "var(--color-es-asst-bubble-text)" }}
              />
            </span>
          )}

          {attachment.status === "error" && (
            <span
              className="absolute inset-0 flex items-center justify-center"
              aria-label="Upload failed"
            >
              <AlertCircle
                size={16}
                style={{ color: "var(--color-es-danger)" }}
              />
            </span>
          )}

          <button
            type="button"
            aria-label={`Remove attachment ${index + 1}`}
            title="Remove attachment"
            onClick={() => onRemove(attachment.key)}
            className="absolute top-0.5 right-0.5 rounded-full p-0.5 opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100"
            style={{
              backgroundColor: "rgba(28, 38, 50, 0.55)",
              color: "rgba(255, 255, 255, 0.92)",
            }}
          >
            <X size={11} />
          </button>
        </div>
      ))}

      <span
        className="text-[10px]"
        style={{ color: "var(--color-es-asst-bubble-text)", opacity: 0.55 }}
      >
        {inactive ? (
          "Not supported by the selected model"
        ) : (
          <>
            {total}/{MAX_ATTACHMENTS}
            {atCap && " - up to 4 images per message"}
          </>
        )}
      </span>
    </div>
  );
}
