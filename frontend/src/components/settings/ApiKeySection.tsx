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
        setKeyInput(""); // Clear on success — write-only
        setFeedback({ type: "success", text: "API key saved" });
      } else {
        setFeedback({
          type: "error",
          text: "API key validation unavailable; key was not saved",
        });
      }
    } catch (err) {
      setFeedback({ type: "error", text: parseApiError(err).message });
    }
  };

  const handleDelete = async () => {
    setFeedback(null);
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
          OpenRouter API Key
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

      {/* Input — write-only, never shows saved key */}
      <div className="flex gap-2">
        <Input
          type="password"
          placeholder="sk-or-v1-..."
          value={keyInput}
          onChange={(e) => setKeyInput(e.target.value)}
          disabled={busy}
          className="flex-1 text-xs"
          aria-label="API key input"
        />
        <Button
          size="sm"
          disabled={busy || !keyInput.trim()}
          onClick={handleSave}
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
      </div>

      {/* Delete */}
      {settings?.api_key_set && (
        <Button
          variant="ghost"
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
          Remove API Key
        </Button>
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
