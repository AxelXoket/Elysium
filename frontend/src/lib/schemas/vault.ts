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
