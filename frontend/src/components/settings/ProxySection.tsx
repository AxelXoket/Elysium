import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  useSettings,
  useSetProxy,
  useDeleteProxy,
  useProxyHealth,
  useRefreshProxyHealth,
} from "@/lib/query/settings";
import { parseApiError } from "@/lib/errors";
import { Shield, Trash2, RefreshCw, Check, AlertCircle, Loader2 } from "lucide-react";

export function ProxySection() {
  const { data: settings } = useSettings();
  const { data: health } = useProxyHealth();
  const setProxy = useSetProxy();
  const deleteProxy = useDeleteProxy();
  const refreshHealth = useRefreshProxyHealth();

  const [urlInput, setUrlInput] = useState("");
  const [aliasInput, setAliasInput] = useState("");
  const [requiredToggle, setRequiredToggle] = useState(false);
  // Tracks whether the user touched the toggle since the last save. While NOT
  // dirty, the toggle mirrors the server value - this prevents a save that only
  // changes the URL from silently flipping proxy_required back to false.
  const [requiredDirty, setRequiredDirty] = useState(false);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // Render-time state adjustment (react.dev "you might not need an effect"):
  // whenever the server value changes and the toggle is not dirty, mirror it.
  const serverRequired = settings?.proxy_required ?? null;
  const [syncedServerRequired, setSyncedServerRequired] = useState<boolean | null>(null);
  if (serverRequired !== syncedServerRequired) {
    setSyncedServerRequired(serverRequired);
    if (!requiredDirty && serverRequired != null) {
      setRequiredToggle(serverRequired);
    }
  }

  const handleRequiredChange = (checked: boolean) => {
    setRequiredDirty(true);
    setRequiredToggle(checked);
  };

  const handleSave = async () => {
    if (!urlInput.trim()) return;
    setFeedback(null);
    try {
      await setProxy.mutateAsync({
        proxyUrl: urlInput.trim(),
        proxyRequired: requiredToggle,
        proxyAlias: aliasInput.trim() || null,
      });
      setUrlInput(""); // Clear URL on success - write-only
      setAliasInput("");
      setRequiredDirty(false); // Saved value is now the server value - resume syncing
      setFeedback({ type: "success", text: "Proxy configured" });
    } catch (err) {
      setFeedback({ type: "error", text: parseApiError(err).message });
    }
  };

  const handleDelete = async () => {
    setFeedback(null);
    try {
      await deleteProxy.mutateAsync();
      setRequiredDirty(false);
      setFeedback({ type: "success", text: "Proxy removed" });
    } catch (err) {
      setFeedback({ type: "error", text: parseApiError(err).message });
    }
  };

  const busy = setProxy.isPending || deleteProxy.isPending;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Shield size={14} style={{ color: "var(--color-es-mist-blue)" }} />
        <h3
          className="text-sm font-medium"
          style={{ color: "var(--color-es-text-light)" }}
        >
          Proxy
        </h3>
      </div>

      {/* Status */}
      <div
        className="space-y-1 rounded-lg px-3 py-2 text-xs"
        style={{ backgroundColor: "var(--color-es-surface-elevated)" }}
      >
        <div className="flex items-center gap-2">
          <div
            className="h-2 w-2 rounded-full"
            style={{
              backgroundColor: settings?.proxy_configured
                ? "var(--color-es-success)"
                : "var(--color-es-text-muted)",
            }}
          />
          <span style={{ color: "var(--color-es-text-muted)" }}>
            {settings?.proxy_configured ? "Proxy configured" : "No proxy"}
          </span>
        </div>
        {settings?.proxy_alias && (
          <div style={{ color: "var(--color-es-text-muted)" }}>
            Alias: {settings.proxy_alias}
          </div>
        )}
        <div style={{ color: "var(--color-es-text-muted)" }}>
          Required: {settings?.proxy_required ? "Yes" : "No"}
        </div>
      </div>

      {/* Proxy health */}
      {settings?.proxy_configured && (
        <div
          className="flex items-center justify-between rounded-lg px-3 py-2 text-xs"
          style={{ backgroundColor: "var(--color-es-surface-elevated)" }}
        >
          <div className="flex items-center gap-2">
            <div
              className="h-2 w-2 rounded-full"
              data-testid="proxy-health-indicator"
              style={{
                backgroundColor: health?.healthy
                  ? "var(--color-es-success)"
                  : "var(--color-es-danger)",
              }}
            />
            <span style={{ color: "var(--color-es-text-muted)" }}>
              {health?.healthy ? "Healthy" : "Unhealthy"}
              {health?.latency_ms != null && ` · ${health.latency_ms}ms`}
              {health?.cached && " · cached"}
            </span>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => refreshHealth.mutate()}
            disabled={refreshHealth.isPending}
            className="h-6 w-6 p-0"
            aria-label="Refresh proxy health"
          >
            <RefreshCw
              size={12}
              className={refreshHealth.isPending ? "animate-spin" : ""}
            />
          </Button>
        </div>
      )}

      {/* Configure */}
      <div className="space-y-2">
        <Input
          type="url"
          placeholder="https://proxy.example.com"
          value={urlInput}
          onChange={(e) => setUrlInput(e.target.value)}
          disabled={busy}
          className="text-xs"
          aria-label="Proxy URL input"
        />
        <Input
          type="text"
          placeholder="Alias (optional)"
          value={aliasInput}
          onChange={(e) => setAliasInput(e.target.value)}
          disabled={busy}
          className="text-xs"
          aria-label="Proxy alias input"
        />
        <div className="flex items-center gap-2">
          <Switch
            checked={requiredToggle}
            onCheckedChange={handleRequiredChange}
            disabled={busy}
            aria-label="Proxy required toggle"
          />
          <span
            className="text-xs"
            style={{ color: "var(--color-es-text-muted)" }}
          >
            Require proxy
          </span>
        </div>
        <Button
          size="sm"
          disabled={busy || !urlInput.trim()}
          onClick={handleSave}
          className="gap-1"
          style={{
            backgroundColor: "var(--color-es-primary-sage)",
            color: "var(--color-es-text-dark)",
          }}
        >
          {setProxy.isPending ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <Check size={12} />
          )}
          Save Proxy
        </Button>
      </div>

      {/* Delete */}
      {settings?.proxy_configured && (
        <Button
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={handleDelete}
          className="gap-1 text-xs"
          style={{ color: "var(--color-es-danger)" }}
        >
          {deleteProxy.isPending ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <Trash2 size={12} />
          )}
          Remove Proxy
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
