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
import { useDraftStore, editDraftKey } from "@/lib/store/draftStore";
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
  Scissors,
} from "lucide-react";

/**
 * Visible text for the truncation mark, exported so its test imports this
 * instead of retyping the word - the same reason COPY_FEEDBACK_MS is
 * exported by CopyMessageButton. The word IS the accessible name (a bare
 * span with no aria-label): the icon next to it is `aria-hidden`, so a
 * screen reader gets the text, never colour or a glyph alone.
 */
export const TRUNCATED_MARK_TEXT = "Truncated";

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
  const [confirmEdit, setConfirmEdit] = useState(false);
  // The edit buffer lives in the process-lifetime draft store, not here. As
  // component state it died with the component, and VaultGate unmounts the
  // whole app subtree when the vault locks - so a half-retyped question was
  // destroyed by locking the app, silently, with no way to get it back.
  //
  // The ENTRY'S EXISTENCE is what holds the box open, which is why there is
  // no separate `editing` flag: a box that was open before a remount is a
  // buffer that still exists after it, and the two can never disagree.
  const draftKey = editDraftKey(chatId, message.id);
  // Selected as primitives so this memo(MessageBubble) re-renders on its own
  // keystrokes and on nothing else - an object selector would re-render every
  // bubble in the chat whenever any other bubble's buffer changed.
  const editDraft = useDraftStore((s) => s.edits[draftKey]?.text) ?? "";
  const editPhase = useDraftStore((s) => s.edits[draftKey]?.phase);
  const editing = editPhase === "editing";
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
  // The edit box takes its metrics from the reader's own settings (--msg-fs/
  // --msg-lh, hoisted onto <main> by ChatCanvas), so a change to either one
  // invalidates the height this bubble measured. Subscribed here purely to
  // re-run the sizing effect below; the values themselves are never read.
  const msgFontPx = useUiStore((s) => s.msgFontPx);
  const msgLineHeight = useUiStore((s) => s.msgLineHeight);
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

  // The same three behaviours the delete panel above needs, for the edit
  // confirmation (K-27): focus the destructive button, let Escape answer this
  // panel WITHOUT reaching Composer's window-level "stop generating", and let a
  // click outside dismiss it. Written out rather than shared with the block
  // above because the two differ in what they focus on the way out and in what
  // "outside" means - a shared version would need both of those as parameters
  // and would be longer than the duplication.
  useEffect(() => {
    if (!confirmEdit) return;
    editConfirmButtonRef.current?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        event.preventDefault();
        setConfirmEdit(false);
        editSaveTriggerRef.current?.focus();
      }
    };
    const handlePointerDown = (event: PointerEvent) => {
      const panel = editConfirmPanelRef.current;
      if (
        event.target instanceof Node &&
        !(panel && panel.contains(event.target)) &&
        event.target !== editSaveTriggerRef.current
      ) {
        setConfirmEdit(false);
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("pointerdown", handlePointerDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [confirmEdit]);

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
  const editConfirmButtonRef = useRef<HTMLButtonElement>(null);
  const editSaveTriggerRef = useRef<HTMLButtonElement>(null);
  const editConfirmPanelRef = useRef<HTMLDivElement>(null);

  /**
   * Rows a save would destroy, counted the way the SERVER counts them (K-27).
   *
   * The backend sweeps `DELETE FROM messages WHERE chat_id = ? AND id > ?`,
   * which takes inactive variant siblings with it. `messages` is the raw list
   * for exactly this reason - it is not filtered by `active` - so a count taken
   * from it matches what actually goes, and a reply with three takes counts as
   * three. `awaitingReply` above deliberately ignores inactive rows; this must
   * NOT be derived from that, or the number shown would be smaller than the
   * number deleted.
   *
   * Optimistic ids are negative and therefore below `message.id`, and the
   * pencil is disabled while the chat is busy, so nothing in flight is counted.
   */
  const followingRowCount = messages.filter((m) => m.id > message.id).length;

  // True only for the box THIS mounted bubble opened by pencil press. A box
  // that is on screen because a buffer survived a remount is a restore, and
  // the two must not be treated alike (see the effect below).
  const openedByPencilRef = useRef(false);

  const startEditing = () => {
    setConfirmDelete(false);
    setConfirmEdit(false);
    openedByPencilRef.current = true;
    // Opening the box IS creating the buffer. A refusal here (the text is
    // past a memory ceiling) raises its own toast and leaves the box shut
    // rather than opening an edit that could not be typed into.
    useDraftStore
      .getState()
      .openEditDraft(chatId, message.id, message.content);
  };

  /**
   * Size the box on entry, and select its text ONLY when the user just asked
   * for it.
   *
   * The select-all is right for a pencil press: the box opens holding the
   * message's current words and the usual next act is to replace them. It is
   * catastrophic for a RESTORE. When a buffer survives a vault lock or a
   * failed save, this effect runs on mount with the box already open, and a
   * bare `ta.select()` would highlight the whole recovered draft - so the
   * user's first keystroke replaces the very text this store exists to save,
   * and the textarea is controlled so the browser's own undo cannot bring it
   * back. Restoring also must not steal focus: the box may be halfway down a
   * long list, and yanking the caret into it fights the scroll restore and
   * moves the user somewhere they did not ask to be.
   */
  useEffect(() => {
    if (!editing) return;
    const ta = editTextareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${ta.scrollHeight}px`;
    if (!openedByPencilRef.current) return;
    openedByPencilRef.current = false;
    ta.focus();
    ta.select();
  }, [editing]);

  /**
   * Re-measure when the text changes from outside a keystroke.
   *
   * `handleEditInput` sizes the box from the DOM before the store has
   * accepted the write, so a refused write (over a memory ceiling) leaves the
   * textarea at the height of text it is no longer showing. The Composer has
   * carried the same effect for the same reason since it became controlled.
   */
  useEffect(() => {
    const ta = editTextareaRef.current;
    if (!editing || !ta) return;
    ta.style.height = "auto";
    ta.style.height = `${ta.scrollHeight}px`;
    // msgFontPx/msgLineHeight are in the deps and nowhere in the body on
    // purpose. The box used to inherit a fixed `text-sm leading-relaxed` from
    // the shell, so the reader's settings could not change its metrics and a
    // pinned height stayed correct forever. It now follows --msg-fs/--msg-lh,
    // so moving either slider while the box is OPEN reflows the text under a
    // stale height - and with `resize: none` + `overflow: hidden` on the
    // textarea the overflow is clipped with no scrollbar and no handle to
    // recover it. The Composer has carried the same dependency since v1.1 E3
    // (Composer.tsx:129-139) for the same reason.
  }, [editing, editDraft, msgFontPx, msgLineHeight]);

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

  /** Send the edit. Only reached once there is nothing left to ask about. */
  const commitEdit = () => {
    // The SAME precondition saveEdit opens with. The confirmation dialog's
    // "Save and delete" calls this function directly, so checking only in
    // saveEdit left the one path that skips it: the box would close, the
    // buffer would go to `committing`, and handleEditMessage would bail on a
    // missing model without ever answering - stranding the retyped text in a
    // phase that renders nothing.
    if (editBlockedReason != null) return;
    const trimmed = editDraft.trim();
    setConfirmEdit(false);
    const drafts = useDraftStore.getState();
    if (trimmed.length === 0 || trimmed === message.content) {
      // Nothing to send, so nothing can fail: the buffer has no further job.
      drafts.clearEditDraft(chatId, message.id);
      return;
    }
    // Close the box but KEEP the text: the save can still fail, be aborted,
    // or be refused by a stream that already owns the chat, and each of
    // those has to give the user their sentence back. ChatCanvas clears the
    // buffer on success and reopens it on anything else.
    drafts.commitEditDraft(chatId, message.id);
    onEditMessage?.(message.id, trimmed);
  };

  const saveEdit = () => {
    // Keep the box - and the retyped text - open when the save cannot be
    // dispatched. Closing first is what made the text vanish.
    if (editBlockedReason != null) return;
    const trimmed = editDraft.trim();
    // Empty or unchanged → cancel silently (mirror of the rename rules).
    // Checked BEFORE the confirmation: a save that changes nothing destroys
    // nothing, so asking about it would be a dialog for a no-op.
    if (trimmed.length === 0 || trimmed === message.content) {
      useDraftStore.getState().clearEditDraft(chatId, message.id);
      return;
    }
    // K-27. Saving an edit deletes every row after this one, permanently -
    // no soft delete, no undo, and the only warning was a tooltip that said
    // "the reply", singular, while a dozen were about to go. The confirmation
    // is HERE rather than on the pencil because opening the box is not the
    // destructive act; and only above one row, because rewriting the last
    // question really does rewrite one reply and a dialog for that would be
    // the habit-forming kind that gets clicked through.
    //
    // The buffer is deliberately left in its `editing` phase on this path:
    // cancelling the confirmation has to leave the box and the retyped text
    // exactly where they were, which is the same lesson the blocked-save
    // branch above is written around.
    if (followingRowCount > 1) {
      setConfirmEdit(true);
      return;
    }
    commitEdit();
  };

  const cancelEditConfirm = () => {
    setConfirmEdit(false);
    editSaveTriggerRef.current?.focus();
  };

  const cancelEdit = () => {
    setConfirmEdit(false);
    // Cancel is the user saying "throw this away", which is the ONE thing
    // that discards a draft outright. Everything else keeps it.
    useDraftStore.getState().clearEditDraft(chatId, message.id);
  };

  const handleEditInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    useDraftStore.getState().setEditDraft(chatId, message.id, e.target.value);
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
      // With the confirmation up, Escape answers the QUESTION, not the box.
      // The panel takes focus when it opens, so this branch is for the case
      // where focus has gone back to the textarea - without it, Escape would
      // close the edit and leave an unattached dialog on screen.
      if (confirmEdit) cancelEditConfirm();
      else cancelEdit();
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
                  // A PERSISTED row copies its whole content, not the paced
                  // prefix. "Copy what is on screen" was written for the
                  // transient bubble, where no row exists to copy; once the
                  // reply is saved and only the typewriter is still catching
                  // up, handing over a half-typed string would copy less than
                  // the reader can already see finishing in front of them.
                  // `showDots` stays FIRST: during a regenerate's
                  // pre-first-delta window the row is persisted too, and
                  // paneText is the PREVIOUS variant in full (KOK 15).
                  text={
                    showDots ? "" : isPersisted ? shownMessage.content : paneText
                  }
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
                      ? followingRowCount > 1
                        ? `Edit message (the ${followingRowCount} messages after it are deleted)`
                        : "Edit message (the reply after it is rewritten)"
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
                    ref={editSaveTriggerRef}
                    type="button"
                    className="inline-confirm-button"
                    onClick={saveEdit}
                    disabled={
                      editDraft.trim().length === 0 || editBlockedReason != null
                    }
                    title={
                      editBlockedReason ??
                      (followingRowCount > 1
                        ? `Save (asks first - ${followingRowCount} messages would be deleted)`
                        : "Save and rewrite the reply (Enter)")
                    }
                  >
                    Save
                  </button>
                </div>
                {/*
                  K-27. Below the box rather than over it: the panel this is
                  modelled on hangs at `top: 2.25rem` where the action strip
                  sits, and that strip is hidden while editing - so the same
                  placement here would cover the sentence the reader is being
                  asked about. Everything else is the delete panel's, class for
                  class, so it inherits the reduced-motion and
                  reduced-transparency lists it is already named in.

                  The label is distinct from "Confirm delete message" on
                  purpose: both panels are `role="dialog"` inside the same
                  message list, and a test that queried by role alone could
                  match the wrong one and still pass.
                */}
                {confirmEdit && (
                  <div
                    ref={editConfirmPanelRef}
                    className="message-action-confirm mt-2 is-inline"
                    role="dialog"
                    aria-label="Confirm rewriting this message"
                  >
                    <p>
                      {`Saving this deletes the ${followingRowCount} messages after it. This cannot be undone.`}
                    </p>
                    <div className="mt-2 flex justify-end gap-1.5">
                      {/*
                        "Go back", not "Cancel". The edit box's own Cancel is
                        still on screen right above this, and two buttons
                        reading Cancel a centimetre apart - one abandoning the
                        edit, one only closing this question - is a choice
                        nobody should have to work out under a warning about
                        permanent deletion. Found because a test could not tell
                        them apart either.
                      */}
                      <button
                        type="button"
                        className="inline-confirm-button"
                        onClick={cancelEditConfirm}
                      >
                        Go back
                      </button>
                      <button
                        ref={editConfirmButtonRef}
                        type="button"
                        className="inline-confirm-button is-danger"
                        onClick={commitEdit}
                      >
                        Save and delete
                      </button>
                    </div>
                  </div>
                )}
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
            {/* Owner's ask, verbatim: a mark bottom-left, not colliding with
                anything already there. This row IS the bubble's bottom-left
                corner - the timestamp already lives here alone, so a sibling
                flex item extends the row instead of overlapping it. Checked
                against every other thing that can render on an assistant
                bubble: .message-actions (top-right, hover-only), the delete
                confirm dialog (`.message-action-confirm`, absolute but
                anchored at top: 2.25rem - the top strip, not here), the
                variant-nav chevrons (outside the bubble entirely, siblings
                in the outer flex row), and the attachment thumbnails (above
                the text, not below it). None of them touch this row.

                No new class, colour or size: `msg-chrome` (user-select:none,
                already shared with the timestamp), the same 9px/opacity-70
                pairing the timestamp uses, and the 13px icon size every
                other action glyph in this file already uses. Assistant-only
                - a token ceiling only ever cuts off a GENERATED reply - and
                read from shownMessage so paging to an older variant shows
                THAT variant's own truncation state, not the active row's. */}
            {!isUser && shownMessage.truncated && (
              <span
                className="msg-chrome flex items-center gap-1 text-[9px] opacity-70"
                title="This reply was cut off by the token limit"
              >
                <Scissors size={13} aria-hidden="true" />
                {TRUNCATED_MARK_TEXT}
              </span>
            )}
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
