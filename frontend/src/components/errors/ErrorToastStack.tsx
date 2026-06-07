import { useCallback, useEffect, useState } from "react";
import { AlertCircle } from "lucide-react";
import { useErrorStore } from "@/lib/errors";
import type { ErrorEvent } from "@/lib/errors";

const AUTO_DISMISS_MS = 4_500;
const EXIT_ANIMATION_MS = 180;

export function ErrorToastStack() {
  const errors = useErrorStore((s) => s.errors);
  const dismiss = useErrorStore((s) => s.dismiss);
  const [exitingIds, setExitingIds] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    const visibleIds = new Set(errors.map((error) => error.id));
    setExitingIds((current) => {
      const next = new Set([...current].filter((id) => visibleIds.has(id)));
      return next.size === current.size ? current : next;
    });
  }, [errors]);

  const closeToast = useCallback(
    (id: string) => {
      setExitingIds((current) => {
        if (current.has(id)) return current;
        const next = new Set(current);
        next.add(id);
        return next;
      });

      window.setTimeout(() => dismiss(id), EXIT_ANIMATION_MS);
    },
    [dismiss],
  );

  return (
    <div
      className="error-toast-stack pointer-events-none absolute left-1/2 flex w-[min(680px,calc(100%-3rem))] -translate-x-1/2 flex-col items-center gap-2"
      data-testid="error-toast-stack"
      aria-live="assertive"
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
    <button
      type="button"
      role="alert"
      className={`error-toast pointer-events-auto flex w-full max-w-[640px] items-center gap-2.5 rounded-full px-4 py-2 text-left ${
        exiting ? "is-exiting" : ""
      }`}
      onClick={() => onClose(error.id)}
      aria-label={`Dismiss error: ${error.message}`}
      title={error.message}
    >
      <span className="error-toast-accent" aria-hidden="true">
        <AlertCircle size={14} strokeWidth={1.8} />
      </span>
      <span className="min-w-0 truncate text-xs font-medium">
        {error.message}
      </span>
    </button>
  );
}
