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
  //
  // Seeded from the stored name, so the box SHOWS what the server holds and
  // an emptied box is a choice made in view of it: no name, back to the
  // default. The buffer wins the moment the user types (`stored ?? fallback`
  // in the store) and until then the box follows the server. It used to open
  // empty, with the stored name printed on a read-only line above it, so a
  // box that looked empty could reset a name the user never saw leave.
  const [aliasInput, setAliasInput] = usePanelDraft(
    PANEL_DRAFT_KEYS.proxyAlias,
    settings?.proxy_alias ?? "",
  );
  // Same reason as requiredDirty: an untouched box must mean "keep what is
  // stored", not "clear it". Sending `aliasInput.trim() || null`
  // unconditionally wiped the stored alias on every later URL-only update.
  //
  // DERIVED rather than tracked beside the box. A plain `useState(false)`
  // came back false after every tab switch, so a restored alias would have
  // been shown to the user and then not saved - a worse failure than losing
  // it outright, because the box looks like it worked. Dirty is the box
  // disagreeing with the stored name: a buffer that spells the stored name
  // again is not a change, and a server that holds no name compares as the
  // empty box it is shown as.
  const aliasDirty = aliasInput.trim() !== (settings?.proxy_alias ?? "");
  const clearPanelDraft = useDraftStore((s) => s.clearPanelDraft);
  const clearAliasDraft = () => clearPanelDraft(PANEL_DRAFT_KEYS.proxyAlias);
  // What the switch shows: the server's value, or the one the user moved it
  // to. It does NOT also carry "the server has not answered" - that reading
  // lives in `serverRequired` below and nowhere else, and the switch, the
  // Save button and the "Required:" line are all closed by it. Two places
  // answering one question is what let a URL-only save send an off nobody
  // chose, and a second copy of the unknown would be the same mistake again.
  const [requiredToggle, setRequiredToggle] = useState(false);
  // Tracks whether the user touched the toggle since the last save. While NOT
  // dirty, the toggle mirrors the server value - this prevents a save that only
  // changes the URL from silently flipping proxy_required back to false.
  const [requiredDirty, setRequiredDirty] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  // Render-time state adjustment (react.dev "you might not need an effect"):
  // whenever the server value changes and the toggle is not dirty, mirror it.
  //
  // Unknown on the same split the "Required:" line below draws: while the
  // fetch is out and after it failed, a stale answer in the cache included.
  // That line reads "checking" or "unknown" then, and a switch left live
  // beside it would be a second signal contradicting the first, so the
  // switch and the Save button are closed by this same reading until the
  // answer is back. A dirty switch keeps the user's choice in view and
  // reopens with it.
  const serverRequired: boolean | null =
    settingsPending || settingsIsError
      ? null
      : (settings?.proxy_required ?? null);
  // The one spelling of "no answer in hand". The switch, the Save button and
  // the "Required:" line all close on THIS, and none of them restates it in
  // its own terms. They used to: the switch tested `serverRequired == null`
  // while the other two tested `settingsPending || settingsIsError`, which
  // is the same set of states only because `proxy_required` is a bare
  // boolean in the schema. Give it a `.default()` the way five of its
  // neighbours already have and a successful fetch can hand back null - and
  // then the switch closes, the button reopens, the line prints "No", and a
  // click does nothing at all. One term, one reading, no drift.
  const answerUnknown = serverRequired == null;
  const [syncedServerRequired, setSyncedServerRequired] = useState<boolean | null>(null);
  if (serverRequired !== syncedServerRequired) {
    setSyncedServerRequired(serverRequired);
    // Only an answer is written into the switch. With none in hand the
    // switch keeps the last value it showed, which is what stays in view
    // while it is closed; what stops that value from being SENT is
    // `answerUnknown` at the Save button, and `serverRequired` itself in
    // handleSave, where the null check is also what narrows the type.
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
    // Not reachable from the screen: the button is closed on this same
    // reading. It stands anyway, because the body below is written from the
    // server's answer and there is none to write it from here -
    // `serverRequired` is null while the fetch is out or failed, a stale
    // answer in the cache included. Nothing is sent and nothing is guessed
    // in its place. `settings` is named beside it because the full save
    // below reads the stored name straight off it, and this line is what
    // tells the compiler it is there.
    if (settings == null || serverRequired == null) {
      return;
    }
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
          // Dropping the buffer hands the box back to the stored name, which
          // the cache carries until the refetch lands: a name just typed
          // stays in view, and an emptied box shows the old name until the
          // server's default arrives to replace it. Nothing blinks to blank.
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
        // An untouched box spells the stored name, so the stored name goes
        // back; an emptied one sends null and the server answers with its
        // default. Read off `settings` itself, never a fallback: with no
        // answer in hand this line is not reached.
        proxyAlias: aliasDirty ? aliasInput.trim() || null : settings.proxy_alias,
      });
      setUrlInput(""); // Clear URL on success - write-only
      // Dropped, not emptied. The box follows the stored name again, and an
      // empty buffer left here would read as "reset the name" on the next
      // URL-only save.
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
        <div
          data-testid="proxy-required"
          // "error" is this attribute's spelling of the unknown reading, and
          // it keeps that spelling: the word the line prints is "unknown",
          // and every state the attribute has ever carried it in is a failed
          // fetch. What changed is where the branch gets its answer -
          // `answerUnknown`, the same term the switch and the button read.
          data-state={
            settingsPending
              ? "pending"
              : answerUnknown
                ? "error"
                : serverRequired ? "required" : "optional"
          }
          style={{ color: "var(--color-es-text-muted)" }}
        >
          {/* Same collapse as the two indicators above, and the same fix.
              "No" here means the app is free to reach the network without a
              proxy, which is the opposite of what a user who set one wants
              to read while the answer is still loading. */}
          Required: {settingsPending
            ? "checking"
            : answerUnknown
              ? "unknown"
              : serverRequired ? "Yes" : "No"}
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
            // Closed, not merely unchecked, while the server's answer is not
            // in hand: the "Required:" line above says "checking" or
            // "unknown" at that moment, and a live switch beside it would let
            // the user believe a change was made that cannot be sent. The
            // test is `answerUnknown`, the one term the line and the Save
            // button read too, and never the value on show: a switch the
            // user has moved keeps that value through a failed refetch, and
            // a gate read off the value left exactly that switch live beside
            // a closed button. The value stays in view; the control does not
            // stay open.
            disabled={busy || answerUnknown}
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
          // Closed on `answerUnknown`, the switch's own term: both writes
          // this button makes carry what the server holds back to it, the
          // requirement through the switch and the name through the box, and
          // with no answer in hand a save would carry a guess. Restating the
          // condition here as "pending or error" is what let the button and
          // the switch drift apart on a schema change.
          disabled={busy || !canSave || answerUnknown}
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
