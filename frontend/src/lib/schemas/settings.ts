import { z } from "zod/v4";

// Exact match of routers/settings.py get_settings() return dict
export const SettingsSchema = z.object({
  api_key_set: z.boolean(),
  /** Stop sequences live in the ENCRYPTED settings table, not in localStorage:
   *  they are character names, i.e. user content, which the S-09b privacy test
   *  bans from browser storage. Keeping them server-side is what lets them
   *  survive a session and a vault lock without breaking that rule. */
  stop_sequences: z.array(z.string()).default([]),
  proxy_required: z.boolean(),
  proxy_configured: z.boolean(),
  proxy_alias: z.string().nullable(),
  selected_persona_id: z.number().nullable(),
  /** May a model answer with a generated picture. Off by default, and stored in
   *  the vault rather than localStorage because it changes what is sent to the
   *  provider. Defaulted so a newer client against an older server still
   *  parses - and defaults to the safe answer. */
  image_output_enabled: z.boolean().default(false),
  /** Minutes of inactivity before the vault locks itself; 0 means never.
   *  Defaulted so a newer client against an older server parses, and the
   *  default is the one that does not lock a session the server never
   *  promised to keep track of. */
  auto_lock_minutes: z.number().default(0),
  /** Hide the window from screen capture and screen sharing. Defaulted so a
   *  newer client against an older server parses, and the default is OFF -
   *  the owner takes screenshots of this app, and a protection that silently
   *  blanks them is a bug report rather than a feature. */
  screen_privacy_enabled: z.boolean().default(false),
});

// Exact match of proxy_health.py check_proxy_health() return dict
export const ProxyHealthSchema = z.object({
  healthy: z.boolean(),
  latency_ms: z.number().nullable(),
  reason: z.string().nullable(),
  cached: z.boolean(),
});

// Used for endpoints returning { ok: true }:
// POST /settings/api-key, DELETE /settings/api-key,
// POST /settings/proxy, DELETE /settings/proxy
export const OkResponseSchema = z.object({ ok: z.literal(true) });
export const ApiKeySaveResponseSchema = z.discriminatedUnion("ok", [
  z.object({
    ok: z.literal(true),
    key_status: z.literal("valid"),
  }),
  z.object({
    ok: z.literal(false),
    key_status: z.literal("validation_unavailable"),
  }),
]);
export type OkResponse = z.infer<typeof OkResponseSchema>;
export type ApiKeySaveResponse = z.infer<typeof ApiKeySaveResponseSchema>;

export const StopSequencesResponseSchema = z.object({
  ok: z.boolean(),
  stop_sequences: z.array(z.string()),
});

export const ImageOutputResponseSchema = z.object({
  ok: z.boolean(),
  image_output_enabled: z.boolean(),
});
export type ImageOutputResponse = z.infer<typeof ImageOutputResponseSchema>;

export const AutoLockResponseSchema = z.object({
  ok: z.boolean(),
  auto_lock_minutes: z.number(),
});
export type AutoLockResponse = z.infer<typeof AutoLockResponseSchema>;

export type Settings = z.infer<typeof SettingsSchema>;
export type StopSequencesResponse = z.infer<typeof StopSequencesResponseSchema>;
export type ProxyHealth = z.infer<typeof ProxyHealthSchema>;
