/**
 * OrphanedCopyNotice - the other full copy of the vault.
 *
 * An interrupted migration leaves app.db.enc-tmp: a complete, encrypted copy
 * of everything. Recovery reclaims it when the live database is missing or
 * empty, and deliberately declines in the only two other cases - the live one
 * is healthy (so this is a duplicate), or it does not open under this key (so
 * it may be a vault under a DIFFERENT passphrase).
 *
 * Either way it stayed forever. /vault/status has reported it as a boolean
 * since it was added, and nothing in this app ever rendered that boolean.
 *
 * The difference from the plaintext backup, and the reason this file exists
 * separately: that one is readable by anyone, so removing it is a privacy
 * win. This one is encrypted, so it is not a leak - it is a duplicate, or it
 * is the only copy of something under a passphrase we do not have. Which of
 * those it is decides whether the user is offered a delete button at all.
 */
import { useEffect, useRef, useState } from "react";
import { AlertCircle, Loader2, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useVaultStatus, useDiscardOrphanedCopy } from "@/lib/query/vault";

export function OrphanedCopyNotice() {
  const status = useVaultStatus();
  const discard = useDiscardOrphanedCopy();
  const [confirming, setConfirming] = useState(false);

  // Same fix as PlaintextBackupNotice, same reason: the confirm row replaces
  // the trigger it grew out of, so an unhelped keyboard user loses focus to
  // <body> asking to delete an irreversible second copy of the vault. Focus
  // the SAFE choice on open, hand it back to the trigger on close, and let
  // Escape close without also stopping a reply generating behind the panel.
  const deleteTriggerRef = useRef<HTMLButtonElement>(null);
  const keepButtonRef = useRef<HTMLButtonElement>(null);
  const wasConfirming = useRef(false);

  useEffect(() => {
    if (confirming) {
      wasConfirming.current = true;
      keepButtonRef.current?.focus();
    } else if (wasConfirming.current) {
      wasConfirming.current = false;
      deleteTriggerRef.current?.focus();
    }
  }, [confirming]);

  useEffect(() => {
    if (!confirming) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      event.preventDefault();
      setConfirming(false);
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [confirming]);

  if (!status.data?.orphaned_copy) return null;
  const readable = status.data.orphaned_copy_readable;

  return (
    <section
      aria-label="Duplicate vault copy"
      data-testid="orphaned-copy-notice"
      className="space-y-2 rounded-lg p-3"
      style={{
        border: "1px solid rgba(195, 106, 114, 0.24)",
        backgroundColor: "rgba(195, 106, 114, 0.10)",
      }}
    >
      <div className="flex items-center gap-2">
        <AlertCircle size={13} style={{ color: "var(--color-es-danger)" }} />
        <h4
          className="text-xs font-semibold"
          style={{ color: "var(--color-es-text-light)" }}
        >
          A second copy of the vault
        </h4>
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">
        An interrupted move left a second, complete copy of your database on
        disk, beside the vault and named after it (
        <code>app.db.enc-tmp</code>, or <code>app.db.enc-tmp.orphan.bak-…</code>
        if a later move had to step around it). It is encrypted, so it is not
        readable by anyone else.
      </p>

      {readable === true && (
        <p className="text-xs leading-relaxed text-muted-foreground">
          It opens with your current passphrase, so it is yours and it is
          readable. Recovery left it behind because your live database was
          already healthy, which makes it a leftover rather than a rescue.
        </p>
      )}
      {readable === false && (
        <p className="text-xs leading-relaxed text-muted-foreground">
          This copy does <strong>not</strong> open with your current
          passphrase. It may belong to an older one - so it could be the only
          copy of chats this vault cannot show you. Elysium will not delete it.
          Move it somewhere safe and keep it until you are sure.
        </p>
      )}
      {readable == null && (
        <p className="text-xs leading-relaxed text-muted-foreground">
          Unlock the vault to find out whether this copy is a duplicate or
          something only an older passphrase can open.
        </p>
      )}

      {discard.data?.removed === false && discard.data.reason === "in_use" && (
        <p className="persona-local-error" role="alert">
          Still on disk: something else has the file open.
        </p>
      )}

      {/* Offered ONLY for a copy this vault can read. The other case is not a
          disabled button with a tooltip - there is no safe version of it. */}
      {readable === true && !confirming && (
        <Button
          ref={deleteTriggerRef}
          type="button"
          variant="ghost"
          size="sm"
          disabled={discard.isPending}
          onClick={() => setConfirming(true)}
          className="persona-danger-action gap-1 text-xs"
        >
          <Trash2 size={12} />
          Delete the duplicate
        </Button>
      )}
      {readable === true && confirming && (
        <div className="flex items-center gap-2">
          <span
            className="text-xs"
            style={{ color: "var(--color-es-text-muted)" }}
          >
            Permanently delete it?
          </span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={discard.isPending}
            onClick={() => {
              setConfirming(false);
              discard.mutate();
            }}
            className="persona-danger-action gap-1 text-xs"
          >
            {discard.isPending ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <Trash2 size={12} />
            )}
            Delete
          </Button>
          <Button
            ref={keepButtonRef}
            type="button"
            variant="ghost"
            size="sm"
            disabled={discard.isPending}
            onClick={() => setConfirming(false)}
            className="persona-ghost-action text-xs"
          >
            Keep
          </Button>
        </div>
      )}
    </section>
  );
}
