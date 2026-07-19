import { useCallback, useEffect, useState } from "react";
import { AlertCircle, X } from "lucide-react";
import { useErrorStore } from "@/lib/errors";
import type { ErrorEvent } from "@/lib/errors";

const AUTO_DISMISS_MS = 4_500;
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
  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      onClose(error.id);
    }, AUTO_DISMISS_MS);

    return () => window.clearTimeout(timeoutId);
  }, [error.id, onClose]);

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
      title={error.message}
    >
      <span className="error-toast-accent" aria-hidden="true">
        <AlertCircle size={14} strokeWidth={1.8} />
      </span>
      <span className="min-w-0 flex-1 truncate text-xs font-medium">
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
