/**
 * CopyMessageButton - put what the reader is looking at on the clipboard.
 *
 * WHY THIS EXISTS AT ALL. Until now there was no way to get text out of a
 * message: pywebview ships with text_select=False, which injects
 * `body {user-select: none}` into every page, and the WebView2 context menu
 * is bound to the debug flag (off), so there was no right-click "Copy"
 * either. Selection is being turned on alongside this button, but the two
 * are not alternatives - a drag-select copies what the DOM renders, and
 * parseMessage deliberately drops the delimiting asterisks of narration, so
 * only a button reading the message body can honour the promise Settings
 * already makes: "Copying a message always copies the original text,
 * asterisks included."
 *
 * WHAT IT COPIES, and why the caller passes it in. The text is `paneText`
 * from MessageBubble - `streamingText ?? shownMessage.content` - which is
 * the string actually on screen. NOT `message.content`: while browsing old
 * variants (isViewOnly) the active row and the shown row differ, and during
 * streaming `message.content` is the PREVIOUS variant in full. Copying that
 * would hand over a message the reader never saw, silently. This file has
 * paid for that mistake twice already, both confessed in MessageBubble
 * comments: attachments read from the active row put one variant's picture
 * over another's words, and KÖK 15 had Speak reading text B aloud while the
 * screen showed text A.
 *
 * FEEDBACK IS LOCAL, NEVER A TOAST. The error store dedupes on code+message
 * against the visible toasts, so a fixed "Copied" would match the previous
 * one and be dropped entirely - the reader would see a confirmation on the
 * first copy and silence on the next nineteen (the shape recorded as K-21).
 * Component-local state has no such ceiling: three messages copied in a row
 * show three ticks.
 *
 * FAILURE DOES NOT SELF-CLEAR. A confirmation is transient, a refusal is
 * not. navigator.clipboard rejects when the document is not focused, and
 * swallowing that would leave the reader pasting stale clipboard content
 * believing the copy worked. Same reasoning as AutoLockControl's inline
 * alert, and the opposite of the `.catch(() => undefined)` recorded as K-22.
 */
import { useEffect, useRef, useState } from "react";
import { AlertCircle, Check, Copy } from "lucide-react";

/**
 * How long the tick stays. Exported so the test reads THIS number instead of
 * keeping its own copy - the auto-dismiss test in ErrorToastStack held a
 * private 4500 and stayed green when production dropped to 1200 (K-25).
 */
export const COPY_FEEDBACK_MS = 2000;

type CopyState = "idle" | "copied" | "failed";

export function CopyMessageButton({
  text,
  isUser,
}: {
  text: string;
  isUser: boolean;
}) {
  const [state, setState] = useState<CopyState>("idle");

  // One timer, cleared on re-click AND on unmount. MessageBubble unmounts on
  // chat switch, message delete and variant change, so an uncleared timer
  // would call setState on a row that no longer exists.
  //
  // The token is not decoration. The first version cleared the ref before
  // `await` and assigned after it, so two clicks that overlapped - a held
  // Enter key repeats every ~30ms - both saw a null ref, cleared nothing,
  // and the FIRST timer's callback then nulled the ref belonging to the
  // SECOND. The live timer lost its handle and the unmount cleanup had
  // nothing left to clear. Comparing identity before nulling fixes that.
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inFlightRef = useRef(false);
  useEffect(
    () => () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
    },
    [],
  );

  const handleCopy = async () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    if (timerRef.current !== null) clearTimeout(timerRef.current);
    try {
      // First statement in the handler, deliberately. Chromium refuses a
      // clipboard write when the document is not focused, and anything
      // awaited before this point is a chance for focus to move.
      const clipboard = navigator.clipboard;
      if (clipboard?.writeText == null) throw new Error("no clipboard");
      await clipboard.writeText(text);
      setState("copied");
      const token = setTimeout(() => {
        // Only clear the handle if it is still OURS.
        if (timerRef.current === token) timerRef.current = null;
        setState("idle");
      }, COPY_FEEDBACK_MS);
      timerRef.current = token;
    } catch {
      setState("failed");
    } finally {
      inFlightRef.current = false;
    }
  };

  const label = isUser ? "Copy your message" : "Copy reply";

  // Nothing on screen, nothing to copy. Two real cases reach this, and both
  // used to end with `writeText("")` - which Chromium resolves SUCCESSFULLY,
  // WIPING whatever the reader had on the clipboard, and then the tick said
  // it had worked:
  //   1. A reply that is only a generated picture. The backend keeps that row
  //      deliberately (completions.py: `and not generated`), so its text is
  //      genuinely empty.
  //   2. The gap between "a new variant is streaming" and its first delta,
  //      where the bubble shows dots. MessageBubble hands an empty string for
  //      that window rather than paneText, because paneText there is still
  //      the PREVIOUS variant in full - the same trap KÖK 15 caught Speak in.
  const empty = text.trim().length === 0;

  return (
    <>
      <button
        type="button"
        className="message-action-button"
        disabled={empty}
        // The NAME stays put through every state. Speak renames itself
        // because its action changes (speak, then stop); copying is the same
        // action whatever happened last time, and a control that loses its
        // name for two seconds is a control a screen reader user cannot find
        // again. The outcome travels by title and by the live region below.
        aria-label={label}
        title={
          empty
            ? "Nothing to copy yet"
            : state === "copied"
              ? "Copied"
              : state === "failed"
                ? "Could not reach the clipboard. Click the window, then try again"
                : `${label} text`
        }
        onClick={() => void handleCopy()}
      >
        {/* One button, three faces, never remounted: unmounting a focused
            control drops focus to <body>, the lesson the variant arrows
            carry a few hundred lines up.

            The refusal is a FACE, not a paragraph. A <p> here would become a
            flex child of `.message-actions` - the floating strip pinned to
            the bubble's top right - where it would spill over the words and,
            worse, inherit the strip's resting `opacity: .16`. A warning
            nobody can read is the silence this was written to avoid. */}
        {state === "copied" ? (
          <Check size={13} />
        ) : state === "failed" ? (
          <AlertCircle size={13} />
        ) : (
          <Copy size={13} />
        )}
      </button>
      {/* Mounted empty and filled later, never rendered conditionally: a live
          region that arrives together with its text is not announced.
          `aria-live` WITHOUT `role="status"`, and that is not an oversight -
          the two are equivalent for announcement, but one of these sits in
          every bubble, and adding the role made getByRole("status") ambiguous
          for the thinking indicator, breaking five SendFlow tests. A new
          control does not get to make existing queries worse. */}
      <span className="sr-only" aria-live="polite" aria-atomic="true">
        {state === "copied"
          ? "Copied to the clipboard"
          : state === "failed"
            ? "Could not reach the clipboard. Click the window, then try again."
            : ""}
      </span>
    </>
  );
}
