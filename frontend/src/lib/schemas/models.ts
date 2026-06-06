import { z } from "zod/v4";

// Exact match of openrouter.py _normalise_model() — 12 fields
export const ModelSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  context_length: z.number().nullable(),
  max_completion_tokens: z.number().nullable(),
  supported_parameters: z.array(z.string()),
  input_modalities: z.array(z.string()),
  output_modalities: z.array(z.string()),
  pricing: z.record(z.string(), z.string()),
  top_provider: z.record(z.string(), z.unknown()),
  created: z.number().nullable(),
  canonical_slug: z.string(),
});

// Exact match of openrouter.py fetch_models() return shape
export const ModelListSchema = z.object({
  source: z.enum(["user", "public", "public_fallback"]),
  cached: z.boolean(),
  count: z.number(),
  models: z.array(ModelSchema),
  fallback_reason: z.string().optional(), // only present on public_fallback
});

export type Model = z.infer<typeof ModelSchema>;
export type ModelList = z.infer<typeof ModelListSchema>;
