/**
 * AutoLockControl - close the vault when nobody is using it.
 *
 * Every other protection in this app is about data at rest. An unlocked vault
 * is a decrypted vault for as long as the window stays open, and windows stay
 * open for days: on a desk, on a shared machine, on a laptop somebody walked
 * away from. This is the only control that shortens that window.
 *
 * Off by default, and said plainly rather than buried, because a lock that
 * interrupts somebody mid-conversation is a lock they turn off and never turn
 * back on. The copy therefore explains what it costs as well as what it buys.
 *
 * A fixed set of choices rather than a number field: the value that matters is
 * "did you think about this", and a free-form box invites a 1 that locks the
 * vault while the user is reading.
 */
import { Lock } from "lucide-react";

import { useSettings, useSetAutoLock } from "@/lib/query/settings";

const CHOICES: { minutes: number; label: string }[] = [
  { minutes: 0, label: "Never" },
  { minutes: 5, label: "5 min" },
  { minutes: 15, label: "15 min" },
  { minutes: 30, label: "30 min" },
  { minutes: 60, label: "1 hour" },
];

export function AutoLockControl() {
  const settings = useSettings();
  const setAutoLock = useSetAutoLock();
  const current = settings.data?.auto_lock_minutes ?? 0;

  return (
    <div className="space-y-2" data-testid="auto-lock-control">
      <div className="flex items-center gap-2">
        <Lock size={13} style={{ color: "var(--color-es-text-muted)" }} />
        <h4
          data-testid="auto-lock-heading"
          className="text-xs font-semibold"
          style={{ color: "var(--color-es-text-light)" }}
        >
          Lock when idle
        </h4>
      </div>
      {/* `settings-hint`'s colour is a hardcoded rgba tuned for the DARK
          settings dialog and never a variable, so it could not pick up
          .glass-right's redefined tokens even if the cascade reached it -
          measured at 1.24-1.31:1 on this light island. The sibling notebook
          panels already solved this: plain hint text on this surface is
          Tailwind's `text-muted-foreground`, which resolves against
          .glass-right's own `--muted-foreground` redefinition. */}
      <p data-testid="auto-lock-hint" className="text-xs leading-relaxed text-muted-foreground">
        While Elysium is unlocked, your chats are decrypted. This closes the
        vault again after a period with nothing happening, so a window left
        open does not leave them readable. A reply that is still being written
        counts as something happening and will not be interrupted.
      </p>
      <div
        role="radiogroup"
        aria-label="Lock the vault after this long idle"
        className="flex flex-wrap gap-1.5"
      >
        {CHOICES.map(({ minutes, label }) => {
          const selected = current === minutes;
          return (
            <button
              key={minutes}
              type="button"
              role="radio"
              aria-checked={selected}
              disabled={setAutoLock.isPending}
              onClick={() => setAutoLock.mutate(minutes)}
              className="inline-flex h-7 items-center rounded-lg px-2.5 text-xs disabled:cursor-not-allowed disabled:opacity-50"
              style={
                selected
                  ? {
                      backgroundColor: "var(--color-es-primary-sage-deep)",
                      color: "var(--color-es-text-light)",
                      fontWeight: 600,
                    }
                  : {
                      border: "1px solid var(--color-es-border-subtle)",
                      color: "var(--color-es-text-muted)",
                    }
              }
            >
              {label}
            </button>
          );
        })}
      </div>
      {current === 0 && (
        <p data-testid="auto-lock-off-hint" className="text-xs leading-relaxed text-muted-foreground">
          Off: the vault stays open until you lock it or close Elysium.
        </p>
      )}
      {/* `persona-local-error`, not `settings-error`: same reason as the
          hint above, and the class the notebook panels already use for a
          refusal on this exact surface. */}
      {setAutoLock.isError && (
        <p className="persona-local-error" role="alert">
          That could not be saved. The vault is still using the previous
          setting.
        </p>
      )}
    </div>
  );
}
