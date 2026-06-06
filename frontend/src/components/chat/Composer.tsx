import { useState, useRef, useCallback, useEffect } from "react";
import { useSettings } from "@/lib/query/settings";
import { useUiStore } from "@/lib/store/uiStore";
import { isApiError } from "@/lib/api/client";
import { Send, Loader2, AlertCircle, X, Settings } from "lucide-react";

// ── Error map ────────────────────────────────────────────────────
const ERROR_MAP: Record<string, { text: string; cta?: "secrets" }> = {
  api_key_missing: {
    text: "API key is not set. Configure it in Secrets.",
    cta: "secrets",
  },
  auth_failed: {
    text: "Authentication failed. Check your API key in Secrets.",
    cta: "secrets",
  },
  proxy_missing: {
    text: "Proxy required but not configured. Set it up in Secrets.",
    cta: "secrets",
  },
  proxy_unreachable: {
    text: "Proxy is unreachable. Check your proxy configuration.",
    cta: "secrets",
  },
  openrouter_timeout: { text: "Request timed out. Try again." },
  openrouter_rate_limited: {
    text: "Rate limit reached. Wait a moment and try again.",
  },
  openrouter_completion_error: {
    text: "OpenRouter returned an error. This may be because the model is unavailable under Elysium\u2019s strict privacy routing (ZDR). Try a different model or retry.",
  },
  context_too_large: {
    text: "Chat history is too long for this model\u2019s context limit. Try a model with a larger context window.",
  },
  invalid_openrouter_completion_response: {
    text: "Received an unexpected response. Please retry.",
  },
  chat_not_found: { text: "This chat no longer exists. Please refresh." },
  invalid_response_shape: {
    text: "Received an unexpected response format. Please retry.",
  },
  invalid_gen_params: {
    text: "Invalid generation parameters. Check your settings and try again.",
  },
};

function getErrorMessage(err: unknown): { text: string; cta?: "secrets" } {
  if (!isApiError(err)) {
    return { text: "Cannot reach the server. Is the backend running?" };
  }
  return ERROR_MAP[err.detail] ?? { text: "Something went wrong. Please try again." };
}

// ── Props ────────────────────────────────────────────────────────
interface ComposerProps {
  onSend: (messageText: string) => void;
  isPending: boolean;
  sendError: unknown;
  /** Changes to a new number on successful send — triggers input clear. */
  resetKey: number;
}

export function Composer({ onSend, isPending, sendError, resetKey }: ComposerProps) {
  const [message, setMessage] = useState("");
  const [dismissed, setDismissed] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const prevResetKey = useRef(resetKey);
  const selectedChatId = useUiStore((s) => s.selectedChatId);
  const selectedModelId = useUiStore((s) => s.selectedModelId);
  // CTA routes to "secrets" tab (renamed from "settings" in Phase 6E-A)
  const setActiveTab = useUiStore((s) => s.setActiveRightPanelTab);
  const { data: settings, isLoading: settingsLoading, error: settingsError } = useSettings();

  // Clear input when resetKey changes (i.e., on success)
  useEffect(() => {
    if (resetKey !== prevResetKey.current) {
      prevResetKey.current = resetKey;
      setMessage("");
      const ta = textareaRef.current;
      if (ta) ta.style.height = "auto";
    }
  }, [resetKey]);

  // Reset dismiss flag when a new error arrives
  const prevErrorRef = useRef<unknown>(null);
  if (sendError !== prevErrorRef.current) {
    prevErrorRef.current = sendError;
    if (sendError) setDismissed(false);
  }

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

  const canSend = !preflightBlocked && message.trim().length > 0 && !isPending;

  // ── Handlers ──
  const handleSend = useCallback(() => {
    if (!canSend) return;
    onSend(message.trim());
  }, [canSend, message, onSend]);

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
    setMessage(e.target.value);
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = "auto";
      ta.style.height = `${ta.scrollHeight}px`;
    }
  };

  // ── Preflight helper text ──
  let helperText: string | null = null;
  if (noChatSelected) helperText = "Select a character and chat to start.";
  else if (settingsLoading) helperText = "Checking settings\u2026";
  else if (settingsBroken) helperText = "Cannot load settings. Is the backend running?";
  else if (apiKeyMissing) helperText = "API key is not set. Configure it in Secrets.";
  else if (proxyIssue) helperText = "Proxy is required but not configured. Set it up in Secrets.";
  else if (noModelSelected) helperText = "Select a model from the Models tab.";

  // ── Error display ──
  const showError = sendError != null && !dismissed;
  const errorInfo = sendError ? getErrorMessage(sendError) : null;

  return (
    <div
      className="px-8 py-5 lg:px-14"
      style={{
        boxShadow: "0 -18px 36px rgba(47, 49, 45, 0.10)",
        borderTop: "1px solid rgba(47, 49, 45, 0.12)",
      }}
    >
      {/* Error banner */}
      {showError && errorInfo && (
        <div
          className="mb-3 flex items-start gap-2 rounded-xl px-3 py-2.5 text-xs"
          role="alert"
          style={{
            backgroundColor: "rgba(201, 110, 91, 0.10)",
            color: "var(--color-es-danger)",
            border: "1px solid rgba(201, 110, 91, 0.18)",
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
            onClick={() => setDismissed(true)}
            aria-label="Dismiss error"
          >
            <X size={12} />
          </button>
        </div>
      )}

      {/* Helper banner (preflight) */}
      {helperText && !showError && (
        <div
          className="mb-3 flex items-center gap-2 rounded-xl px-3 py-2 text-xs"
          style={{
            backgroundColor: "rgba(47, 49, 45, 0.07)",
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

      {/* Input row — warm translucent dock */}
      <div
        className="flex items-end gap-3 rounded-2xl px-5 py-4"
        style={{
          backgroundColor: "rgba(245, 235, 217, 0.62)",
          border: "1px solid rgba(255, 255, 255, 0.46)",
          boxShadow: "0 14px 32px rgba(47, 49, 45, 0.10), inset 0 1px 0 rgba(255,255,255,0.42)",
          backdropFilter: "blur(12px)",
        }}
      >
        <textarea
          ref={textareaRef}
          value={message}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          disabled={preflightBlocked || isPending}
          placeholder={
            isPending
              ? "Waiting for response\u2026"
              : preflightBlocked
                ? "Message sending disabled"
                : "Type a message\u2026"
          }
          aria-label="Message"
          rows={1}
          className="flex-1 resize-none bg-transparent text-sm leading-relaxed outline-none disabled:cursor-not-allowed disabled:opacity-40"
          style={{
            color: "var(--color-es-asst-bubble-text)",
            maxHeight: "6rem",
            overflowY: "auto",
          }}
        />
        <button
          type="button"
          disabled={!canSend}
          onClick={handleSend}
          aria-label="Send message"
          className="btn-sage-glow shrink-0 rounded-xl p-2.5 transition-all disabled:cursor-not-allowed disabled:opacity-30"
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
      </div>
    </div>
  );
}
