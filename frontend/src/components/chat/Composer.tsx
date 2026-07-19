import { memo, useRef, useCallback, useEffect, useId } from "react";
import { useSettings } from "@/lib/query/settings";
import { useModels } from "@/lib/query/models";
import { useUiStore } from "@/lib/store/uiStore";
import { parseApiError } from "@/lib/errors";
import { findModelById, hasInputModality } from "@/lib/models";
import { AttachmentStrip } from "./AttachmentStrip";
import { ACCEPTED_IMAGE_ACCEPT, isAcceptedImageFile } from "./attachments";
import type { StagedAttachment } from "./attachments";
import {
  Send,
  Loader2,
  AlertCircle,
  X,
  Settings,
  Square,
  ImagePlus,
} from "lucide-react";

// ── CTA-aware error mapping ─────────────────────────────────────
// Uses FE-1A parseApiError for safe message, adds CTA hint for secrets-related errors
const SECRETS_CTA_CODES = new Set([
  "api_key_missing",
  "api_key_invalid",
  "auth_failed",
  "proxy_missing",
  "proxy_unreachable",
  "proxy_auth_failed",
]);

function getErrorDisplay(err: unknown): { text: string; cta?: "secrets" } {
  const parsed = parseApiError(err);
  return {
    text: parsed.message,
    cta: SECRETS_CTA_CODES.has(parsed.detail) ? "secrets" : undefined,
  };
}

// ── Props ────────────────────────────────────────────────────────
interface ComposerProps {
  onSend: (messageText: string) => void;
  isPending: boolean;
  sendError: unknown;
  /** Called when the user dismisses the error banner - the owner (ChatCanvas)
   * drops the chat's error entry, so the banner stays gone across chat
   * switches until a NEW error arrives. */
  onDismissError?: () => void;
  /** True while a stream is active for the selected chat - the send button
   * becomes a Stop button that aborts the stream. */
  streaming?: boolean;
  /** Aborts the active stream for the selected chat. */
  onStop?: () => void;
  /** If true, input clears immediately when onSend is called (optimistic). */
  clearOnSend?: boolean;
  /** If set, restores this text into the input (e.g. after error rollback). */
  restoredDraft?: string | null;
  /** Called once when restoredDraft has been consumed into the input. */
  onDraftConsumed?: () => void;
  /** Live draft text for the selected chat (owned by ChatCanvas). Kept
   * per-chat so switching chats never shows another chat's unsent text. */
  draft: string;
  /** Updates the selected chat's live draft text. */
  onDraftChange: (text: string) => void;
  /** Staged image attachments for the selected chat (owned by ChatCanvas). */
  attachments?: readonly StagedAttachment[];
  /** Adds image files to the staged attachments (file picker or paste). */
  onAddFiles?: (files: File[]) => void;
  /** Removes one staged attachment by its client key. */
  onRemoveAttachment?: (key: string) => void;
}

/* memo: ChatCanvas re-renders on every streaming flush; the composer's props
   are all stable during a stream (draft/attachments untouched), so memo keeps
   the input dock out of the per-frame reconcile. Keystrokes still re-render
   it via the changing `draft` prop - that part is its actual job. */
export const Composer = memo(function Composer({
  onSend,
  isPending,
  sendError,
  onDismissError,
  streaming,
  onStop,
  clearOnSend,
  restoredDraft,
  onDraftConsumed,
  draft,
  onDraftChange,
  attachments,
  onAddFiles,
  onRemoveAttachment,
}: ComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const helperId = useId();
  const errorId = useId();
  const selectedChatId = useUiStore((s) => s.selectedChatId);
  const selectedModelId = useUiStore((s) => s.selectedModelId);
  // CTA routes to "secrets" tab (renamed from "settings" in Phase 6E-A)
  const setActiveTab = useUiStore((s) => s.setActiveRightPanelTab);
  const { data: settings, isLoading: settingsLoading, error: settingsError } = useSettings();
  const { data: models } = useModels();

  // Restore draft text on error rollback; consuming clears the parent entry.
  // The live draft is owned by ChatCanvas, so restoring writes back through
  // onDraftChange rather than any local state.
  const prevDraft = useRef(restoredDraft);
  useEffect(() => {
    if (restoredDraft != null && restoredDraft !== prevDraft.current) {
      onDraftChange(restoredDraft);
      onDraftConsumed?.();
    }
    prevDraft.current = restoredDraft;
  }, [restoredDraft, onDraftConsumed, onDraftChange]);

  // Keep the textarea height in sync when the draft changes from outside a
  // keystroke - a chat switch (new chat's draft) or an error restore.
  // Keystroke-originated changes already sized synchronously in handleInput
  // and are skipped here: repeating the auto→measure→set dance would force a
  // second reflow per keystroke for nothing.
  const sizedByKeystrokeRef = useRef<string | null>(null);
  useEffect(() => {
    if (sizedByKeystrokeRef.current === draft) return;
    sizedByKeystrokeRef.current = null;
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    if (draft.length > 0) ta.style.height = `${ta.scrollHeight}px`;
  }, [draft]);

  // ── Preflight ──
  const noChatSelected = selectedChatId == null;
  const noModelSelected = selectedModelId == null;
  const settingsBroken = settingsError != null;
  const apiKeyMissing = settings != null && !settings.api_key_set;
  const proxyIssue =
    settings != null && settings.proxy_required && !settings.proxy_configured;

  const preflightBlocked =
    noChatSelected ||
    noModelSelected ||
    settingsLoading ||
    settingsBroken ||
    apiKeyMissing ||
    proxyIssue;

  // ── Attachments ──
  const stagedAttachments = attachments ?? [];
  // Sends must wait for in-flight uploads: their ids do not exist yet.
  const uploadingAttachment = stagedAttachments.some(
    (a) => a.status === "uploading",
  );
  const hasReadyAttachment = stagedAttachments.some(
    (a) => a.status === "ready",
  );
  const selectedModel = findModelById(models?.models, selectedModelId);
  const supportsImageInput = hasInputModality(selectedModel, "image");
  const attachBlocked = preflightBlocked || isPending || !supportsImageInput;
  // Staged images on a text-only model cannot be sent - the backend rejects
  // them (model_no_image_input). Block send instead of wasting a round-trip.
  const imagesBlockedByModel =
    stagedAttachments.length > 0 && !supportsImageInput;

  const canSend =
    !preflightBlocked &&
    draft.trim().length > 0 &&
    !isPending &&
    !uploadingAttachment &&
    !imagesBlockedByModel;

  // ── Handlers ──
  const handleSend = useCallback(() => {
    if (!canSend) return;
    const text = draft.trim();
    if (clearOnSend) {
      onDraftChange("");
      const ta = textareaRef.current;
      if (ta) ta.style.height = "auto";
    }
    onSend(text);
  }, [canSend, draft, onSend, clearOnSend, onDraftChange]);

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    // The button guarding this input is disabled while attachBlocked, but the
    // change handler re-checks (defense in depth, same as the paste path).
    const files = attachBlocked
      ? []
      : Array.from(e.target.files ?? []).filter(isAcceptedImageFile);
    if (files.length > 0) onAddFiles?.(files);
    // Reset so selecting the same file again re-fires the change event.
    e.target.value = "";
  };

  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    if (attachBlocked) return;
    const images = Array.from(e.clipboardData?.files ?? []).filter(
      isAcceptedImageFile,
    );
    if (images.length === 0) return;
    e.preventDefault();
    onAddFiles?.(images);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      if (canSend) {
        e.preventDefault();
        handleSend();
      } else {
        e.preventDefault();
      }
    }
  };

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    sizedByKeystrokeRef.current = e.target.value;
    onDraftChange(e.target.value);
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = "auto";
      ta.style.height = `${ta.scrollHeight}px`;
    }
  };

  // ── Preflight helper text ──
  let helperText: string | null = null;
  if (noChatSelected) helperText = "Select a character and chat to start.";
  else if (settingsLoading) helperText = "Checking settings…";
  else if (settingsBroken) helperText = "Cannot load settings. Is the backend running?";
  else if (apiKeyMissing) helperText = "API key is not set. Configure it in Secrets.";
  else if (proxyIssue) helperText = "Proxy is required but not configured. Set it up in Secrets.";
  else if (noModelSelected) helperText = "Select a model from the Models tab.";

  // ── Attach button title ──
  // Reflect the TRUE reason the attach button is unavailable, not always the
  // modality one (which misleads when nothing is selected yet).
  let attachTitle = "Attach images";
  if (noChatSelected) attachTitle = "Select a chat to attach images";
  else if (noModelSelected) attachTitle = "Select a model to attach images";
  else if (!supportsImageInput)
    attachTitle = "Selected model does not support image input";

  // ── Send button title ──
  // Explain WHY send is unavailable so a disabled button is never silent.
  let sendTitle: string | undefined;
  if (uploadingAttachment) sendTitle = "Uploading image…";
  else if (imagesBlockedByModel)
    sendTitle =
      "Selected model does not support images - remove them or switch models";
  else if (hasReadyAttachment && draft.trim().length === 0)
    sendTitle = "Add a message to send with your images";

  // ── Error display ──
  // Dismissal is owned by the parent: it deletes the chat's error entry, so
  // sendError itself goes null - no local dismissed-instance tracking needed.
  const showError = sendError != null;
  const errorInfo = sendError ? getErrorDisplay(sendError) : null;

  // Link visible banners to the textarea for assistive tech
  const describedBy =
    [
      showError && errorInfo ? errorId : null,
      helperText && !showError ? helperId : null,
    ]
      .filter(Boolean)
      .join(" ") || undefined;

  return (
    <div
      // xl (not lg): the side panels' clamp() floors leave the chat column
      // ~380-430px at a 1024-1185px window - a viewport-keyed lg:px-14 would
      // spend 112px of that on padding right where space is tightest.
      className="px-8 py-5 xl:px-14"
      style={{
        boxShadow: "0 -18px 36px rgba(28, 38, 50, 0.10)",
        borderTop: "1px solid rgba(28, 38, 50, 0.12)",
      }}
    >
      {/* Error banner */}
      {showError && errorInfo && (
        <div
          id={errorId}
          className="mb-3 flex items-start gap-2 rounded-xl px-3 py-2.5 text-xs"
          role="alert"
          style={{
            backgroundColor: "rgba(195, 106, 114, 0.10)",
            color: "var(--color-es-danger)",
            border: "1px solid rgba(195, 106, 114, 0.18)",
          }}
        >
          <AlertCircle size={13} className="mt-0.5 shrink-0" />
          <span className="flex-1">{errorInfo.text}</span>
          {errorInfo.cta === "secrets" && (
            <button
              type="button"
              className="shrink-0 opacity-70 hover:opacity-100 transition-opacity"
              onClick={() => setActiveTab("secrets")}
              aria-label="Go to Secrets"
            >
              <Settings size={12} />
            </button>
          )}
          <button
            type="button"
            className="shrink-0 opacity-50 hover:opacity-90 transition-opacity"
            onClick={() => onDismissError?.()}
            aria-label="Dismiss error"
          >
            <X size={12} />
          </button>
        </div>
      )}

      {/* Helper banner (preflight) */}
      {helperText && !showError && (
        <div
          id={helperId}
          className="mb-3 flex items-center gap-2 rounded-xl px-3 py-2 text-xs"
          style={{
            backgroundColor: "rgba(28, 38, 50, 0.07)",
            color: "var(--color-es-asst-bubble-text)",
            opacity: 0.85,
          }}
        >
          {(apiKeyMissing || proxyIssue) && (
            <button
              type="button"
              className="shrink-0 opacity-70 hover:opacity-100 transition-opacity"
              onClick={() => setActiveTab("secrets")}
              aria-label="Go to Secrets"
            >
              <Settings size={12} />
            </button>
          )}
          <span>{helperText}</span>
        </div>
      )}

      {/* Input dock - warm translucent surface */}
      <div
        className="rounded-xl px-5 py-4"
        style={{
          backgroundColor: "rgba(238, 244, 250, 0.62)",
          border: "1px solid rgba(255, 255, 255, 0.46)",
          boxShadow: "0 14px 32px rgba(28, 38, 50, 0.10)",
          backdropFilter: "blur(12px)",
        }}
      >
        {/* Staged image thumbnails (renders nothing when empty) */}
        <AttachmentStrip
          attachments={stagedAttachments}
          onRemove={(key) => onRemoveAttachment?.(key)}
          inactive={imagesBlockedByModel}
        />

        <div className="flex items-end gap-3">
          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPTED_IMAGE_ACCEPT}
            multiple
            className="hidden"
            aria-label="Attach image files"
            onChange={handleFileInputChange}
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={attachBlocked}
            aria-label="Attach images"
            title={attachTitle}
            className="composer-icon-button shrink-0 rounded-xl p-2.5 disabled:cursor-not-allowed disabled:opacity-30"
            style={{
              backgroundColor: "rgba(28, 38, 50, 0.08)",
              color: "var(--color-es-asst-bubble-text)",
            }}
          >
            <ImagePlus size={15} />
          </button>
          <textarea
            ref={textareaRef}
            value={draft}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            disabled={preflightBlocked || isPending}
            placeholder={
              isPending
                ? "Waiting for response…"
                : preflightBlocked
                  ? "Message sending disabled"
                  : "Type a message…"
            }
            aria-label="Message"
            aria-describedby={describedBy}
            rows={1}
            // py-1.5 sizes a single line (~22.75px text + 12px padding) to the
            // 35px icon buttons, so the caret/text line sits vertically
            // centered against them; multiline growth stays bottom-aligned.
            className="flex-1 resize-none bg-transparent py-1.5 text-sm leading-relaxed outline-none disabled:cursor-not-allowed disabled:opacity-40"
            style={{
              color: "var(--color-es-asst-bubble-text)",
              maxHeight: "6rem",
              overflowY: "auto",
            }}
          />
          {streaming ? (
            <button
              type="button"
              onClick={onStop}
              aria-label="Stop generating"
              title="Stop generating"
              className="btn-sage-glow composer-icon-button shrink-0 rounded-xl p-2.5"
              style={{
                backgroundColor: "var(--color-es-primary-sage)",
                color: "var(--color-es-text-dark)",
              }}
            >
              <Square size={15} />
            </button>
          ) : (
            <button
              type="button"
              disabled={!canSend}
              onClick={handleSend}
              aria-label="Send message"
              title={sendTitle}
              className="btn-sage-glow composer-icon-button shrink-0 rounded-xl p-2.5 disabled:cursor-not-allowed disabled:opacity-30"
              style={{
                backgroundColor: "var(--color-es-primary-sage)",
                color: "var(--color-es-text-dark)",
              }}
            >
              {isPending ? (
                <Loader2 size={15} className="animate-spin" />
              ) : (
                <Send size={15} />
              )}
            </button>
          )}
        </div>
      </div>
    </div>
  );
});
