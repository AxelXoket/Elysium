/** Vault API - passphrase lifecycle. Passphrases travel ONLY in request
 * bodies over localhost; they are never stored, logged, or persisted on the
 * frontend (component state only). */
import { request } from "./client";
import {
  VaultStatusSchema,
  VaultOkSchema,
  type VaultStatus,
  type VaultOk,
} from "@/lib/schemas/vault";

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
