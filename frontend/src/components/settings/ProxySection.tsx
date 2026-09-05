import { useState } from "react";
import {
  PANEL_DRAFT_KEYS,
  useDraftStore,
  usePanelDraft,
} from "@/lib/store/draftStore";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  useSettings,
  useSetProxy,
  useSetProxyAlias,
  useSetProxyRequired,
  useDeleteProxy,
  useProxyHealth,
  useRefreshProxyHealth,
} from "@/lib/query/settings";
import { parseApiError } from "@/lib/errors";
import { Shield, Trash2, RefreshCw, Check, AlertCircle, Loader2 } from "lucide-react";

/** How a feedback line is coloured, and the sibling section's table verbatim.
 *  ApiKeySection carries the same three-way split for the same reason: this
 *  panel's answers are not all "it worked" or "it failed", and `partial` means
 *  the delete ran while a copy of the secret outlived it. Same red as `error`
 *  on purpose, because both say the secret is still reachable; the sentence
 *  and the kind published on the box are what tell them apart.
 *
 *  Not exported: the react-refresh gate refuses a non-primitive export from a
 *  file that exports a component. The kind reaches the DOM on `data-feedback`
 *  instead, which is the better contract anyway. */
const FEEDBACK_COLOR = {
  success: "var(--color-es-success)",
  error: "var(--color-es-danger)",
  partial: "var(--color-es-danger)",
} as const;

type Feedback = { type: keyof typeof FEEDBACK_COLOR; text: string };

/**
 * What the panel says when removing the proxy left a copy behind.
 *
 * Exported as a string so the test proving it reaches the screen imports it
 * rather than restating it; a primitive is the one export the react-refresh
 * gate allows out of a component file. The URL is a secret in its own right,
 * and it is the shape most likely to carry credentials inside it, so a copy
 * the credential store would not release is the same class of problem as a
 * leftover API key.
 */
export const PROXY_COPY_LEFT_TEXT =
  "Proxy removed from the vault, but the OS credential store would not " +
  "release its copy of the URL. That copy is still readable on this machine; " +
  "delete it there by hand.";

export function ProxySection() {
  const {
    data: settings,
    isPending: settingsPending,
    isError: settingsIsError,
  } = useSettings();
  const {
    data: health,
    isPending: healthPending,
    isError: healthIsError,
  } = useProxyHealth();
  const setProxy = useSetProxy();
  const setRequired = useSetProxyRequired();
  const setAlias = useSetProxyAlias();
  const deleteProxy = useDeleteProxy();
  const refreshHealth = useRefreshProxyHealth();

  const [urlInput, setUrlInput] = useState("");
  // The one box in this panel whose contents are kept across a tab switch.
  // Its three neighbours - the URL above and the passphrases in the section
  // below - are credentials and stay component-local on purpose; the draft
  // store's header argues that exclusion in full. A label is not a secret.
  const [aliasInput, setAliasInput] = usePanelDraft(
    PANEL_DRAFT_KEYS.proxyAlias,
  );
  // Same reason as requiredDirty: the alias field is write-only too, so an
  // untouched box must mean "keep what is stored", not "clear it". Sending
  // `aliasInput.trim() || null` unconditionally wiped the stored alias on
  // every later URL-only update.
  //
  // DERIVED from the buffer rather than tracked beside it. A plain
  // `useState(false)` came back false after every tab switch, so a restored
  // alias would have been shown to the user and then not saved - a worse
  // failure than losing it outright, because the box looks like it worked.
  // A buffer exists exactly when the user has typed in this box.
  const aliasDirty = useDraftStore(
    (s) => s.panel[PANEL_DRAFT_KEYS.proxyAlias] != null,
  );
  const clearPanelDraft = useDraftStore((s) => s.clearPanelDraft);
  const clearAliasDraft = () => clearPanelDraft(PANEL_DRAFT_KEYS.proxyAlias);
  const [requiredToggle, setRequiredToggle] = useState(false);
  // Tracks whether the user touched the toggle since the last save. While NOT
  // dirty, the toggle mirrors the server value - this prevents a save that only
  // changes the URL from silently flipping proxy_required back to false.
  const [requiredDirty, setRequiredDirty] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);

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

  // A save is meaningful with a new URL, or - once a proxy exists - with
  // nothing but the kill-switch or the label moved. `aliasDirty` was left
  // out of both of these (KÖK 15), and there was no alias-only write path
  // at all, so naming an existing proxy was impossible: the URL is
  // write-only and never shown, and the box refused to save without it.
  const noUrl = !urlInput.trim();
  const configured = Boolean(settings?.proxy_configured);
  const fieldsOnlySave = noUrl && configured && (requiredDirty || aliasDirty);
  const canSave = Boolean(urlInput.trim()) || fieldsOnlySave;

  const handleSave = async () => {
    if (!canSave) return;
    setFeedback(null);
    try {
      if (fieldsOnlySave) {
        // The switch and/or the label, without the URL. Routing either
        // through the full proxy write meant retyping a URL nobody can see.
        //
        // BOTH are sent when both moved. The old branch fired on
        // requiredDirty alone and returned early, so changing the switch and
        // the name together wrote only the switch - and then reported
        // "Proxy is now required" over a name box still showing the text
        // that had just been dropped.
        const done: string[] = [];
        if (aliasDirty) {
          await setAlias.mutateAsync(aliasInput.trim() || null);
          // Dropping the buffer is what marks the box clean now, because
          // "clean" is derived from the buffer's absence. It empties the box
          // too, which the URL box above has always done on success - both
          // are write-only, and a box still showing text the server has
          // already taken invites a second, pointless save.
          clearAliasDraft();
          // Not "Name cleared" - an emptied box no longer deletes the
          // stored name, it asks for the default one back.
          done.push(
            aliasInput.trim() ? "Name saved" : "Name reset to the default",
          );
        }
        if (requiredDirty) {
          await setRequired.mutateAsync(requiredToggle);
          setRequiredDirty(false);
          done.push(
            requiredToggle ? "Proxy is now required" : "Proxy no longer required",
          );
        }
        setFeedback({ type: "success", text: done.join(" · ") });
        return;
      }
      await setProxy.mutateAsync({
        proxyUrl: urlInput.trim(),
        proxyRequired: requiredToggle,
        // Untouched box → preserve the stored alias instead of erasing it.
        proxyAlias: aliasDirty
          ? aliasInput.trim() || null
          : (settings?.proxy_alias ?? null),
      });
      setUrlInput(""); // Clear URL on success - write-only
      // Dropped, not emptied. An empty buffer is still a buffer, and this
      // box counts as edited whenever one exists - so writing "" here would
      // leave the next URL-only save convinced the user had asked for the
      // stored name to be cleared.
      clearAliasDraft();
      setRequiredDirty(false); // Saved value is now the server value - resume syncing
      setFeedback({ type: "success", text: "Proxy configured" });
    } catch (err) {
      setFeedback({ type: "error", text: parseApiError(err).message });
    }
  };

  const handleDelete = async () => {
    setFeedback(null);
    try {
      const result = await deleteProxy.mutateAsync();
      setRequiredDirty(false);
      clearAliasDraft();
      // The vault row is gone either way. `legacy_copy_left` is the server
      // saying the credential store kept its own copy of the URL, and until
      // the delete response got its own schema that answer was stripped on
      // the way in and this branch could not exist.
      setFeedback(
        result.legacy_copy_left
          ? { type: "partial", text: PROXY_COPY_LEFT_TEXT }
          : { type: "success", text: "Proxy removed" },
      );
    } catch (err) {
      setFeedback({ type: "error", text: parseApiError(err).message });
    }
  };

  const busy =
    setProxy.isPending ||
    setRequired.isPending ||
    setAlias.isPending ||
    deleteProxy.isPending;

  /**
   * Same collapse as ApiKeySection, on the "Proxy configured" line: while
   * GET /settings is still in flight, `settings?.proxy_configured` reads
   * false, and a user who already has a proxy configured briefly saw "No
   * proxy" - the muted dot made this milder than the danger-red key alarm,
   * but it is the identical wrong claim. `pending` and `error` get their own
   * branch so neither one can be read as the true unconfigured state.
   */
  const configuredStatus: "pending" | "error" | "configured" | "unconfigured" =
    settingsPending
      ? "pending"
      : settingsIsError
        ? "error"
        : settings?.proxy_configured
          ? "configured"
          : "unconfigured";

  /**
   * The health probe is a REAL network call to the user's own proxy, fired
   * only from this component - nothing prefetches it, so it lags the
   * settings fetch by tens of milliseconds in practice. `health?.healthy`
   * collapsing straight to false during that window painted a danger-red
   * "Unhealthy" over a proxy that was fine; this is the worst of the three
   * defects because it is the one that wears the actual alarm colour.
   */
  const healthStatus: "pending" | "error" | "healthy" | "unhealthy" =
    healthPending
      ? "pending"
      : healthIsError
        ? "error"
        : health?.healthy
          ? "healthy"
          : "unhealthy";

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
        <div className="flex items-center gap-2" data-state={configuredStatus}>
          {configuredStatus === "pending" ? (
            // Spinning, not coloured: the same idiom the Save/Delete buttons
            // below already use for "in flight", so "we do not know yet"
            // never borrows the static muted dot that means "no proxy".
            <Loader2
              size={8}
              className="animate-spin"
              style={{ color: "var(--color-es-text-muted)" }}
            />
          ) : (
            <div
              className="h-2 w-2 rounded-full"
              style={{
                backgroundColor:
                  configuredStatus === "configured"
                    ? "var(--color-es-success)"
                    : configuredStatus === "error"
                      ? "var(--color-es-danger)"
                      : "var(--color-es-text-muted)",
              }}
            />
          )}
          <span style={{ color: "var(--color-es-text-muted)" }}>
            {configuredStatus === "pending"
              ? "Checking…"
              : configuredStatus === "error"
                ? "Could not check whether a proxy is configured."
                : configuredStatus === "configured"
                  ? "Proxy configured"
                  : "No proxy"}
          </span>
        </div>
        {settings?.proxy_alias && (
          <div style={{ color: "var(--color-es-text-muted)" }}>
            Alias: {settings.proxy_alias}
          </div>
        )}
        <div
          data-testid="proxy-required"
          data-state={
            settingsPending
              ? "pending"
              : settingsIsError
                ? "error"
                : settings?.proxy_required ? "required" : "optional"
          }
          style={{ color: "var(--color-es-text-muted)" }}
        >
          {/* Same collapse as the two indicators above, and the same fix.
              "No" here means the app is free to reach the network without a
              proxy, which is the opposite of what a user who set one wants
              to read while the answer is still loading. */}
          Required: {settingsPending
            ? "checking"
            : settingsIsError
              ? "unknown"
              : settings?.proxy_required ? "Yes" : "No"}
        </div>
      </div>

      {/* Proxy health */}
      {settings?.proxy_configured && (
        <div
          className="flex items-center justify-between rounded-lg px-3 py-2 text-xs"
          style={{ backgroundColor: "var(--color-es-surface-elevated)" }}
        >
          <div className="flex items-center gap-2" data-state={healthStatus}>
            {healthStatus === "pending" ? (
              // This probe is a real round trip to the user's own proxy and
              // nothing prefetches it, so it is the slowest of the three
              // queries this screen waits on - the danger-red dot used to
              // sit here for tens of milliseconds on every load of a healthy
              // proxy. Spinning-and-muted instead of red-and-wrong.
              <Loader2
                size={8}
                className="animate-spin"
                data-testid="proxy-health-indicator"
                style={{ color: "var(--color-es-text-muted)" }}
              />
            ) : (
              <div
                className="h-2 w-2 rounded-full"
                data-testid="proxy-health-indicator"
                style={{
                  backgroundColor:
                    healthStatus === "healthy"
                      ? "var(--color-es-success)"
                      : "var(--color-es-danger)",
                }}
              />
            )}
            <span style={{ color: "var(--color-es-text-muted)" }}>
              {healthStatus === "pending"
                ? "Checking…"
                : healthStatus === "error"
                  ? "Could not check the proxy."
                  : healthStatus === "healthy"
                    ? "Healthy"
                    : "Unhealthy"}
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
          // No dirty flag to raise: writing the buffer IS what raises it.
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
          disabled={busy || !canSave}
          onClick={handleSave}
          /* Was an inline backgroundColor + --color-es-text-dark, which
             broke two things at once. It painted #1C2632 on #3E72B0 at
             3.09:1, under the 4.5:1 floor and against the theme law stated
             on .composer-send-button ("white-on-primary"); and an inline
             background outranks hover:bg-primary/80, so this was one of the
             two primary CTAs on the panel that did not react to the pointer
             at all. The default Button variant already IS bg-primary with
             text-primary-foreground (#FFFFFF, 4.95:1 AA), so the fix is to
             stop overriding it.

             Its stock hover is NOT kept: bg-primary/80 lightens toward this
             near-white panel and takes the label to 3.45:1, under the floor.
             index.css deepens it instead, scoped to .glass-right. */
          className="gap-1"
        >
          {setProxy.isPending || setRequired.isPending ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <Check size={12} />
          )}
          Save proxy
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
          Remove proxy
        </Button>
      )}

      {/* Feedback */}
      {feedback && (
        <div
          className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs"
          // Announced, the same way and for the same reason as the sibling
          // ApiKeySection's line: this box is the entire answer to arming a
          // kill-switch, and a switch whose result is silent leaves somebody
          // believing the block went on when it did not. `status` and not
          // `alert`, because one node cannot be both and the sibling already
          // settled which one this family uses.
          role="status"
          // The KIND of answer, in one machine-readable word. Without it the
          // only thing separating "saved" from "refused" here was the colour
          // of the text, which is the one signal a colour-blind reader loses
          // and no assistive technology reads at all. Same shape as
          // data-state on the indicators above.
          data-feedback={feedback.type}
          style={{
            backgroundColor: "var(--color-es-surface-elevated)",
            color: FEEDBACK_COLOR[feedback.type],
          }}
        >
          {/* A mark per kind, not a hue per kind. The error glyph was already
              here; success gets the same tick this panel's Save button uses,
              so the two outcomes differ by shape as well as colour. */}
          {feedback.type === "success" && <Check size={12} />}
          {feedback.type !== "success" && <AlertCircle size={12} />}
          {feedback.text}
        </div>
      )}
    </div>
  );
}
