import { memo, useState } from "react";
import type { Attachment, Message } from "@/lib/schemas/chats";
import { FadeIn } from "@/components/motion/FadeIn";
import { VariantCarousel } from "@/components/motion/VariantCarousel";
import { MessageText } from "./MessageText";
import { useDeleteMessageAndFollowing } from "@/lib/query/chats";
import { canRegenerateMessage } from "@/lib/chat";
import { useUiStore } from "@/lib/store/uiStore";
import { imageUrl } from "@/lib/api/uploads";
import { ImageLightbox } from "./ImageLightbox";
import {
  Loader2,
  Trash2,
  ImageOff,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";

interface MessageBubbleProps {
  chatId: number;
  /** The displayed row of its variant group (the active one). */
  message: Message;
  /** Full raw message list (variant siblings included) - for eligibility. */
  messages: Message[];
  /** All rows of this message's variant group, id ASC (length 1 = no swipes). */
  group?: Message[];
  /** Called with the message id when the user asks for a NEW variant (right
   * arrow on the newest). The payload is assembled by ChatCanvas. */
  onRegenerate?: (messageId: number) => void;
  /** Called with a sibling row id to make it the active variant. */
  onActivateVariant?: (messageId: number) => void;
  /** Aborts the in-flight variant generation (left arrow during streaming). */
  onAbortGeneration?: () => void;
  /** True when a regenerate for this chat is in flight (spinner). */
  regenerating?: boolean;
  /** True when a send or regenerate for this chat is in flight - mutual
   * exclusion for message actions within the chat. */
  pendingForChat?: boolean;
  /** When set, this bubble renders the streaming text (with a cursor)
   * instead of its stored content - a new variant is generating in place. */
  streamingText?: string | null;
  /** True while a regenerate targets THIS bubble's group (covers the
   * pre-first-delta window where streamingText is still null). */
  isStreamingTarget?: boolean;
}

// memo: during streaming the list re-renders every animation frame (the
// streaming entry changes per rAF flush). Message rows and their props are
// referentially stable across those flushes, so every bubble except the
// regenerate target can skip its render entirely - without this, long chats
// re-render every bubble per frame and the stream visibly stutters.
export const MessageBubble = memo(function MessageBubble({
  chatId,
  message,
  messages,
  group,
  onRegenerate,
  onActivateVariant,
  onAbortGeneration,
  regenerating,
  pendingForChat,
  streamingText,
  isStreamingTarget = false,
}: MessageBubbleProps) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [lightboxAttachment, setLightboxAttachment] = useState<Attachment | null>(
    null,
  );
  // Direction of the LAST arrow press - drives the carousel slide. Captured
  // in state at press time so rapid mashing can't retarget a wrong way.
  const [direction, setDirection] = useState<1 | -1>(1);
  // Panes only slide once the user has actually pressed an arrow - plain
  // mounts (chat open, refetch) must not play an entrance shift.
  const [hasNavigated, setHasNavigated] = useState(false);
  const selectedModelId = useUiStore((s) => s.selectedModelId);
  const deleteMessage = useDeleteMessageAndFollowing();
  const isUser = message.role === "user";
  const isPersisted = message.id > 0;
  const canRegenerate = canRegenerateMessage(messages, message);
  const isBusy = deleteMessage.isPending || Boolean(pendingForChat);
  // Parsed rows always carry the array (schema default); optimistic cache
  // entries may omit it - treat missing as empty.
  const attachments = message.attachments ?? [];

  const siblings = group ?? [message];
  const displayIndex = Math.max(
    0,
    siblings.findIndex((m) => m.id === message.id),
  );
  const variantCount = siblings.length;
  const atNewest = displayIndex === variantCount - 1;
  // Arrows live on the bubble that can grow new variants: the last active
  // group. Older groups show only a static counter when they hold variants.
  const showNav = !isUser && isPersisted && canRegenerate;
  const showCounter = variantCount > 1 || isStreamingTarget;


  const handleDelete = () => {
    deleteMessage.mutate(
      { chatId, messageId: message.id },
      { onSuccess: () => setConfirmDelete(false) },
    );
  };

  const handleNext = () => {
    if (isStreamingTarget || isBusy) return;
    setDirection(1);
    setHasNavigated(true);
    if (!atNewest) {
      onActivateVariant?.(siblings[displayIndex + 1].id);
    } else if (selectedModelId && canRegenerate) {
      onRegenerate?.(message.id);
    }
  };

  const handlePrev = () => {
    setDirection(-1);
    setHasNavigated(true);
    if (isStreamingTarget) {
      // Escape hatch: abort the generation and fall back to the stored
      // variant - the pane naturally flips back.
      onAbortGeneration?.();
      return;
    }
    if (isBusy) return;
    if (displayIndex > 0) {
      onActivateVariant?.(siblings[displayIndex - 1].id);
    }
  };

  // The Previous button is ALWAYS rendered when nav shows (disabled at the
  // left edge) - unmounting it would drop keyboard focus to <body> mid-
  // navigation and shift the bubble sideways as the flex sibling appears.
  const prevDisabled = !isStreamingTarget && (displayIndex === 0 || isBusy);
  const nextDisabled =
    isStreamingTarget || isBusy || (atNewest && !selectedModelId);
  const nextTitle = isStreamingTarget
    ? "Generating…"
    : !atNewest
      ? "Next reply"
      : selectedModelId
        ? "Generate a new reply"
        : "Select a model to generate";

  // Pane key = position within the group, NOT the row id. The streaming pane
  // takes the position the new variant will land on, so when the stream
  // settles into the persisted row the key does not change - identical text
  // never re-animates. Arrow navigation changes the position → slide.
  const paneKey = isStreamingTarget
    ? String(variantCount)
    : String(displayIndex);
  const paneText = streamingText ?? message.content;
  const showDots = isStreamingTarget && streamingText == null;

  return (
    <FadeIn duration={0.15}>
      <div
        className={`flex items-center gap-1.5 ${
          isUser ? "justify-end" : "justify-start"
        }`}
      >
        {showNav && (
          <button
            type="button"
            className="variant-nav-button"
            aria-label={
              isStreamingTarget
                ? "Stop and return to the previous reply"
                : "Previous reply"
            }
            title={
              isStreamingTarget
                ? "Stop and return to the previous reply"
                : "Previous reply"
            }
            onClick={handlePrev}
            disabled={prevDisabled}
          >
            <ChevronLeft size={14} />
          </button>
        )}

        <div
          className={`message-bubble-shell max-w-[75%] rounded-xl px-5 py-3 text-sm leading-relaxed ${
            isUser ? "is-user" : "is-assistant"
          } ${isPersisted ? "has-actions" : ""}`}
          style={
            isUser
              ? {
                  backgroundColor: "var(--color-es-user-bubble)",
                  color: "var(--color-es-user-bubble-text)",
                  borderBottomRightRadius: "2px",
                  boxShadow: "var(--shadow-bubble)",
                }
              : {
                  backgroundColor: "var(--color-es-asst-bubble)",
                  color: "var(--color-es-asst-bubble-text)",
                  borderBottomLeftRadius: "2px",
                  boxShadow: "var(--shadow-bubble)",
                }
          }
        >
          {isPersisted && (
            <div className="message-actions" aria-label="Message actions">
              <button
                type="button"
                className="message-action-button is-danger"
                aria-label="Delete message"
                title="Delete message and following"
                onClick={() => setConfirmDelete(true)}
                disabled={isBusy}
              >
                {deleteMessage.isPending ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <Trash2 size={13} />
                )}
              </button>
            </div>
          )}

          {attachments.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-2">
              {attachments.map((attachment, index) => (
                <AttachmentThumbnail
                  key={attachment.id}
                  attachment={attachment}
                  index={index + 1}
                  total={attachments.length}
                  onOpen={setLightboxAttachment}
                />
              ))}
            </div>
          )}

          {isUser ? (
            <p className="message-text whitespace-pre-wrap break-words">
              <MessageText text={message.content} />
            </p>
          ) : (
            <VariantCarousel
              paneKey={paneKey}
              direction={direction}
              animateEnter={hasNavigated}
            >
              {showDots ? (
                <span
                  className="flex items-center gap-2 py-1"
                  role="status"
                  aria-label="Generating a new reply"
                >
                  {[0, 1, 2].map((i) => (
                    <span
                      key={i}
                      className="thinking-dot inline-block h-2 w-2 rounded-full"
                      style={{
                        backgroundColor: "var(--color-es-asst-bubble-text)",
                      }}
                    />
                  ))}
                </span>
              ) : (
                <p className="message-text whitespace-pre-wrap break-words">
                  <MessageText
                    text={paneText}
                    streaming={streamingText != null}
                  />
                  {(streamingText != null || isStreamingTarget) && (
                    <span
                      aria-hidden="true"
                      style={{ opacity: 0.6, marginLeft: "1px" }}
                    >
                      {"▍"}
                    </span>
                  )}
                </p>
              )}
            </VariantCarousel>
          )}

          <span className="mt-1.5 flex items-center gap-2">
            <time
              className="block text-[9px] opacity-70"
              dateTime={message.created_at}
            >
              {new Date(message.created_at).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </time>
            {showCounter && (
              <span
                className="variant-counter"
                aria-live="polite"
                aria-label={`Reply ${
                  isStreamingTarget ? variantCount + 1 : displayIndex + 1
                } of ${isStreamingTarget ? variantCount + 1 : variantCount}`}
              >
                {isStreamingTarget
                  ? `${variantCount + 1}/${variantCount + 1}`
                  : `${displayIndex + 1}/${variantCount}`}
              </span>
            )}
          </span>

          {confirmDelete && (
            <div
              className="message-action-confirm"
              role="dialog"
              aria-label="Confirm delete message"
            >
              <p>Delete this message and everything after it?</p>
              <div className="mt-2 flex justify-end gap-1.5">
                <button
                  type="button"
                  className="inline-confirm-button"
                  onClick={() => setConfirmDelete(false)}
                  disabled={isBusy}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="inline-confirm-button is-danger"
                  onClick={handleDelete}
                  disabled={isBusy}
                >
                  {deleteMessage.isPending ? "Deleting..." : "Delete"}
                </button>
              </div>
            </div>
          )}
        </div>

        {showNav && (
          <button
            type="button"
            className="variant-nav-button"
            aria-label={nextTitle}
            title={nextTitle}
            onClick={handleNext}
            disabled={nextDisabled}
          >
            {isStreamingTarget || regenerating ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <ChevronRight size={14} />
            )}
          </button>
        )}
      </div>

      {attachments.length > 0 && (
        <ImageLightbox
          attachment={lightboxAttachment}
          onClose={() => setLightboxAttachment(null)}
        />
      )}
    </FadeIn>
  );
});

/**
 * One attachment thumbnail that opens the lightbox on click. Falls back to a
 * graceful "image unavailable" placeholder if the binary 404s (a real backend
 * state, attachment_not_found) instead of the browser's broken-image glyph.
 */
function AttachmentThumbnail({
  attachment,
  index,
  total,
  onOpen,
}: {
  attachment: Attachment;
  index: number;
  total: number;
  onOpen: (attachment: Attachment) => void;
}) {
  const [errored, setErrored] = useState(false);

  return (
    <button
      type="button"
      aria-label={`View attached image ${index} of ${total}`}
      title="View attached image"
      className="block cursor-zoom-in overflow-hidden rounded-xl"
      onClick={() => onOpen(attachment)}
    >
      {errored ? (
        <span
          className="flex h-[120px] w-[120px] flex-col items-center justify-center gap-1.5 rounded-xl px-2 text-center"
          style={{
            backgroundColor: "rgba(28, 38, 50, 0.06)",
            border: "1px solid rgba(28, 38, 50, 0.14)",
            color: "var(--color-es-asst-bubble-text)",
          }}
        >
          <ImageOff size={18} style={{ opacity: 0.5 }} />
          <span className="text-[10px]" style={{ opacity: 0.6 }}>
            Image unavailable
          </span>
        </span>
      ) : (
        <img
          src={imageUrl(attachment.id)}
          alt="attached image"
          width={attachment.width}
          height={attachment.height}
          loading="lazy"
          onError={() => setErrored(true)}
          className="block h-auto max-h-[200px] w-auto max-w-full rounded-xl object-contain"
        />
      )}
    </button>
  );
}
