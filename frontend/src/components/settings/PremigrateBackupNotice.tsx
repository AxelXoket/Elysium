/**
 * PremigrateBackupNotice - the snapshot an old uploads migration would not
 * let go of.
 *
 * legacy_migration.py takes an encrypted copy of the whole database,
 * app.db.premigrate.bak, before the first pass of a migration that moves old
 * upload files into blobs and deletes rows as it goes - a guard against that
 * row-deleting pass costing anything if it goes wrong partway through. It is
 * discarded automatically only when a pass finishes with zero failures; when
 * one does not, the snapshot survives every unlock after it, forever, with
 * nothing in the app ever mentioning it. Same shape as the other three files
 * in this folder: a moment (one migration, once) turned into a state nobody
 * could see or act on.
 *
 * The wording problem is the opposite of PlaintextBackupNotice's. That file
 * is a genuine leak and has to say so. This one opens with the current
 * passphrase ONLY WHEN premigrate_backup_readable says so, so calling it
 * exposed would be a lie in one direction and calling it always-safe-to-open
 * would be a lie in the other: discard_premigrate_backup_now (legacy_
 * migration.py) refuses to shred a copy that does not open with the current
 * key, because it may be the only copy of something from an era this
 * passphrase does not reach - the same reason recovery leaves an unreadable
 * orphaned copy alone. What makes the readable case worth a banner anyway is
 * staleness: it is frozen at the moment it was taken, so a message deleted
 * from the live vault afterward keeps living inside it - "delete" stops being
 * a complete answer while this file is still on disk. Same reasoning and the
 * same three-way branch (readable / not readable / unknown while locked)
 * OrphanedCopyNotice already uses for its own encrypted leftover; this file
 * borrows its shape for both reasons.
 *
 * BACKEND CONTRACT, CONFIRMED. GET /vault/status carries `premigrate_backup`
 * (boolean presence flag, same shape as `empty_stub` - there is exactly one
 * of these) and `premigrate_backup_readable` (nullish: null while locked,
 * else whether the current key opens the file - see lib/schemas/vault.ts,
 * mirroring `orphaned_copy_readable`). POST /vault/discard-premigrate-backup
 * answers `{ removed: boolean, reason: string }`, with `reason` one of
 * "not_present" | "different_key" | "in_use" - discard_premigrate_backup_now
 * says explicitly it shares that vocabulary with discard_orphaned_enc_tmp "so
 * a caller only has to learn it once." Both fields are declared `.optional()`
 * / `.nullish()` so an older backend still parses; the notice just renders
 * less rather than throwing.
 */
import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { z } from "zod/v4";
import { AlertCircle, Loader2, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { request } from "@/lib/api/client";
import { keys } from "@/lib/query/keys";
import { useVaultStatus } from "@/lib/query/vault";
import { useErrorStore } from "@/lib/errors";

/** POST /vault/discard-premigrate-backup. See the file header for why this
 *  schema lives here instead of lib/api/vault.ts: that file is shared ground
 *  this component does not own. */
const DiscardPremigrateSchema = z.object({
  removed: z.boolean(),
  reason: z.string(),
});
//: How many times this button has FAILED. Module-level so it survives
//: the notice unmounting when /vault/status refetches after a failure.
let premigratePresses = 0;


function useDiscardPremigrateBackup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      request("/vault/discard-premigrate-backup", DiscardPremigrateSchema, {
        method: "POST",
      }),
    // Same reason as every sibling discard hook: the warning has to
    // disappear once the file the status query reported is actually gone.
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.vault() });
    },
    // K-22's idiom, four irreversible deletions later. There was no
    // `onError` anywhere on these, no MutationCache to catch them, and the
    // `role="alert"` paragraphs in the notices report fields of a SUCCESSFUL
    // response - so a 500 or a 423 said nothing at all, and "the file is
    // gone" looked exactly like "nothing was even attempted" to the one
    // person who cannot check.
    //
    // Per hook rather than a MutationCache default: `useUnlockVault` and
    // `useChangeVaultPassphrase` show their failures INSIDE the screen on
    // purpose, and a global handler would double every one of those.
    // A SOURCE, same reason as its three siblings in lib/query/vault.ts.
    //
    // This notice renders directly above the orphaned-copy one, and both
    // answer 423 with the same code and no chat id. Without a source the
    // second Remove pressed inside the first toast's window said nothing at
    // all - and the `role="alert"` paragraph below reads fields of a
    // SUCCESSFUL response, so it says nothing on a failure either.
    onError: (err: unknown) => {
      premigratePresses += 1;
      useErrorStore.getState().pushError(err, "error", {
        source: `vault:discard-premigrate#${premigratePresses}`,
      });
    },
  });
}

export function PremigrateBackupNotice() {
  const status = useVaultStatus();
  const discard = useDiscardPremigrateBackup();
  const [confirming, setConfirming] = useState(false);

  // Same fix as the other two irreversible deletes in this folder: the
  // confirm row replaces the trigger it grew out of, so an unhelped keyboard
  // user loses focus to <body> asking to delete a full copy of the vault.
  // Focus the SAFE choice on open, hand it back to the trigger on close, and
  // let Escape close without also stopping a reply generating behind the
  // panel (Composer binds Escape at the window; this stops it there first).
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

  if (!status.data?.premigrate_backup) return null;
  const readable = status.data.premigrate_backup_readable;

  return (
    <section
      aria-label="Stale migration snapshot"
      data-testid="premigrate-backup-notice"
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
          A snapshot from an old migration
        </h4>
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">
        A migration that moves old upload files into the vault takes an
        encrypted copy of the whole database first, <code>app.db.premigrate.bak</code>,
        so the step that follows cannot cost you anything if it goes wrong.
        That migration has not finished cleanly, so the copy is still on disk.
      </p>

      {readable === true && (
        <p className="text-xs leading-relaxed text-muted-foreground">
          It opens with your current passphrase, the same as the rest of the
          vault - it is not readable by anyone who does not have it. It still
          matters, because it is frozen at the moment it was taken: a message
          you delete from the vault afterward keeps living inside this copy.
          While it exists, deleting something here is not the complete answer
          it looks like.
        </p>
      )}
      {readable === false && (
        <p className="text-xs leading-relaxed text-muted-foreground">
          This copy does <strong>not</strong> open with your current
          passphrase. It may belong to an older one - so it could be the only
          copy of chats this vault cannot show you. Elysium will not delete it.
        </p>
      )}
      {readable == null && (
        <p className="text-xs leading-relaxed text-muted-foreground">
          Unlock the vault to find out whether this snapshot is safe to delete
          or belongs to an older passphrase.
        </p>
      )}

      {discard.data?.removed === false && discard.data.reason === "in_use" && (
        <p className="persona-local-error" role="alert">
          Still on disk: something else has the file open.
        </p>
      )}
      {discard.data?.removed === false &&
        discard.data.reason === "different_key" && (
          <p className="persona-local-error" role="alert">
            Not deleted: it no longer opens with your current passphrase, so
            it may be the only copy of something an older passphrase reached.
            Elysium left it alone.
          </p>
        )}
      {discard.data?.removed === false &&
        discard.data.reason === "not_present" && (
          <p className="persona-local-error" role="alert">
            Already gone - there was nothing left to delete.
          </p>
        )}

      {/* Offered ONLY for a copy this vault can read, same fork as
          OrphanedCopyNotice - there is no safe version of the delete button
          for the other two states. */}
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
          Delete the snapshot
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
