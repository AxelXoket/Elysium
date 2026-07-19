import {
  useRef,
  useEffect,
  useState,
  useCallback,
  type CSSProperties,
} from "react";
import { useMutationState } from "@tanstack/react-query";
import { useUiStore } from "@/lib/store/uiStore";
import { useChatBackground } from "@/lib/appearance/useChatBackground";
import { useChats, useMessages, useActivateVariant } from "@/lib/query/chats";
import { messageAnchor } from "@/lib/chat";
import {
  SEND_MESSAGE_MUTATION_KEY,
  REGENERATE_MESSAGE_MUTATION_KEY,
} from "@/lib/query/completions";
import { useStreamingCompletion } from "@/lib/chat/useStreamingCompletion";
import { usePersonas } from "@/lib/query/personas";
import { useModels } from "@/lib/query/models";
import { getSelectedPersonaId, safePersonaId } from "@/lib/personas";
import { findModelById, hasInputModality } from "@/lib/models";
import { uploadImage } from "@/lib/api/uploads";
import { useErrorStore } from "@/lib/errors";
import { useGenerationSettings } from "@/components/generation/GenerationSettingsContext";
import { useReducedMotion } from "@/components/motion/ReducedMotion";
import { MessageList } from "./MessageList";
import { EmptyState } from "./EmptyState";
import { Composer } from "./Composer";
import {
  MAX_ATTACHMENTS,
  isAcceptedImageFile,
  createPreviewUrl,
  revokePreviewUrl,
} from "./attachments";
import type { StagedAttachment } from "./attachments";
import { ErrorToastStack } from "@/components/errors/ErrorToastStack";
import { CanvasMist } from "@/components/backdrop/MistCanvas";

/**
 * Chat ids of currently pending mutations for the given key.
 *
 * The selected-chat comparison happens in render (not inside the mutation
 * filter): useMutationState only recomputes on mutation cache events, so a
 * predicate closing over selectedChatId would go stale when the user switches
 * chats mid-request.
 */
function usePendingChatIds(mutationKey: readonly string[]): (number | undefined)[] {
  return useMutationState({
    filters: { mutationKey, status: "pending" },
    select: (mutation) =>
      (mutation.state.variables as { chatId?: number } | undefined)?.chatId,
  });
}

/** How long a failed upload thumbnail stays visible before auto-removal. */
const ATTACHMENT_ERROR_HIDE_MS = 1500;

// Stable empty list: `?? []` would mint a fresh array every render and
// silently defeat memo(Composer) whenever nothing is staged.
const EMPTY_STAGED: readonly StagedAttachment[] = [];

// Client-side identity for staged attachments - same module-counter pattern
// as nextOptimisticId in lib/query/completions.
let stagedKeyCounter = 0;
function nextStagedKey(): string {
  stagedKeyCounter += 1;
  return `staged_${stagedKeyCounter}`;
}

export function ChatCanvas() {
  const selectedChatId = useUiStore((s) => s.selectedChatId);
  const selectedModelId = useUiStore((s) => s.selectedModelId);
  // Reader preferences: message-body font/leading, applied as CSS variables
  // on the scroll container so ONLY .message-text consumers scale.
  const msgFontPx = useUiStore((s) => s.msgFontPx);
  const msgLineHeight = useUiStore((s) => s.msgLineHeight);
  // Chat wallpaper: layers + adaptive-chrome flag. Only painted while a chat
  // is open (EmptyState keeps the plain warm canvas).
  const chatBg = useChatBackground();
  const { streamingByChat, startSend, startRegenerate, stop } =
    useStreamingCompletion();
  const { data: messages } = useMessages(selectedChatId);
  const { data: chats } = useChats();
  const { data: personas } = usePersonas();
  const { data: models } = useModels();
  const generationSettings = useGenerationSettings();
  const scrollRef = useRef<HTMLDivElement>(null);
  const reduced = useReducedMotion();

  // Per-chat pending state: a request for chat A must not show indicators
  // (thinking bubble, disabled composer) while chat B is open. Streaming is
  // the production path; the mutation keys cover the non-streaming fallback.
  const sendingChatIds = usePendingChatIds(SEND_MESSAGE_MUTATION_KEY);
  const regeneratingChatIds = usePendingChatIds(REGENERATE_MESSAGE_MUTATION_KEY);
  const streamingEntry =
    selectedChatId != null ? streamingByChat.get(selectedChatId) ?? null : null;
  const regeneratingThisChat =
    streamingEntry?.kind === "regenerate" ||
    (selectedChatId != null && regeneratingChatIds.includes(selectedChatId));
  const pendingForThisChat =
    streamingEntry != null ||
    regeneratingThisChat ||
    (selectedChatId != null && sendingChatIds.includes(selectedChatId));

  // Draft restoration: failed sends are keyed by chat id so a failure in one
  // chat never clobbers the composer of another. In-memory only - the
  // contract forbids persisting drafts.
  const [failedDrafts, setFailedDrafts] = useState<ReadonlyMap<number, string>>(
    () => new Map(),
  );

  const storeFailedDraft = useCallback((chatId: number, draft: string) => {
    setFailedDrafts((prev) => {
      const next = new Map(prev);
      next.set(chatId, draft);
      return next;
    });
  }, []);

  const clearFailedDraft = useCallback((chatId: number) => {
    setFailedDrafts((prev) => {
      if (!prev.has(chatId)) return prev;
      const next = new Map(prev);
      next.delete(chatId);
      return next;
    });
  }, []);

  const restoredDraft =
    selectedChatId != null ? failedDrafts.get(selectedChatId) ?? null : null;

  const handleDraftConsumed = useCallback(() => {
    if (selectedChatId != null) clearFailedDraft(selectedChatId);
  }, [selectedChatId, clearFailedDraft]);

  // Live composer draft: kept PER-CHAT (like failedDrafts) so typing in one
  // chat and switching to another never shows - or sends - the first chat's
  // unsent text. Switching back restores it. In-memory only (privacy rule:
  // no browser storage for drafts).
  const [liveDrafts, setLiveDrafts] = useState<ReadonlyMap<number, string>>(
    () => new Map(),
  );

  const handleDraftChange = useCallback(
    (text: string) => {
      if (selectedChatId == null) return;
      const chatId = selectedChatId;
      setLiveDrafts((prev) => {
        if ((prev.get(chatId) ?? "") === text) return prev;
        const next = new Map(prev);
        // Empty drafts drop their entry - keeps the map to genuinely-unsent text.
        if (text === "") next.delete(chatId);
        else next.set(chatId, text);
        return next;
      });
    },
    [selectedChatId],
  );

  const liveDraft =
    selectedChatId != null ? liveDrafts.get(selectedChatId) ?? "" : "";

  // Send errors are keyed by chat id too: the Composer banner shows only the
  // selected chat's error (single surface for send errors).
  const [sendErrors, setSendErrors] = useState<ReadonlyMap<number, unknown>>(
    () => new Map(),
  );

  const storeSendError = useCallback((chatId: number, err: unknown) => {
    setSendErrors((prev) => {
      const next = new Map(prev);
      next.set(chatId, err);
      return next;
    });
  }, []);

  const clearSendError = useCallback((chatId: number) => {
    setSendErrors((prev) => {
      if (!prev.has(chatId)) return prev;
      const next = new Map(prev);
      next.delete(chatId);
      return next;
    });
  }, []);

  const sendErrorForThisChat =
    selectedChatId != null ? sendErrors.get(selectedChatId) ?? null : null;

  // Dismissal deletes the chat's map entry - the error stays gone across
  // chat switches, and a NEW error for the same chat shows again.
  const handleDismissError = useCallback(() => {
    if (selectedChatId != null) clearSendError(selectedChatId);
  }, [selectedChatId, clearSendError]);

  // ── Staged image attachments ─────────────────────────────────────
  // Keyed by chat id like drafts: staging in one chat never leaks into
  // another. In-memory only - the contract forbids persisting attachments
  // in browser storage. Entries upload immediately on add; send collects
  // the ready ids.
  const pushError = useErrorStore((s) => s.pushError);
  const [stagedAttachments, setStagedAttachments] = useState<
    ReadonlyMap<number, readonly StagedAttachment[]>
  >(() => new Map());
  const errorHideTimersRef = useRef<Set<ReturnType<typeof setTimeout>>>(
    new Set(),
  );

  // Ref mirror of the staged map. Two roles: (1) unmount/deletion cleanup can
  // revoke outstanding preview URLs without an effect subscribed to every
  // staging change; (2) it is the SYNCHRONOUS source of truth for the add
  // cap - the state closure goes stale when two adds land in one tick, but the
  // ref is written in lockstep with each add so the cap always sees reality.
  const stagedAttachmentsRef = useRef(stagedAttachments);
  useEffect(() => {
    stagedAttachmentsRef.current = stagedAttachments;
  }, [stagedAttachments]);

  useEffect(() => {
    const timers = errorHideTimersRef.current;
    return () => {
      for (const timer of timers) clearTimeout(timer);
      timers.clear();
      // No object-URL leaks: revoke whatever is still staged on unmount.
      for (const list of stagedAttachmentsRef.current.values()) {
        for (const attachment of list) revokePreviewUrl(attachment.previewUrl);
      }
    };
  }, []);

  const updateStagedAttachment = useCallback(
    (chatId: number, key: string, patch: Partial<StagedAttachment>) => {
      setStagedAttachments((prev) => {
        const current = prev.get(chatId);
        if (!current || !current.some((a) => a.key === key)) return prev;
        const next = new Map(prev);
        next.set(
          chatId,
          current.map((a) => (a.key === key ? { ...a, ...patch } : a)),
        );
        return next;
      });
    },
    [],
  );

  const removeStagedAttachment = useCallback(
    (chatId: number, key: string) => {
      setStagedAttachments((prev) => {
        const current = prev.get(chatId);
        const entry = current?.find((a) => a.key === key);
        if (!current || !entry) return prev;
        // Side effect inside the updater is safe: revoking an already-revoked
        // object URL is a no-op.
        revokePreviewUrl(entry.previewUrl);
        const remaining = current.filter((a) => a.key !== key);
        const next = new Map(prev);
        if (remaining.length === 0) next.delete(chatId);
        else next.set(chatId, remaining);
        return next;
      });
    },
    [],
  );

  /** Staged rows purge server-side on their own - client just drops + revokes. */
  const clearStagedAttachments = useCallback((chatId: number) => {
    setStagedAttachments((prev) => {
      const current = prev.get(chatId);
      if (!current || current.length === 0) return prev;
      for (const attachment of current) revokePreviewUrl(attachment.previewUrl);
      const next = new Map(prev);
      next.delete(chatId);
      return next;
    });
  }, []);

  /** Restore staged entries after a failed send cleared them at
   * user_message time - ONLY when the strip is empty (a pre-stream failure
   * never cleared it, and restoring then would duplicate entries). The
   * previews were revoked, so entries come back without a bitmap. */
  const restoreStagedIfEmpty = useCallback(
    (chatId: number, entries: readonly StagedAttachment[]) => {
      if (entries.length === 0) return;
      setStagedAttachments((prev) => {
        if ((prev.get(chatId) ?? []).length > 0) return prev;
        const next = new Map(prev);
        next.set(chatId, [...entries]);
        return next;
      });
    },
    [],
  );

  // Revoke staged previews for chats that leave the list (deleted elsewhere).
  // Their blob: URLs hold full decoded images and would otherwise leak until
  // app unmount. The currently-viewed chat is skipped - its previews are in
  // use. Reads the ref mirror so this effect need not depend on every staging
  // change; it only fires when the chats list or the selection changes.
  useEffect(() => {
    if (chats == null) return; // list not loaded yet - nothing to reconcile
    const liveIds = new Set(chats.map((c) => c.id));
    for (const chatId of stagedAttachmentsRef.current.keys()) {
      if (chatId === selectedChatId) continue; // in-use previews stay
      if (!liveIds.has(chatId)) clearStagedAttachments(chatId);
    }
  }, [chats, selectedChatId, clearStagedAttachments]);

  const handleAddAttachments = useCallback(
    (files: File[]) => {
      if (selectedChatId == null) return;
      const chatId = selectedChatId;
      const accepted = files.filter(isAcceptedImageFile);
      if (accepted.length === 0) return;

      // Cap against the LATEST staged state via the ref, not the render
      // closure: two adds in the same tick would both read an empty snapshot
      // and each stage up to 4, blowing past MAX_ATTACHMENTS. The ref is
      // updated in lockstep with the write below, so the second add sees the
      // first add's entries.
      const current = stagedAttachmentsRef.current.get(chatId) ?? [];
      const slots = MAX_ATTACHMENTS - current.length;
      if (slots <= 0) return;
      const toAdd = accepted.slice(0, slots);

      const entries: StagedAttachment[] = toAdd.map((file) => ({
        key: nextStagedKey(),
        id: null,
        mime: file.type,
        width: null,
        height: null,
        previewUrl: createPreviewUrl(file),
        status: "uploading",
      }));

      // Write the ref synchronously (source of truth for the next add's cap)
      // and push the same map into React state.
      const nextMap = new Map(stagedAttachmentsRef.current);
      nextMap.set(chatId, [...current, ...entries]);
      stagedAttachmentsRef.current = nextMap;
      setStagedAttachments(nextMap);

      // Upload immediately; each entry settles independently.
      toAdd.forEach((file, index) => {
        const { key } = entries[index];
        uploadImage(file).then(
          (uploaded) => {
            updateStagedAttachment(chatId, key, {
              id: uploaded.id,
              mime: uploaded.mime,
              width: uploaded.width,
              height: uploaded.height,
              status: "ready",
            });
          },
          (err: unknown) => {
            // Toast is the surface for upload failures; the thumbnail shows
            // an error state briefly, then removes itself.
            pushError(err);
            updateStagedAttachment(chatId, key, { status: "error" });
            const timer = setTimeout(() => {
              errorHideTimersRef.current.delete(timer);
              removeStagedAttachment(chatId, key);
            }, ATTACHMENT_ERROR_HIDE_MS);
            errorHideTimersRef.current.add(timer);
          },
        );
      });
    },
    [
      selectedChatId,
      pushError,
      updateStagedAttachment,
      removeStagedAttachment,
    ],
  );

  const handleRemoveAttachment = useCallback(
    (key: string) => {
      if (selectedChatId != null) removeStagedAttachment(selectedChatId, key);
    },
    [selectedChatId, removeStagedAttachment],
  );

  const stagedForThisChat =
    selectedChatId != null
      ? stagedAttachments.get(selectedChatId) ?? EMPTY_STAGED
      : EMPTY_STAGED;

  // Auto-scroll:
  //  (a) chat switch → instant jump to bottom (no animation)
  //  (b) new last GROUP (send, response) → smooth scroll
  // Derived from the variant-group anchor, not the raw row id: appending a
  // new variant or flipping the active one changes ids but not the anchor,
  // and neither should yank the scroll position.
  const lastMessage =
    messages && messages.length > 0 ? messages[messages.length - 1] : null;
  const lastMessageId = lastMessage ? messageAnchor(lastMessage) : null;
  const scrolledChatRef = useRef<number | null>(null);
  const prevLastMessageIdRef = useRef<number | null>(null);

  useEffect(() => {
    const el = scrollRef.current;
    const prevLastMessageId = prevLastMessageIdRef.current;
    prevLastMessageIdRef.current = lastMessageId;

    if (selectedChatId == null) {
      scrolledChatRef.current = null;
      return;
    }
    if (!el || typeof el.scrollTo !== "function") return;

    // Claim this chat as "seen" BEFORE the empty-chat early return: otherwise
    // switching A(populated) → B(empty) → A leaves scrolledChatRef stuck on A,
    // and the return to A takes the smooth branch and animates from the top
    // instead of the promised instant jump.
    const firstPaintOfThisChat = scrolledChatRef.current !== selectedChatId;
    if (firstPaintOfThisChat) scrolledChatRef.current = selectedChatId;

    if (lastMessageId == null) return; // empty chat - nothing to scroll to

    if (firstPaintOfThisChat) {
      // First paint of this chat's messages - jump without animation
      el.scrollTo({ top: el.scrollHeight, behavior: "instant" });
      return;
    }
    if (lastMessageId !== prevLastMessageId) {
      el.scrollTo({
        top: el.scrollHeight,
        behavior: reduced ? "instant" : "smooth",
      });
    }
  }, [selectedChatId, lastMessageId, reduced]);

  // Follow the streaming text as it grows. Always an INSTANT jump: deltas
  // land every animation frame, and a `smooth` scroll restarted per frame
  // never finishes its animation - the viewport rubber-bands and the text
  // looks stuttery. Discrete events (new message) keep their smooth scroll.
  // Bottom-anchored only: once the reader scrolls up to re-read, following
  // would yank them back every frame (and force a full re-raster of the
  // scroller per flush). Returning near the bottom re-engages the follow.
  const streamingTextLength = streamingEntry?.text.length ?? 0;
  useEffect(() => {
    if (streamingTextLength === 0) return;
    const el = scrollRef.current;
    if (!el || typeof el.scrollTo !== "function") return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distanceFromBottom > 120) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "instant" });
  }, [streamingTextLength]);

  const handleSend = useCallback(
    (messageText: string) => {
      if (selectedChatId == null || selectedModelId == null) return;
      const chatId = selectedChatId;
      const personaId = safePersonaId(getSelectedPersonaId(personas));
      const selectedModel = findModelById(models?.models, selectedModelId);
      const { generationParams, contextBudgetTokens } =
        generationSettings.getRequestSettings();
      // Ready upload ids only - the Composer blocks send while any upload is
      // in flight, and failed entries auto-remove themselves. Defense in depth:
      // a text-only model contributes NO attachment ids even if some are staged
      // (the Composer already disables send in that case) - never POST images to
      // a model that would 400 on them.
      const supportsImages = hasInputModality(selectedModel, "image");
      const readyStaged = supportsImages
        ? (stagedAttachments.get(chatId) ?? []).filter(
            (a) => a.status === "ready" && a.id != null,
          )
        : [];
      const attachmentIds = readyStaged.map((a) => a.id as number);
      // Snapshot for error restore: the strip clears at user_message time,
      // but a later failure unlinks the ids server-side - the retry must
      // carry them again. Previews are revoked by then, so snapshot without
      // a bitmap (AttachmentStrip renders a blank tile).
      const stagedSnapshot: StagedAttachment[] = readyStaged.map((a) => ({
        ...a,
        previewUrl: "",
      }));
      clearFailedDraft(chatId);
      clearSendError(chatId);
      void startSend(
        {
          chatId,
          message: messageText,
          modelId: selectedModelId,
          generationParams,
          personaId,
          contextBudgetTokens,
          model: selectedModel,
          attachments: attachmentIds.length > 0 ? attachmentIds : undefined,
        },
        {
          onError: (err) => {
            // Restore draft + surface the error - both keyed by the chat the
            // send targeted, not whatever chat is open at error time. The
            // backend unlinked the attachment ids on failure, so restore the
            // staged entries too when the strip was already cleared.
            storeFailedDraft(chatId, messageText);
            storeSendError(chatId, err);
            restoreStagedIfEmpty(chatId, stagedSnapshot);
          },
          onAbortedEmpty: () => {
            // User stopped before anything streamed: nothing was sent - give
            // the text (and staged attachments) back without an error banner.
            storeFailedDraft(chatId, messageText);
            restoreStagedIfEmpty(chatId, stagedSnapshot);
          },
          onUserMessagePersisted: () => {
            // The image now renders inside the sent bubble - drop the staged
            // thumbnails immediately instead of showing a duplicate above the
            // composer for the whole (possibly long) stream.
            clearStagedAttachments(chatId);
          },
          onPersisted: () => {
            // Safety net (idempotent): the exchange is fully persisted - any
            // staged copies left are consumed and their previews revoked.
            clearStagedAttachments(chatId);
          },
        },
      );
    },
    [
      selectedChatId,
      selectedModelId,
      personas,
      models?.models,
      generationSettings,
      stagedAttachments,
      startSend,
      clearFailedDraft,
      clearSendError,
      storeFailedDraft,
      storeSendError,
      clearStagedAttachments,
      restoreStagedIfEmpty,
    ],
  );

  const handleRegenerate = useCallback(
    (messageId: number) => {
      if (selectedChatId == null || selectedModelId == null) return;
      // Assemble EXACTLY the same sources as handleSend so regenerate carries
      // generation settings, persona, and context budget.
      const personaId = safePersonaId(getSelectedPersonaId(personas));
      const selectedModel = findModelById(models?.models, selectedModelId);
      const { generationParams, contextBudgetTokens } =
        generationSettings.getRequestSettings();
      // Variant-group anchor: streaming text is routed to the bubble by
      // GROUP (bubbles are group-keyed), not by row id.
      const target = messages?.find((m) => m.id === messageId);
      const anchor = target ? messageAnchor(target) : messageId;
      void startRegenerate({
        chatId: selectedChatId,
        messageId,
        anchor,
        modelId: selectedModelId,
        generationParams,
        personaId,
        contextBudgetTokens,
        model: selectedModel,
      });
    },
    [
      selectedChatId,
      selectedModelId,
      personas,
      models?.models,
      generationSettings,
      startRegenerate,
      messages,
    ],
  );

  // Variant navigation: make a sibling row the active variant (optimistic
  // flag flip in the mutation; the carousel animates from cache state).
  // Destructured to .mutate on purpose: TanStack v5's mutation RESULT is a
  // fresh object every render, and depending on it rebuilt this callback each
  // render - which broke memo(MessageBubble) for every bubble on every
  // streaming flush. Only .mutate is referentially stable.
  const { mutate: activateVariantMutate } = useActivateVariant();
  const handleActivateVariant = useCallback(
    (messageId: number) => {
      if (selectedChatId == null) return;
      activateVariantMutate({ chatId: selectedChatId, messageId });
    },
    [selectedChatId, activateVariantMutate],
  );

  const handleStop = useCallback(() => {
    if (selectedChatId != null) stop(selectedChatId);
  }, [selectedChatId, stop]);

  // A user-set chat background paints an opaque cover over the canvas - the
  // in-canvas fog would be invisible work, so it unmounts underneath it.
  const chatBgCovers = selectedChatId != null && chatBg.style != null;

  return (
    <main className="warm-canvas flex flex-1 flex-col overflow-hidden">
      {!chatBgCovers && <CanvasMist />}
      <ErrorToastStack />

      {/* Message area */}
      <div
        ref={scrollRef}
        className={`flex-1 overflow-y-auto ${
          selectedChatId != null && chatBg.style != null && chatBg.dark
            ? "chat-bg-dark"
            : ""
        }`}
        style={
          {
            "--msg-fs": `${msgFontPx}px`,
            "--msg-lh": String(msgLineHeight),
            ...(selectedChatId != null && chatBg.style != null
              ? chatBg.style
              : null),
          } as CSSProperties
        }
      >
        {selectedChatId != null ? (
          <MessageList
            chatId={selectedChatId}
            isPending={pendingForThisChat}
            regenerating={regeneratingThisChat}
            onRegenerate={handleRegenerate}
            onActivateVariant={handleActivateVariant}
            onAbortGeneration={handleStop}
            streaming={streamingEntry}
          />
        ) : (
          <EmptyState />
        )}
      </div>

      {/* Composer */}
      <Composer
        onSend={handleSend}
        isPending={pendingForThisChat}
        streaming={streamingEntry != null}
        onStop={handleStop}
        sendError={sendErrorForThisChat}
        onDismissError={handleDismissError}
        clearOnSend={true}
        restoredDraft={restoredDraft}
        onDraftConsumed={handleDraftConsumed}
        draft={liveDraft}
        onDraftChange={handleDraftChange}
        attachments={stagedForThisChat}
        onAddFiles={handleAddAttachments}
        onRemoveAttachment={handleRemoveAttachment}
      />
    </main>
  );
}
