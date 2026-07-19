import { useState } from "react";
import { ImageOff, X } from "lucide-react";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { imageUrl } from "@/lib/api/uploads";
import type { Attachment } from "@/lib/schemas/chats";

interface ImageLightboxProps {
  /** Attachment to show full-size; null renders the lightbox closed. */
  attachment: Attachment | null;
  onClose: () => void;
}

/**
 * Minimal full-size image viewer for message attachments. The frame is
 * transparent (just the image over the dimmed backdrop); Escape, backdrop
 * click, and the close button all close it.
 *
 * The close control is a distinct button with a solid backing pinned to the
 * image's top-right corner, so it never disappears into the image itself.
 */
export function ImageLightbox({ attachment, onClose }: ImageLightboxProps) {
  return (
    <Dialog
      open={attachment != null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent
        showCloseButton={false}
        className="w-auto max-w-[calc(100%-2rem)] bg-transparent p-0 shadow-none ring-0 sm:max-w-3xl"
      >
        <DialogTitle className="sr-only">Attached image</DialogTitle>
        {/* Keyed by id so switching attachments remounts with a fresh
            (non-errored) state. */}
        {attachment != null && (
          <div className="relative">
            <LightboxImage key={attachment.id} attachment={attachment} />
            <DialogClose
              aria-label="Close"
              className="absolute top-2 right-2 flex h-7 w-7 items-center justify-center rounded-[3px] transition-colors"
              style={{
                background: "rgba(14, 18, 24, 0.68)",
                color: "rgba(255, 255, 255, 0.92)",
                border: "1px solid rgba(255, 255, 255, 0.16)",
              }}
            >
              <X size={15} />
            </DialogClose>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

/**
 * The full-size image, with a graceful placeholder if the binary 404s
 * (attachment_not_found) instead of the browser's broken-image glyph.
 */
function LightboxImage({ attachment }: { attachment: Attachment }) {
  const [errored, setErrored] = useState(false);

  if (errored) {
    return (
      <div
        className="mx-auto flex h-64 w-64 flex-col items-center justify-center gap-2 rounded-md text-center"
        style={{
          backgroundColor: "rgba(18, 26, 36, 0.92)",
          border: "1px solid rgba(255, 255, 255, 0.12)",
          color: "rgba(255, 255, 255, 0.86)",
        }}
      >
        <ImageOff size={28} style={{ opacity: 0.55 }} />
        <span className="text-xs" style={{ opacity: 0.7 }}>
          Image unavailable
        </span>
      </div>
    );
  }

  return (
    <img
      src={imageUrl(attachment.id)}
      alt="attached image"
      width={attachment.width}
      height={attachment.height}
      onError={() => setErrored(true)}
      className="mx-auto block h-auto max-h-[80vh] w-auto max-w-full rounded-md object-contain"
    />
  );
}
