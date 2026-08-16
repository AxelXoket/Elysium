import { memo, useEffect, useRef, useState } from "react";
import type { Attachment, Message } from "@/lib/schemas/chats";
import { FadeIn } from "@/components/motion/FadeIn";
import { VariantCarousel } from "@/components/motion/VariantCarousel";
import { MessageText } from "./MessageText";
import { SpeakButton } from "./SpeakButton";
import { SpeakLiveButton } from "./SpeakLiveButton";
import { CopyMessageButton } from "./CopyMessageButton";
import { useDeleteMessageAndFollowing } from "@/lib/query/chats";
import {
  canRegenerateMessage,
  isMessageActive,
  parseServerDate,
  serverDateTimeAttr,
} from "@/lib/chat";
import { useUiStore } from "@/lib/store/uiStore";
import { bubbleSurface } from "@/lib/appearance/bubbleSurface";
import { imageUrl } from "@/lib/api/uploads";
import { ImageLightbox } from "./ImageLightbox";
import {
  Loader2,
  Trash2,
  Pencil,
  ImageOff,
  ChevronLeft,
  ChevronRight,
  CornerDownLeft,
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
  /** Called with (messageId, newText) when a user-message edit is saved -
   * the tail is discarded and the assistant rewrites (v1.1 C3). */
  onEditMessage?: (messageId: number, newText: string) => void;
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
  onEditMessage,
  regenerating,
  pendingForChat,
  streamingText,
  isStreamingTarget = false,
}: MessageBubbleProps) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editDraft, setEditDraft] = useState("");
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
  const msgOpacity = useUiStore((s) => s.msgOpacity);
  const deleteMessage = useDeleteMessageAndFollowing();
  const isUser = message.role === "user";
  const isPersisted = message.id > 0;
  const canRegenerate = canRegenerateMessage(messages, message);
  const isBusy = deleteMessage.isPending || Boolean(pendingForChat);
  const siblings = group ?? [message];
  const displayIndex = Math.max(
    0,
    siblings.findIndex((m) => m.id === message.id),
  );
  const variantCount = siblings.length;
  const atNewest = displayIndex === variantCount - 1;

  // I1 (v1.1): older groups (not the last active group) are VIEW-ONLY -
  // arrows page a LOCAL index and never touch the persisted active flag.
  // Activating a non-last group would desync the visible chain from the
  // provider history (context assembly follows `active`), so the backend
  // keeps its 409 and this UI never calls /activate for such groups.
  const isViewOnly = !isUser && isPersisted && !canRegenerate && variantCount > 1;
  const [viewIndex, setViewIndex] = useState<number | null>(null);
  const shownIndex = isViewOnly
    ? Math.min(viewIndex ?? displayIndex, variantCount - 1)
    : displayIndex;
  const shownMessage = siblings[shownIndex] ?? message;

  /** A persisted user message with nothing after it - so nobody answered it. */
  const awaitingReply =
    isUser &&
    isPersisted &&
    !pendingForChat &&
    messages.every((m) => m.id <= message.id || !isMessageActive(m));

  // From shownMessage, NOT from message. `message` is the group's ACTIVE row
  // while shownMessage is the row the reader has actually paged to, and every
  // other visible field (text, timestamp, the Speak target) already comes from
  // shownMessage. Reading attachments from the active row instead put one
  // variant's picture above another variant's words, and opened the wrong image
  // in the lightbox. Latent while only user rows had attachments - user rows
  // never form variant groups - and reachable the moment a reply can carry a
  // generated picture.
  //
  // Parsed rows always carry the array (schema default); optimistic cache
  // entries may omit it - treat missing as empty.
  const attachments = shownMessage.attachments ?? [];

  // Arrows live on the last active group (regenerate + activate) OR an older
  // group in view-only paging mode. Groups with a single variant show nothing.
  const showNav = !isUser && isPersisted && (canRegenerate || isViewOnly);
  const showCounter = variantCount > 1 || isStreamingTarget;


  // FF10 a11y for the delete confirm: refs for autofocus (destructive
  // button, like ChatList's inline confirm), Escape-close with focus return,
  // and outside-click close.
  const deleteTriggerRef = useRef<HTMLButtonElement>(null);
  const confirmPanelRef = useRef<HTMLDivElement>(null);
  const confirmDeleteButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!confirmDelete) return;
    confirmDeleteButtonRef.current?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        // The Escape that closes this panel must not ALSO reach Composer's
        // window-level "Escape = stop generating" binding. document bubbles to
        // window, so a single press dismissed the confirm box and killed the
        // reply that was streaming behind it. The edit textarea's handler two
        // hundred lines down already had this; the confirm panel did not.
        event.stopPropagation();
        event.preventDefault();
        setConfirmDelete(false);
        deleteTriggerRef.current?.focus();
      }
    };
    const handlePointerDown = (event: PointerEvent) => {
      const panel = confirmPanelRef.current;
      if (
        event.target instanceof Node &&
        !(panel && panel.contains(event.target)) &&
        event.target !== deleteTriggerRef.current
      ) {
        setConfirmDelete(false);
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("pointerdown", handlePointerDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [confirmDelete]);

  const handleDelete = () => {
    deleteMessage.mutate(
      { chatId, messageId: message.id },
      // onSettled, not onSuccess (v1.1 D2): a 404 must ALSO close the panel -
      // the mutation's own onError already dropped the ghost row from the
      // cache, and a panel stuck open on a vanished bubble was the reported
      // "kargacık burgacık" end state.
      { onSettled: () => setConfirmDelete(false) },
    );
  };

  // ── Inline edit (v1.1 C3): user rows only, composer conventions ─────────
  const editTextareaRef = useRef<HTMLTextAreaElement>(null);

  const startEditing = () => {
    setConfirmDelete(false);
    setEditDraft(message.content);
    setEditing(true);
  };

  // Focus + select on entry; auto-grow to fit the existing text.
  useEffect(() => {
    if (!editing) return;
    const ta = editTextareaRef.current;
    if (ta) {
      ta.focus();
      ta.select();
      ta.style.height = "auto";
      ta.style.height = `${ta.scrollHeight}px`;
    }
  }, [editing]);

  /**
   * Whether a save can actually land right now.
   *
   * The Edit TRIGGER is disabled while the chat is busy or no model is
   * selected - but the box can also become un-savable while it is already
   * open (a regenerate started from another bubble, a second edit box saved
   * first, the model deselected). saveEdit used to close the box first and
   * discover that afterwards: startEdit early-returns with no error and no
   * callback, handleEditMessage bails on a missing model, so the retyped text
   * was destroyed with no request, no toast and nothing to diagnose.
   */
  const editBlockedReason = !selectedModelId
    ? "Select a model to save this edit"
    : isBusy
      ? "Wait for the current reply to finish"
      : null;

  const saveEdit = () => {
    // Keep the box - and the retyped text - open when the save cannot be
    // dispatched. Closing first is what made the text vanish.
    if (editBlockedReason != null) return;
    const trimmed = editDraft.trim();
    setEditing(false);
    // Empty or unchanged → cancel silently (mirror of the rename rules).
    if (trimmed.length === 0 || trimmed === message.content) return;
    onEditMessage?.(message.id, trimmed);
  };

  const cancelEdit = () => setEditing(false);

  const handleEditInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setEditDraft(e.target.value);
    const ta = editTextareaRef.current;
    if (ta) {
      ta.style.height = "auto";
      ta.style.height = `${ta.scrollHeight}px`;
    }
  };

  const handleEditKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      saveEdit();
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      cancelEdit();
    }
  };

  const handleNext = () => {
    if (isViewOnly) {
      // View-only paging: local index, NO activate/regenerate.
      setDirection(1);
      setHasNavigated(true);
      setViewIndex(Math.min(shownIndex + 1, variantCount - 1));
      // The open lightbox belongs to the variant being left behind. Defence
      // only: the lightbox is a modal that removes the rest of the page from
      // the tree, so the arrows are unreachable while it is open and this
      // cannot fire today. It is one line, and it stops the ordering from
      // mattering if the lightbox ever stops trapping.
      setLightboxAttachment(null);
      return;
    }
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
    if (isViewOnly) {
      setDirection(-1);
      setHasNavigated(true);
      setViewIndex(Math.max(shownIndex - 1, 0));
      setLightboxAttachment(null);
      return;
    }
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
  const prevDisabled = isViewOnly
    ? shownIndex === 0
    : !isStreamingTarget && (displayIndex === 0 || isBusy);
  const nextDisabled = isViewOnly
    ? shownIndex === variantCount - 1
    : isStreamingTarget || isBusy || (atNewest && !selectedModelId);
  const nextTitle = isViewOnly
    ? "Next reply"
    : isStreamingTarget
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
    : String(shownIndex);
  const paneText = streamingText ?? shownMessage.content;
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
          } ${isPersisted ? "has-actions" : ""} ${editing ? "is-editing" : ""}`}
          // How much room the absolutely positioned action row needs. An
          // UPPER BOUND on purpose: SpeakButton decides internally whether to
          // render, so an exact count is not visible from here, and guessing
          // high only wraps the text a little early while guessing low puts
          // buttons on top of words. `has-actions` alone reserved a fixed
          // 4.35rem, which fitted two - the third button has been overflowing
          // by about 19px since before Copy existed.
          data-actions={
            isPersisted
              ? 1 + // delete
                (editing ? 0 : 1) + // copy
                (isUser ? 0 : 1) + // speak
                (isUser && onEditMessage != null && !editing ? 1 : 0) + // edit
                (isUser && awaitingReply && onEditMessage != null && !editing
                  ? 1
                  : 0) // get a reply
              : undefined
          }
          style={
            // v1.1 E2: layered surface vars. A contrast preset sets --msg-*;
            // Default sets nothing, so the fallbacks reproduce today's pixels
            // bit-for-bit (zero-change contract). box-shadow stays owned by
            // chat-bg-dark, never the presets (orthogonality).
            // The SURFACE takes the opacity, never the element: `opacity` on
            // the bubble would fade the words with it, and text you cannot
            // read is not a translucency setting. `color-mix` thins the fill
            // and leaves `color` untouched, so the ink stays solid at every
            // setting - see bubbleSurface.
            isUser
              ? {
                  backgroundColor: bubbleSurface(
                    "var(--msg-user-bg, var(--color-es-user-bubble))",
                    msgOpacity,
                  ),
                  color: "var(--msg-user-fg, var(--color-es-user-bubble-text))",
                  borderBottomRightRadius: "2px",
                  boxShadow: "var(--shadow-bubble)",
                }
              : {
                  backgroundColor: bubbleSurface(
                    "var(--msg-asst-bg, var(--color-es-asst-bubble))",
                    msgOpacity,
                  ),
                  color: "var(--msg-asst-fg, var(--color-es-asst-bubble-text))",
                  borderBottomLeftRadius: "2px",
                  boxShadow: "var(--shadow-bubble)",
                }
          }
        >
          {isPersisted && (
            <div className="message-actions" aria-label="Message actions">
              {/* V5: hear this reply. Renders only when a voice model is
                  selected; speaks by message_id so the raw delivery tags -
                  stripped from the visible text - reach the engine. */}
              {/* KÖK 15: while a NEW variant streams into this bubble the
                  pane shows streamingText, but shownMessage.id is still the
                  OLD row - so Speak read out text B while the user was
                  reading text A, with nothing to say they differed. The live
                  button speaks what is actually arriving, and it is also the
                  alternative regenerate never had (SpeakLiveButton lived only
                  in StreamingBubble, which send and edit render but
                  regenerate does not). */}
              {!isUser &&
                (isStreamingTarget ? (
                  <SpeakLiveButton chatId={chatId} />
                ) : (
                  <SpeakButton messageId={shownMessage.id} />
                ))}
              {/* Copy sits before the writers and well away from Delete: it
                  is the only read-only action here, and putting it beside a
                  destructive one invites the misclick. It takes paneText,
                  the string on screen, for the reason spelled out in
                  CopyMessageButton - the active row and the shown row are
                  not the same message while variants are being browsed. */}
              {!editing && (
                <CopyMessageButton
                  // `showDots` is the window between "a new variant started
                  // streaming" and its first delta: the bubble shows dots,
                  // but paneText is still the PREVIOUS variant in full.
                  // Handing that over would copy a reply the reader is not
                  // looking at - the exact shape of KÖK 15, which the Speak
                  // button next to it already had to be rescued from.
                  text={showDots ? "" : paneText}
                  isUser={isUser}
                />
              )}
              {/* Deleting a reply leaves its question standing with nothing
                  after it, and no way to ask again: the regenerate arrow lives
                  on the assistant bubble, which is the row that was just
                  deleted, and the composer's Send needs new text - so an empty
                  composer reads as a dead button. The only route back was to
                  press Edit and Save without changing a word, which is a trick
                  rather than an affordance.

                  It calls the edit endpoint with the text UNCHANGED, which is
                  exactly what that trick did: the backend only requires the row
                  to be a user row, and with nothing after it there is nothing
                  to sweep - it just writes a fresh reply. */}
              {isUser && awaitingReply && onEditMessage != null && !editing && (
                <button
                  type="button"
                  className="message-action-button"
                  aria-label="Get a reply"
                  title={
                    selectedModelId
                      ? "Get a reply to this message"
                      : "Select a model to get a reply"
                  }
                  onClick={() => onEditMessage(message.id, message.content)}
                  disabled={isBusy || !selectedModelId}
                >
                  <CornerDownLeft size={13} />
                </button>
              )}
              {isUser && onEditMessage != null && !editing && (
                <button
                  type="button"
                  className="message-action-button"
                  aria-label="Edit message"
                  // v1.1 audit L1: gate on a selected model like send/regenerate.
                  // An edit rewrites the following reply, so with no model the
                  // save is a no-op (handleEditMessage bails) - disabling here
                  // stops the user's retyped text from vanishing silently.
                  title={
                    selectedModelId
                      ? "Edit message (the reply after it is rewritten)"
                      : "Select a model to edit"
                  }
                  onClick={startEditing}
                  disabled={isBusy || !selectedModelId}
                >
                  <Pencil size={13} />
                </button>
              )}
              <button
                ref={deleteTriggerRef}
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
            editing ? (
              <div className="message-edit-area">
                <textarea
                  ref={editTextareaRef}
                  value={editDraft}
                  onChange={handleEditInput}
                  onKeyDown={handleEditKeyDown}
                  aria-label="Edit message text"
                  rows={1}
                  className="message-edit-textarea"
                />
                <div className="mt-2 flex justify-end gap-1.5">
                  <button
                    type="button"
                    className="inline-confirm-button"
                    onClick={cancelEdit}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="inline-confirm-button"
                    onClick={saveEdit}
                    disabled={
                      editDraft.trim().length === 0 || editBlockedReason != null
                    }
                    title={
                      editBlockedReason ??
                      "Save and rewrite the reply (Enter)"
                    }
                  >
                    Save
                  </button>
                </div>
              </div>
            ) : (
              <p className="message-text whitespace-pre-wrap break-words">
                <MessageText text={message.content} />
              </p>
            )
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
                      className="msg-chrome"
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
              className="msg-chrome block text-[9px] opacity-70"
              dateTime={serverDateTimeAttr(shownMessage.created_at)}
            >
              {parseServerDate(shownMessage.created_at).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </time>
            {showCounter && (
              <span
                className="variant-counter"
                aria-live="polite"
                aria-label={`Reply ${
                  isStreamingTarget ? variantCount + 1 : shownIndex + 1
                } of ${isStreamingTarget ? variantCount + 1 : variantCount}`}
              >
                {isStreamingTarget
                  ? `${variantCount + 1}/${variantCount + 1}`
                  : `${shownIndex + 1}/${variantCount}`}
              </span>
            )}
          </span>

          {confirmDelete && (
            <div
              ref={confirmPanelRef}
              className="message-action-confirm"
              role="dialog"
              aria-label="Confirm delete message"
            >
              <p>Delete this message and everything after it?</p>
              <div className="mt-2 flex justify-end gap-1.5">
                <button
                  type="button"
                  className="inline-confirm-button"
                  onClick={() => {
                    setConfirmDelete(false);
                    deleteTriggerRef.current?.focus();
                  }}
                  disabled={isBusy}
                >
                  Cancel
                </button>
                <button
                  ref={confirmDeleteButtonRef}
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

/** Thumbnail box caps. The box is computed from the attachment's REAL
 * metadata so layout height is right before a single byte loads. */
const THUMB_MAX_H = 200;
const THUMB_MAX_W = 320;

/**
 * One attachment thumbnail that opens the lightbox on click. Falls back to a
 * graceful "image unavailable" placeholder if the binary 404s (a real backend
 * state, attachment_not_found) instead of the browser's broken-image glyph.
 *
 * Reserved box (v1.1 A1): the old `h-auto w-auto max-h-[200px]` classes
 * OVERRODE the width/height attributes, so an unloaded img laid out at ~0px -
 * the send-time scrollTo measured a short list and the page then grew under
 * the user (~208px per image) with no follow-up scroll. Explicit style
 * width/height reserves the final box up front; the error placeholder uses
 * the SAME box so an error swap cannot shift layout either. Kills the
 * image-CLS class of bugs wholesale.
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
  const scale = Math.min(
    1,
    THUMB_MAX_H / attachment.height,
    THUMB_MAX_W / attachment.width,
  );
  const boxW = Math.round(attachment.width * scale);
  const boxH = Math.round(attachment.height * scale);

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
          className="flex flex-col items-center justify-center gap-1.5 rounded-xl px-2 text-center"
          style={{
            width: boxW,
            height: boxH,
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
          style={{ width: boxW, height: boxH }}
          loading="lazy"
          decoding="async"
          onError={() => setErrored(true)}
          className="block max-w-full rounded-xl object-contain"
        />
      )}
    </button>
  );
}
