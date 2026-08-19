import { useCallback, useEffect, useRef, useState } from "react";
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
const MAX_DISMISS_MS = 14_000;

function dismissDelayFor(message: string): number {
  const extra = Math.floor(message.length / 40) * READING_MS_PER_40_CHARS;
  return Math.min(MAX_DISMISS_MS, AUTO_DISMISS_MS + extra);
}
const EXIT_ANIMATION_MS = 180;

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

  return (
    <div
      className="error-toast-stack pointer-events-none absolute left-1/2 flex w-[min(680px,calc(100%-3rem))] -translate-x-1/2 flex-col items-center gap-2"
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
    </div>
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
  // Two things pause it: the tab being hidden, and the pointer resting on the
  // toast. The second matters because it is what somebody does while READING
  // one, which is the moment it must not vanish.
  //
  // The REMAINING time is tracked rather than the timer restarted. A naive
  // paused flag gives a full 4.5 seconds back on every resume, so a toast
  // under the pointer never expires and a toast in a window that keeps losing
  // focus lives forever - a leak dressed as a fix.
  const [hovered, setHovered] = useState(false);
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
    if (hovered || hidden) return;
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
  }, [error.id, onClose, hovered, hidden]);

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
    >
      <span className="error-toast-accent" aria-hidden="true">
        <AlertCircle size={14} strokeWidth={1.8} />
      </span>
      {/* Wraps. `truncate` put the fix off-screen and left it reachable only
          by a mouse hover on a toast that expires in a few seconds. */}
      <span className="min-w-0 flex-1 text-xs font-medium">
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
