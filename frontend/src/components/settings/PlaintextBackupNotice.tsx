/**
 * PlaintextBackupNotice - the unencrypted copy nobody could see.
 *
 * Migrating a pre-vault database renames the old app.db to
 * app.db.plain.bak-<ts> and keeps it. That is right at the time: a migration
 * that verified wrong would otherwise have destroyed the only copy of
 * everything the user ever wrote.
 *
 * What was wrong is what came after. Nothing removed it and nothing showed
 * it. One banner, on the one launch that migrated, was the entire trace -
 * after which a complete SQLite file holding every message, every character
 * card and every system prompt sat in the clear beside a UI calling the vault
 * encrypted.
 *
 * So it lives here, in the same tab as the passphrase, as a STATE rather than
 * a moment: visible on every visit until the user decides they trust the
 * vault and removes it.
 *
 * Two-step delete, for the same reason ApiKeySection has one: the backend
 * shreds - overwrites, then unlinks - so a stray click destroys the fallback
 * copy of everything, with nothing to undo it from.
 *
 * Colour comes from --color-es-danger and nowhere else. The first cut of this
 * file invented a brown/amber pair, which is precisely the mistake index.css
 * already documents having made once and retired.
 */
import { useState } from "react";
import { AlertCircle, Loader2, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useVaultStatus, useDiscardPlaintextBackup } from "@/lib/query/vault";

export function PlaintextBackupNotice() {
  const status = useVaultStatus();
  const discard = useDiscardPlaintextBackup();
  const [confirming, setConfirming] = useState(false);

  const backups = status.data?.plaintext_backups ?? [];
  const stuck = discard.data?.left ?? [];
  const shared = discard.data?.shared ?? [];
  const removed = discard.data?.removed ?? 0;
  if (backups.length === 0) {
    // Nothing left on disk. Say so once, so pressing delete does not simply
    // make the whole section vanish with no confirmation that it worked.
    if (removed > 0) {
      return (
        <p
          className="settings-value font-semibold"
          style={{ color: "var(--color-es-primary-sage-deep)" }}
        >
          Unencrypted copy deleted.
        </p>
      );
    }
    return null;
  }

  const one = backups.length === 1;

  return (
    <section
      aria-label="Unencrypted database backup"
      data-testid="plaintext-backup-notice"
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
          Unencrypted copy on disk
        </h4>
      </div>
      <p className="settings-hint">
        {one
          ? "A copy of your database from before the vault is still on disk."
          : `${backups.length} copies of your database from before the vault are still on disk.`}{" "}
        {one ? "It is" : "They are"} not encrypted: anyone who can read the
        folder can open {one ? "it" : "them"} without your passphrase. Kept as a fallback in case the move into the vault
        went wrong - remove {one ? "it" : "them"} once you are sure your chats
        are all there.
      </p>
      <ul className="settings-hint space-y-0.5 font-mono">
        {backups.map((name) => (
          <li key={name}>{name}</li>
        ))}
      </ul>
      {stuck.length > 0 && (
        <p className="settings-error" role="alert">
          Still readable on disk and could not be deleted (something else has{" "}
          {stuck.length === 1 ? "the file" : "them"} open): {stuck.join(", ")}
        </p>
      )}
      {/* A different sentence, because it asks for a different thing. The
          message above sends you to close a program and press the button
          again; this one never comes free that way, and deleting the file
          yourself would remove one name while the database stays readable
          under the other. Saying "could not delete" here is what sent people
          off doing exactly that. */}
      {shared.length > 0 && (
        <p className="settings-error" role="alert">
          Left alone on purpose: {shared.join(", ")} shares its contents with
          another file on this disk, so erasing it would destroy that one too.
          Deleting it yourself will not remove the data either - the other name
          still points at it. Find and remove that name first.
        </p>
      )}

      {/* Inline confirm so one stray click cannot destroy the only pre-vault
          copy. The backend overwrites before unlinking, so there is nothing
          to recover afterwards - by design. */}
      {!confirming && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={discard.isPending}
          onClick={() => setConfirming(true)}
          className="gap-1 text-xs"
          style={{ color: "var(--color-es-danger)" }}
        >
          <Trash2 size={12} />
          Delete the unencrypted {one ? "copy" : "copies"}
        </Button>
      )}
      {confirming && (
        <div className="flex items-center gap-2">
          <span
            className="text-xs"
            style={{ color: "var(--color-es-text-muted)" }}
          >
            Permanently delete {one ? "it" : "them"}?
          </span>
          <Button
            type="button"
            size="sm"
            disabled={discard.isPending}
            onClick={() => {
              setConfirming(false);
              discard.mutate();
            }}
            className="inline-confirm-button is-danger gap-1 text-xs"
          >
            {discard.isPending ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <Trash2 size={12} />
            )}
            Delete
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={discard.isPending}
            onClick={() => setConfirming(false)}
            className="text-xs"
          >
            Keep
          </Button>
        </div>
      )}
    </section>
  );
}
