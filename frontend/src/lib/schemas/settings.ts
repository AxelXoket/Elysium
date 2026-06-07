import { z } from "zod/v4";

// Exact match of routers/settings.py get_settings() return dict
export const SettingsSchema = z.object({
  api_key_set: z.boolean(),
  proxy_required: z.boolean(),
  proxy_configured: z.boolean(),
  proxy_alias: z.string().nullable(),
  selected_persona_id: z.number().nullable(),
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

export type Settings = z.infer<typeof SettingsSchema>;
export type ProxyHealth = z.infer<typeof ProxyHealthSchema>;
