import { useEffect, useRef, useState } from "react";
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

/** How a feedback line is coloured. Four kinds, not two, and that is the
 *  whole point of this section: "rejected" and "we never got an answer" are
 *  opposite facts, so they must not arrive wearing the same red.
 *
 *  `partial` DOES wear the same red as `error`, and that is deliberate rather
 *  than a lapse in the rule above. It means the delete ran and a copy of the
 *  secret outlived it, so the fact it reports is the one `error` reports: the
 *  key is still reachable. What tells the two apart is the sentence and the
 *  kind published beside it, not the hue.
 *
 *  Not exported, and not because nobody wants it: the react-refresh gate
 *  refuses a non-primitive export from a file that exports a component. The
 *  kind is published to the DOM instead, on `data-feedback`, which is the
 *  house pattern already (`data-state` on the context meter) and is a better
 *  contract anyway - a reader of the rendered page can tell the four apart,
 *  not only a reader of this module. */
const FEEDBACK_COLOR = {
  success: "var(--color-es-success)",
  error: "var(--color-es-danger)",
  partial: "var(--color-es-danger)",
  unknown: "var(--color-es-warning)",
} as const;

type Feedback = { type: keyof typeof FEEDBACK_COLOR; text: string };

/**
 * What the panel says when the delete left a copy behind.
 *
 * A string, and exported, so the test that proves this sentence reaches the
 * screen can import it instead of holding its own copy: a test carrying its
 * own wording goes green on the day the wording and the branch stop agreeing,
 * which is the only day it was needed. A primitive export is the one kind the
 * react-refresh gate allows out of a component file.
 *
 * The wording says three things because the reader has to act on all three:
 * the vault row IS gone, a copy is NOT, and only the person at the keyboard
 * can finish the job. "API key removed" alone is the failure the server's
 * legacy_copy_left field exists to end.
 */
export const API_KEY_COPY_LEFT_TEXT =
  "API key removed from the vault, but the OS credential store would not " +
  "release its copy. That copy is still readable on this machine; delete it " +
  "there by hand.";

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

  /**
   * Where focus goes when the thing holding it is taken away.
   *
   * Every step of the delete removes the element the keyboard is standing on:
   * opening the confirm unmounts the trigger, and finishing or refusing the
   * delete unmounts the confirm row. The browser's answer to that is
   * document.body, i.e. the top of the page, which is where a keyboard user
   * used to land after destroying their own API key.
   *
   * Refs and not autoFocus, because the same button is also reached with a
   * pointer and autoFocus would fire on a render nobody asked for.
   */
  const triggerRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const feedbackRef = useRef<HTMLDivElement>(null);

  /**
   * Whether the NEXT feedback line is the answer to a delete.
   *
   * A ref rather than a branch inside handleDelete, because the line does not
   * exist yet when the handler returns; and rather than focusing on every
   * feedback change, because Save and "Test stored key" leave focus on a
   * control that is still on screen and moving it would be stealing it. Set
   * immediately before the delete's own setFeedback, never before the null
   * that clears the previous line.
   */
  const focusFeedbackNext = useRef(false);

  const handleDelete = async () => {
    setFeedback(null);
    setConfirmDelete(false);
    try {
      const result = await deleteApiKey.mutateAsync();
      focusFeedbackNext.current = true;
      // The vault row is gone either way. `legacy_copy_left` is the server
      // saying the credential store kept its own copy, and the person who
      // pressed this button pressed it because a key leaked, so "removed" on
      // its own would be the wrong answer to the one press that cannot afford
      // one. The field used to be stripped by the response schema.
      setFeedback(
        result.legacy_copy_left
          ? { type: "partial", text: API_KEY_COPY_LEFT_TEXT }
          : { type: "success", text: "API key removed" },
      );
    } catch (err) {
      focusFeedbackNext.current = true;
      setFeedback({ type: "error", text: parseApiError(err).message });
    }
  };

  /** Whether the NEXT close of the confirm row is a cancel.
   *
   *  The close that follows a delete is not: the row goes away there too, but
   *  the answer to the delete is what should hold focus, and two handlers
   *  reaching for it in the same commit is a flicker at best. Only the Cancel
   *  button raises this. */
  const focusTriggerNext = useRef(false);

  const cancelDelete = () => {
    focusTriggerNext.current = true;
    setConfirmDelete(false);
  };

  // Both sides of the confirm row, in the commit that rebuilt it. Opening it
  // unmounts the trigger that had focus; cancelling unmounts the row that had
  // it. Either way the browser's own answer is document.body, so the row and
  // the trigger take it back in turn.
  useEffect(() => {
    if (confirmDelete) {
      confirmRef.current?.focus();
      return;
    }
    if (focusTriggerNext.current) {
      focusTriggerNext.current = false;
      triggerRef.current?.focus();
    }
  }, [confirmDelete]);

  // The answer to a delete, once it is on screen. `tabIndex={-1}` on the box
  // is what makes this possible; the box is already the announced one, so
  // focus and announcement land in the same place instead of two.
  useEffect(() => {
    if (!focusFeedbackNext.current) return;
    focusFeedbackNext.current = false;
    feedbackRef.current?.focus();
  }, [feedback]);

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
          className="text-ui font-semibold"
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
        className="flex items-center gap-2 rounded-lg px-3 py-2 text-label"
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
          className="flex-1 text-label"
          aria-label="API key input"
          // Keep the browser password manager out: this is an API key field,
          // not a login - no fill offers, no save-password prompts.
          autoComplete="off"
        />
        <Button
          type="submit"
          size="sm"
          disabled={busy || !keyInput.trim()}
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
          className="gap-1 text-label"
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
          ref={triggerRef}
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={() => setConfirmDelete(true)}
          className="gap-1 text-label"
          style={{ color: "var(--color-es-danger)" }}
        >
          <Trash2 size={12} />
          Remove API key
        </Button>
      )}
      {settings?.api_key_set && confirmDelete && (
        <div className="flex items-center gap-2">
          <span className="text-label" style={{ color: "var(--color-es-text-muted)" }}>
            Remove the stored key?
          </span>
          {/* variant="ghost" like the trigger one branch up. Without it the
              Button falls back to bg-primary and painted --color-es-danger
              (rose) on the blue fill at 1.11:1: the word "Remove" on the
              control that destroys the stored API key was not legible, at
              rest or on hover. Danger is ink in this app, never a filled
              button. */}
          <Button
            type="button"
            ref={confirmRef}
            variant="ghost"
            size="sm"
            disabled={busy}
            onClick={handleDelete}
            className="gap-1 text-label"
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
            onClick={cancelDelete}
            className="text-label"
          >
            Cancel
          </Button>
        </div>
      )}

      {/* Feedback */}
      {feedback && (
        <div
          className="flex items-center gap-2 rounded-lg px-3 py-2 text-label"
          ref={feedbackRef}
          // Announced, because this line is the entire answer to a button
          // press and it appears somewhere the eye is not: a screen reader
          // user pressing "Test stored key" would otherwise get silence back.
          role="status"
          // Focusable only on purpose, never in the tab order. The delete
          // takes away the button the keyboard was standing on, and this is
          // where it is put down instead: the one element on the panel that
          // says what just happened. -1 keeps it out of everybody else's
          // tabbing, so nothing changes for a reader who never deletes.
          tabIndex={-1}
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
              The icons stay 12px lucide glyphs like every other one here.
              `partial` borrows the error glyph on purpose: a copy of the key
              left on the machine is an alarm, not a milder shade of one, and
              the sentence beside it is what says which alarm. */}
          {(feedback.type === "error" || feedback.type === "partial") && (
            <AlertCircle size={12} />
          )}
          {feedback.type === "unknown" && <HelpCircle size={12} />}
          {feedback.text}
        </div>
      )}
    </div>
  );
}
