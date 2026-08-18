/** Vault API - passphrase lifecycle. Passphrases travel ONLY in request
 * bodies over localhost; they are never stored, logged, or persisted on the
 * frontend (component state only). */
import { request } from "./client";
import { z } from "zod/v4";
import {
  VaultStatusSchema,
  VaultOkSchema,
  type VaultStatus,
  type VaultOk,
} from "@/lib/schemas/vault";

/** POST /vault/discard-plaintext-backup. `left` names the files it could not
 *  delete: a flat "done" while a full unencrypted database stayed readable
 *  would be the exact promise this feature exists to keep.
 *
 *  `shared` is the other half of that, and it needs the opposite sentence.
 *  Those files were left alone on purpose: their bytes answer to a second
 *  name on the disk, so overwriting them would destroy whatever that other
 *  name belongs to. Reported as "could not delete" they read as "close
 *  something and retry", and the user ends up deleting the file by hand -
 *  which removes one name and leaves the plaintext database readable under
 *  the other. Optional so an older backend still parses. */
export const DiscardBackupSchema = z.object({
  removed: z.number(),
  left: z.array(z.string()),
  shared: z.array(z.string()).optional(),
});
export type DiscardBackup = z.infer<typeof DiscardBackupSchema>;

/** POST /vault/discard-orphaned-copy. `reason` explains a refusal: the copy
 *  is gone already, it does not open under this key, or something holds it. */
export const DiscardOrphanSchema = z.object({
  removed: z.boolean(),
  reason: z.string(),
});
export type DiscardOrphan = z.infer<typeof DiscardOrphanSchema>;

export function getVaultStatus(): Promise<VaultStatus> {
  return request("/vault/status", VaultStatusSchema);
}

export function initVault(passphrase: string): Promise<VaultOk> {
  return request("/vault/init", VaultOkSchema, {
    method: "POST",
    body: JSON.stringify({ passphrase }),
  });
}

export function unlockVault(passphrase: string): Promise<VaultOk> {
  return request("/vault/unlock", VaultOkSchema, {
    method: "POST",
    body: JSON.stringify({ passphrase }),
  });
}

export function lockVault(): Promise<VaultOk> {
  return request("/vault/lock", VaultOkSchema, { method: "POST" });
}

export function changeVaultPassphrase(
  oldPassphrase: string,
  newPassphrase: string,
): Promise<VaultOk> {
  return request("/vault/change-passphrase", VaultOkSchema, {
    method: "POST",
    body: JSON.stringify({
      old_passphrase: oldPassphrase,
      new_passphrase: newPassphrase,
    }),
  });
}

export function discardPlaintextBackup(): Promise<DiscardBackup> {
  return request("/vault/discard-plaintext-backup", DiscardBackupSchema, {
    method: "POST",
  });
}

export function discardOrphanedCopy(): Promise<DiscardOrphan> {
  return request("/vault/discard-orphaned-copy", DiscardOrphanSchema, {
    method: "POST",
  });
}

/** POST /vault/discard-empty-stub. Same shape as the orphan discard, and the
 *  same reason for `reason`: the backend re-measures the file and refuses one
 *  that is not empty, which the screen has to be able to say out loud. */
export function discardEmptyStub(): Promise<DiscardOrphan> {
  return request("/vault/discard-empty-stub", DiscardOrphanSchema, {
    method: "POST",
  });
}
