import { useState } from "react";
import { z } from "zod/v4";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useSettings, useSetApiKey, useDeleteApiKey } from "@/lib/query/settings";
import { request } from "@/lib/api/client";
import { parseApiError } from "@/lib/errors";
import {
  Key,
  Trash2,
  Check,
  AlertCircle,
  HelpCircle,
  RefreshCw,
  Loader2,
} from "lucide-react";

/** How a feedback line is coloured. Three kinds, not two, and that is the
 *  whole point of this section: "rejected" and "we never got an answer" are
 *  opposite facts, so they must not arrive wearing the same red.
 *
 *  Not exported, and not because nobody wants it: the react-refresh gate
 *  refuses a non-primitive export from a file that exports a component. The
 *  kind is published to the DOM instead, on `data-feedback`, which is the
 *  house pattern already (`data-state` on the context meter) and is a better
 *  contract anyway - a reader of the rendered page can tell the three apart,
 *  not only a reader of this module. */
const FEEDBACK_COLOR = {
  success: "var(--color-es-success)",
  error: "var(--color-es-danger)",
  unknown: "var(--color-es-warning)",
} as const;

type Feedback = { type: keyof typeof FEEDBACK_COLOR; text: string };

/**
 * The answers POST /settings/api-key/check can give.
 *
 * Parsed strictly rather than read as a loose string, because the failure mode
 * of leniency here is the worst one available: an unrecognised word must never
 * fall through to "it works". An enum miss becomes `invalid_response_shape`
 * from client.ts, which reaches the reader as a real error instead of a
 * cheerful lie.
 */
const KeyCheckSchema = z.object({
  key_status: z.enum(["valid", "invalid", "validation_unavailable", "not_set"]),
});

/**
 * One sentence per verdict, written once, here.
 *
 * Kept as a table rather than as branches inside the handler so that the thing
 * this feature exists for is visible in a single glance: no two of these
 * sentences say the same thing, and the second and third are not variations of
 * one failure. A rejected key has to be replaced. An unreachable provider says
 * nothing about the key at all, and a user who throws away a good key because
 * their proxy was down has been actively misled.
 */
const VERDICTS: Record<
  z.infer<typeof KeyCheckSchema>["key_status"],
  Feedback
> = {
  valid: {
    type: "success",
    text: "OpenRouter accepted the stored key.",
  },
  invalid: {
    type: "error",
    text: "OpenRouter rejected the stored key. Save a new one to keep going.",
  },
  validation_unavailable: {
    type: "unknown",
    text:
      "Could not reach OpenRouter, so the key was not checked. This says " +
      "nothing about the key itself. Check your connection or proxy.",
  },
  not_set: {
    type: "unknown",
    text: "There is no stored key to check.",
  },
};

export function ApiKeySection() {
  const {
    data: settings,
    isPending: settingsPending,
    isError: settingsIsError,
  } = useSettings();
  const setApiKey = useSetApiKey();
  const deleteApiKey = useDeleteApiKey();
  const [keyInput, setKeyInput] = useState("");
  const [feedback, setFeedback] = useState<Feedback | null>(null);

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

  const [checking, setChecking] = useState(false);

  /**
   * Ask the backend whether the key ALREADY STORED is still accepted.
   *
   * Local state and a direct call rather than a mutation hook in
   * lib/query/settings.ts, because there is nothing here for the query cache
   * to hold: the answer is a fact about this moment, it invalidates nothing,
   * and caching it would let a stale "works" outlive the key it described.
   * The call still goes through request(), so it carries the launch token and
   * routes 423 into the vault-lock signal like every other call in the app.
   *
   * The key is not sent from here and is not returned. This request has no
   * body at all; the backend reads the stored secret itself and answers with a
   * verdict, which is the only reason a control like this can exist on a
   * screen where the key field is write-only.
   */
  const handleCheck = async () => {
    setFeedback(null);
    setChecking(true);
    try {
      const result = await request("/settings/api-key/check", KeyCheckSchema, {
        method: "POST",
      });
      setFeedback(VERDICTS[result.key_status]);
    } catch (err) {
      // Reaching here means the CHECK failed, not the key: a 503 from the
      // proxy gate, a locked vault, an unreachable backend. parseApiError
      // names those; none of them is a verdict on the key, so none of them
      // may borrow one of the sentences above.
      setFeedback({ type: "error", text: parseApiError(err).message });
    } finally {
      setChecking(false);
    }
  };

  const busy = setApiKey.isPending || deleteApiKey.isPending || checking;

  /**
   * Three real answers, not two: "GET /settings has not landed yet" and "no
   * key is stored" used to render as the same red dot, so a user whose key
   * WAS set saw a false alarm about their own machine for the ~30ms before
   * the query resolved. `pending` gets its own branch below so it can never
   * borrow the danger colour or the "No API key configured" sentence, and
   * `error` gets its own branch so a failed fetch does not get read as a
   * verdict on the key either - see Composer.tsx's settingsLoading /
   * settingsBroken split, which this mirrors.
   */
  const keyStatus: "pending" | "error" | "set" | "unset" = settingsPending
    ? "pending"
    : settingsIsError
      ? "error"
      : settings?.api_key_set
        ? "set"
        : "unset";

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

      {/* Status. `data-state` publishes the same four-way verdict the
          copy below is drawn from, so a reader of the rendered page (a test
          included) can tell "we do not know yet" apart from "no key is set"
          without parsing English. */}
      <div
        className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs"
        data-state={keyStatus}
        style={{ backgroundColor: "var(--color-es-surface-elevated)" }}
      >
        {keyStatus === "pending" ? (
          // The spinning idiom this file already uses for every in-flight
          // mutation (Save, Remove, Test stored key) - not a colour, so it
          // can never be mistaken for a verdict.
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
                keyStatus === "set"
                  ? "var(--color-es-success)"
                  : "var(--color-es-danger)",
            }}
          />
        )}
        <span style={{ color: "var(--color-es-text-muted)" }}>
          {keyStatus === "pending"
            ? "Checking for a stored key…"
            : keyStatus === "error"
              ? "Could not check whether a key is stored."
              : keyStatus === "set"
                ? "API key is set"
                : "No API key configured"}
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

      {/* Check the STORED key. The Save button above validates a key while it
          is being typed, which is the one moment the answer is already known.
          The key that quietly stops working is the one saved last month, and
          until this control existed the only ways to find that out were to
          send a message and read the failure, or to retype the whole key -
          which tests nothing, it just saves it again.

          Shown only when a key is stored: an offer to test nothing is a dead
          button, and the panel already knows the answer from api_key_set. */}
      {settings?.api_key_set && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={handleCheck}
          className="gap-1 text-xs"
        >
          {checking ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <RefreshCw size={12} />
          )}
          Test stored key
        </Button>
      )}

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
          // Announced, because this line is the entire answer to a button
          // press and it appears somewhere the eye is not: a screen reader
          // user pressing "Test stored key" would otherwise get silence back.
          role="status"
          // The KIND of answer, in one machine-readable word. Colour and icon
          // say it to a sighted reader; this says it to anything else, and it
          // is what keeps "rejected" and "could not check" distinguishable
          // without anybody parsing an English sentence to find out which
          // happened. Same shape as data-state on the context meter.
          data-feedback={feedback.type}
          style={{
            backgroundColor: "var(--color-es-surface-elevated)",
            color: FEEDBACK_COLOR[feedback.type],
          }}
        >
          {/* A different mark for the third kind, not just a different colour.
              "We could not ask" is not a milder red; it is a question mark,
              and colour alone is the one signal a colour-blind reader loses.
              The icons stay 12px lucide glyphs like every other one here. */}
          {feedback.type === "error" && <AlertCircle size={12} />}
          {feedback.type === "unknown" && <HelpCircle size={12} />}
          {feedback.text}
        </div>
      )}
    </div>
  );
}
