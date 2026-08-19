import { z } from "zod/v4";

/** GET /vault/status */
export const VaultStatusSchema = z.object({
  initialized: z.boolean(),
  unlocked: z.boolean(),
  /** A full, readable copy of the vault stranded at app.db.enc-tmp by an
   *  interrupted migration swap. It is never deleted automatically, and a
   *  crash-recovery pass that declines to adopt it used to leave a single log
   *  line as the only trace - beside a freshly created EMPTY vault the user
   *  read as "my data is gone" (audit KÖK 18). Optional so an older backend
   *  does not fail the parse. */
  orphaned_copy: z.boolean().optional(),
  /** Pre-vault copies of the database that migration kept, UNENCRYPTED. Each
   *  is a complete SQLite file with every message, character card and system
   *  prompt, readable without the passphrase. Migration keeps one on purpose
   *  - a verification that went wrong would otherwise have destroyed the only
   *  copy - but nothing removed it and nothing reported it: one banner on one
   *  launch was the entire trace. Optional so an older backend still parses. */
  plaintext_backups: z.array(z.string()).optional(),
  /** Whether that stranded copy opens under the key we hold. It decides what
   *  the user may safely do with it: a copy this vault can read is a
   *  redundant duplicate, one it cannot may be a vault under a DIFFERENT
   *  passphrase - the only copy of something, not clutter. null while locked,
   *  because answering needs the key. */
  orphaned_copy_readable: z.boolean().nullish(),
  /** A 0-byte app.db that crash recovery moved aside to .empty-stub-bak rather
   *  than unlinking, so that nobody diagnosing a recovery ever reads "the
   *  recovery path deleted a file". Nothing then reported the result: the name
   *  appeared in no route, no field and no screen, and could not be removed
   *  from inside the app. Not a privacy question - it is provably empty - but
   *  an unexplained file beside the vault of an app whose pitch is that you
   *  can see what it keeps. Optional so an older backend still parses. */
  empty_stub: z.boolean().optional(),
  /** Full copies of the database left by a rotation that was killed between
   *  taking its backup and removing it. Every unlock sweeps the ones this
   *  vault can open - those are duplicates of the live file - so a name that
   *  reaches here is the other case: a copy that opens only with the
   *  passphrase that was rotated away, which Elysium will not delete because
   *  it cannot read it. Optional so an older backend still parses. */
  rotation_backups: z.array(z.string()).optional(),
  /** app.db.premigrate.bak: an encrypted snapshot legacy_migration.py takes
   *  before the first pass of an old uploads migration, so a row-deleting
   *  step that goes wrong partway through cannot cost anything. Discarded
   *  only when that migration finishes with zero failures; when it does not,
   *  the snapshot survives every later unlock. It opens with the current
   *  passphrase, so it is not a plaintext leak - the reason it still matters
   *  is that it is a STALE full copy: a message deleted from the live vault
   *  afterward keeps living inside it. z.object STRIPS unknown keys, so this
   *  field has to be named here or the backend's answer vanishes silently
   *  (see PremigrateBackupNotice). Optional so an older backend still parses,
   *  and because the exact field name is this component's own assumption
   *  until the backend route lands - see that file's header comment. */
  premigrate_backup: z.boolean().optional(),
});
export type VaultStatus = z.infer<typeof VaultStatusSchema>;

/** POST /vault/init | /vault/unlock | /vault/lock | /vault/change-passphrase */
export const VaultOkSchema = z.object({
  ok: z.boolean(),
  migrated: z.boolean().optional(),
  /** Name of the PLAINTEXT pre-vault database the migration kept beside the
   *  vault. Rendered by CreatePassphrase: the screen that promises everything
   *  is encrypted on disk is the screen that has to say which file is not.
   *
   *  Also returned by /vault/unlock now: the same migration can run on the
   *  unlock path, and that route used to discard the filename and answer a
   *  bare ok - leaving a full unencrypted copy of the vault on disk that the
   *  user had no way to learn about (audit KÖK 2). */
  backup: z.string().nullish(),
  /** /vault/change-passphrase: encrypted sidecar copies the rotation could
   *  NOT re-key. Each is a complete copy of the vault that is STILL readable
   *  with the OLD passphrase - the one thing a rotation is supposed to
   *  revoke. Empty on the normal path. */
  unrevoked: z.array(z.string()).optional(),
  /** /vault/lock: generated speech that survived the wipe and is still
   *  readable on disk. "Locked" is a promise about what can be read, so a
   *  partial cleanup has to be able to say so. */
  audio_left: z.array(z.string()).optional(),
});
export type VaultOk = z.infer<typeof VaultOkSchema>;
