import { z } from "zod/v4";

/** GET /vault/status */
export const VaultStatusSchema = z.object({
  initialized: z.boolean(),
  unlocked: z.boolean(),
});
export type VaultStatus = z.infer<typeof VaultStatusSchema>;

/** POST /vault/init | /vault/unlock | /vault/lock | /vault/change-passphrase */
export const VaultOkSchema = z.object({
  ok: z.boolean(),
  migrated: z.boolean().optional(),
});
export type VaultOk = z.infer<typeof VaultOkSchema>;
