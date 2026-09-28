/**
 * RotationBackupNotice - the copy a killed rotation left behind.
 *
 * Changing the passphrase, and upgrading the key-derivation settings, both
 * copy the whole database to app.db.rekey.bak-<ts> before touching it and
 * remove it when they are done. Kill the app in that window and the copy
 * stays: a complete vault, in none of the other notices on this screen, and
 * removed by no route.
 *
 * The one that reaches this component is the WORSE of the two shapes. Every
 * unlock sweeps away the copies this vault can open, because those are just
 * duplicates of the live database and there is nothing to decide about them.
 * What survives the sweep is a copy taken before a rotation that DID finish -
 * so it opens with the passphrase that was rotated AWAY from, and Elysium
 * will not delete a file it cannot read.
 *
 * Hence no delete button here, unlike its neighbours. Offering one would mean
 * offering to destroy data nobody could check first, and the honest thing this
 * screen can do is say the file exists and where.
 */
import { AlertCircle } from "lucide-react";

import { useVaultStatus } from "@/lib/query/vault";
import { noticeGate } from "./NoticeGate";

export function RotationBackupNotice() {
  const status = useVaultStatus();
  // Four states, not two: see NoticeGate. A status query that FAILED used to
  // render the same nothing as a disk with no such copy on it.
  const gate = noticeGate(status, (s) => (s.rotation_backups ?? []).length > 0, {
    id: "rotation-backup",
    subject: "a copy from before a passphrase change",
  });
  if (gate.state !== "present") return gate.fallback;
  const names = gate.data.rotation_backups ?? [];

  return (
    <section
      aria-label="Copy left by an interrupted passphrase change"
      data-testid="rotation-backup-notice"
      className="space-y-2 rounded-lg p-3"
      style={{
        border: "1px solid rgba(195, 106, 114, 0.24)",
        backgroundColor: "rgba(195, 106, 114, 0.10)",
      }}
    >
      <div className="flex items-center gap-2">
        <AlertCircle size={13} style={{ color: "var(--color-es-danger)" }} />
        <h4
          className="text-label font-semibold"
          style={{ color: "var(--color-es-text-light)" }}
        >
          A copy from before a passphrase change
        </h4>
      </div>
      {/* `settings-hint` painted for the dark dialog, same as everywhere
          else in this folder - `text-muted-foreground` is the sibling
          panels' idiom on `.glass-right`. */}
      <p data-testid="rotation-hint-1" className="text-label leading-relaxed text-muted-foreground">
        A passphrase change that was interrupted left a complete copy of your
        database beside the vault. It is encrypted, but with the{" "}
        <strong>previous</strong> passphrase - so anyone who knows the old one
        can still open it, and changing your passphrase again will not close
        that.
      </p>
      <p data-testid="rotation-hint-2" className="text-label leading-relaxed text-muted-foreground">
        Elysium will not delete it, because it cannot read it and it may be the
        only copy of something. Delete it yourself, or move it somewhere safe,
        from the Elysium data folder:
      </p>
      <ul className="space-y-1">
        {names.map((name) => (
          <li key={name}>
            <code data-testid={`rotation-name-${name}`} className="text-label leading-relaxed text-muted-foreground">{name}</code>
          </li>
        ))}
      </ul>
    </section>
  );
}
