import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useSettings, useSetApiKey, useDeleteApiKey } from "@/lib/query/settings";
import { parseApiError } from "@/lib/errors";
import { Key, Trash2, Check, AlertCircle, Loader2 } from "lucide-react";

export function ApiKeySection() {
  const { data: settings } = useSettings();
  const setApiKey = useSetApiKey();
  const deleteApiKey = useDeleteApiKey();
  const [keyInput, setKeyInput] = useState("");
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const handleSave = async () => {
    if (!keyInput.trim()) return;
    setFeedback(null);
    try {
      const result = await setApiKey.mutateAsync(keyInput.trim());
      if (result.ok) {
        setKeyInput(""); // Clear on success - write-only
        setFeedback({ type: "success", text: "API key saved" });
      } else {
        // validation_unavailable - the backend did NOT store the key.
        // Keep the input intact so the user can retry without retyping.
        setFeedback({
          type: "error",
          text: "Could not reach OpenRouter, so the key was not saved. Check your connection or proxy.",
        });
      }
    } catch (err) {
      setFeedback({ type: "error", text: parseApiError(err).message });
    }
  };

  const [confirmDelete, setConfirmDelete] = useState(false);

  const handleDelete = async () => {
    setFeedback(null);
    setConfirmDelete(false);
    try {
      await deleteApiKey.mutateAsync();
      setFeedback({ type: "success", text: "API key removed" });
    } catch (err) {
      setFeedback({ type: "error", text: parseApiError(err).message });
    }
  };

  const busy = setApiKey.isPending || deleteApiKey.isPending;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Key size={14} style={{ color: "var(--color-es-primary-sage)" }} />
        <h3
          className="text-sm font-medium"
          style={{ color: "var(--color-es-text-light)" }}
        >
          OpenRouter API key
        </h3>
      </div>

      {/* Status */}
      <div
        className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs"
        style={{ backgroundColor: "var(--color-es-surface-elevated)" }}
      >
        <div
          className="h-2 w-2 rounded-full"
          style={{
            backgroundColor: settings?.api_key_set
              ? "var(--color-es-success)"
              : "var(--color-es-danger)",
          }}
        />
        <span style={{ color: "var(--color-es-text-muted)" }}>
          {settings?.api_key_set ? "API key is set" : "No API key configured"}
        </span>
      </div>

      {/* Input - write-only, never shows saved key. Wrapped in a form so
          Enter saves (house convention). (v1.1 FF12.) */}
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (!busy && keyInput.trim()) void handleSave();
        }}
      >
        <Input
          type="password"
          placeholder="sk-or-v1-..."
          value={keyInput}
          onChange={(e) => setKeyInput(e.target.value)}
          disabled={busy}
          className="flex-1 text-xs"
          aria-label="API key input"
          // Keep the browser password manager out: this is an API key field,
          // not a login - no fill offers, no save-password prompts.
          autoComplete="off"
        />
        <Button
          type="submit"
          size="sm"
          disabled={busy || !keyInput.trim()}
          className="gap-1"
          style={{
            backgroundColor: "var(--color-es-primary-sage)",
            color: "var(--color-es-text-dark)",
          }}
        >
          {setApiKey.isPending ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <Check size={12} />
          )}
          Save
        </Button>
      </form>

      {/* Delete - inline confirm so one stray click can't wipe the key
          (write-only, unrecoverable from the UI). (v1.1 FF13.) */}
      {settings?.api_key_set && !confirmDelete && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={() => setConfirmDelete(true)}
          className="gap-1 text-xs"
          style={{ color: "var(--color-es-danger)" }}
        >
          <Trash2 size={12} />
          Remove API key
        </Button>
      )}
      {settings?.api_key_set && confirmDelete && (
        <div className="flex items-center gap-2">
          <span className="text-xs" style={{ color: "var(--color-es-text-muted)" }}>
            Remove the stored key?
          </span>
          <Button
            type="button"
            size="sm"
            disabled={busy}
            onClick={handleDelete}
            className="gap-1 text-xs"
            style={{ color: "var(--color-es-danger)" }}
          >
            {deleteApiKey.isPending ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <Trash2 size={12} />
            )}
            Remove
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={busy}
            onClick={() => setConfirmDelete(false)}
            className="text-xs"
          >
            Cancel
          </Button>
        </div>
      )}

      {/* Feedback */}
      {feedback && (
        <div
          className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs"
          style={{
            backgroundColor: "var(--color-es-surface-elevated)",
            color:
              feedback.type === "success"
                ? "var(--color-es-success)"
                : "var(--color-es-danger)",
          }}
        >
          {feedback.type === "error" && <AlertCircle size={12} />}
          {feedback.text}
        </div>
      )}
    </div>
  );
}
