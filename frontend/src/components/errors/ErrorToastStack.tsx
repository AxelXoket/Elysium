import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AlertCircle, X } from "lucide-react";
import { useErrorStore } from "@/lib/errors";
import type { ErrorEvent } from "@/lib/errors";

const AUTO_DISMISS_MS = 4_500;

/** Reading time for a longer message, on top of the base window.
 *
 *  The toast used to truncate to one line, and an audit measured the cost:
 *  26 of the catalogue's sentences did not fit, and because every one of them
 *  is written problem-first, the half that vanished was always what to DO.
 *  The safeword message - the one somebody reads at the worst moment they
 *  expect to have - was cut mid-clause.
 *
 *  So the text wraps, and a message that takes longer to read gets longer to
 *  be read. Roughly 220ms per line at a comfortable pace, capped so a toast
 *  cannot camp on the screen.
 */
const READING_MS_PER_40_CHARS = 900;
/** The ceiling on all of the above, exported because it is the only honest
 *  answer to "by when must any toast be gone" - a test that retypes a window
 *  goes on passing after the window it names has moved. */
export const MAX_DISMISS_MS = 14_000;

function dismissDelayFor(message: string): number {
  const extra = Math.floor(message.length / 40) * READING_MS_PER_40_CHARS;
  return Math.min(MAX_DISMISS_MS, AUTO_DISMISS_MS + extra);
}
export const EXIT_ANIMATION_MS = 180;

export function ErrorToastStack() {
  const errors = useErrorStore((s) => s.errors);
  const dismiss = useErrorStore((s) => s.dismiss);
  const [exitingIds, setExitingIds] = useState<Set<string>>(() => new Set());

  const closeToast = useCallback(
    (id: string) => {
      setExitingIds((current) => {
        if (current.has(id)) return current;
        const next = new Set(current);
        next.add(id);
        return next;
      });

      window.setTimeout(() => {
        dismiss(id);
        // Prune the id once its toast is gone - ids are unique and never
        // reused, so this keeps the set from growing without a sync effect.
        setExitingIds((current) => {
          if (!current.has(id)) return current;
          const next = new Set(current);
          next.delete(id);
          return next;
        });
      }, EXIT_ANIMATION_MS);
    },
    [dismiss],
  );

  /*
   * Portalled to the body, and that is the whole fix.
   *
   * The stack used to render inside .elysium-stage, which declares a
   * z-index of its own and so opens a stacking context. Nothing escapes its
   * parent's stacking context by raising a number, so the stack sat under
   * every dialog no matter what the sheet said - and the sheet said 35,
   * which read like a deliberate choice to put it there. Out here it is a
   * direct child of the body, beside the dialog's own portal, and the layer
   * table in index.css orders the two.
   *
   * `fixed` rather than `absolute` for the same reason: the offsets have no
   * positioned ancestor to measure from any more. The width follows: it is
   * the window's, not the canvas's, so the band is centred on the screen
   * the way a dialog is.
   */
  return createPortal(
    <div
      className="error-toast-stack pointer-events-none fixed left-1/2 flex w-[min(680px,calc(100vw-3rem))] -translate-x-1/2 flex-col items-center gap-2"
      data-testid="error-toast-stack"
      aria-live="polite"
      aria-relevant="additions text"
    >
      {errors.map((error) => (
        <ErrorToast
          key={error.id}
          error={error}
          exiting={exitingIds.has(error.id)}
          onClose={closeToast}
        />
      ))}
    </div>,
    document.body,
  );
}

function ErrorToast({
  error,
  exiting,
  onClose,
}: {
  error: ErrorEvent;
  exiting: boolean;
  onClose: (id: string) => void;
}) {
  // K-25. The countdown used to run whatever was happening, so a toast raised
  // as somebody alt-tabbed away was dismissed 4.5 seconds later and never
  // seen at all - counted as shown, gone, and if the queue was full it took a
  // waiting error down with it.
  //
  // Three things pause it: the tab being hidden, the pointer resting on the
  // toast, and keyboard focus inside it. The last two matter because they are
  // what somebody does while READING one, which is the moment it must not
  // vanish. Focus was the missing third: reaching the dismiss button by tab
  // is the pointer-free way to rest on a toast, and it counted for nothing,
  // so the only person who cannot hover was also the only person the toast
  // could expire underneath.
  //
  // The REMAINING time is tracked rather than the timer restarted. A naive
  // paused flag gives a full 4.5 seconds back on every resume, so a toast
  // under the pointer never expires and a toast in a window that keeps losing
  // focus lives forever - a leak dressed as a fix.
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [hidden, setHidden] = useState(
    () => typeof document !== "undefined" && document.visibilityState === "hidden",
  );
  const remainingRef = useRef(dismissDelayFor(error.message));

  useEffect(() => {
    const onVisibility = () =>
      setHidden(document.visibilityState === "hidden");
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  // A new error id is a new toast: give it the whole window again.
  useEffect(() => {
    remainingRef.current = dismissDelayFor(error.message);
  }, [error.id, error.message]);

  useEffect(() => {
    if (hovered || focused || hidden) return;
    const startedAt = Date.now();
    const timeoutId = window.setTimeout(() => {
      onClose(error.id);
    }, remainingRef.current);

    return () => {
      window.clearTimeout(timeoutId);
      remainingRef.current = Math.max(
        0,
        remainingRef.current - (Date.now() - startedAt),
      );
    };
  }, [error.id, onClose, hovered, focused, hidden]);

  return (
    // Non-button container (role="status") so the toast text is announced
    // politely; click-anywhere-to-dismiss is kept as a pointer convenience,
    // while the inner button is the accessible dismiss control.
    <div
      role="status"
      className={`error-toast pointer-events-auto flex w-full max-w-[640px] cursor-pointer items-center gap-2.5 rounded-md px-4 py-2 text-left ${
        exiting ? "is-exiting" : ""
      }`}
      onClick={() => onClose(error.id)}
      // Resting the pointer on a toast is what somebody does while
      // READING it, which is the one moment it must not disappear.
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      // The keyboard equivalent. React's onFocus/onBlur are focusin/focusout,
      // so focus landing on the dismiss button inside counts as focus on the
      // toast. The relatedTarget guard is what keeps a tab step BETWEEN two
      // controls inside the toast from reading as a departure: focusout fires
      // before the matching focusin, and without the guard that gap unpauses
      // the countdown for a frame every time focus moves within.
      onFocus={() => setFocused(true)}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
          setFocused(false);
        }
      }}
    >
      <span className="error-toast-accent" aria-hidden="true">
        <AlertCircle size={14} strokeWidth={1.8} />
      </span>
      {/* Wraps. `truncate` put the fix off-screen and left it reachable only
          by a mouse hover on a toast that expires in a few seconds. */}
      <span className="min-w-0 flex-1 text-label font-semibold">
        {error.message}
      </span>
      <button
        type="button"
        aria-label="Dismiss"
        className="shrink-0 rounded-full p-0.5 opacity-60 transition-opacity hover:opacity-100"
        onClick={(e) => {
          e.stopPropagation();
          onClose(error.id);
        }}
      >
        <X size={12} strokeWidth={2} aria-hidden="true" />
      </button>
    </div>
  );
}
