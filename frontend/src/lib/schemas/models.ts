import { z } from "zod/v4";

// Exact match of openrouter.py _normalise_model() - 13 fields
export const ModelSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  context_length: z.number().nullable(),
  max_completion_tokens: z.number().nullable(),
  supported_parameters: z.array(z.string()),
  input_modalities: z.array(z.string()),
  output_modalities: z.array(z.string()),
  // Pricing values are USUALLY strings ("0.000003") but OpenRouter also nests
  // structured entries like `overrides: [{…}]` - display-only, so accept any
  // value. A strict string record rejected the whole list (one bad model fails
  // the z.array parse → the entire fetch returns no models).
  pricing: z.record(z.string(), z.unknown()),
  top_provider: z.record(z.string(), z.unknown()),
  created: z.number().nullable(),
  canonical_slug: z.string(),
  // D3: OpenRouter's reasoning object, or null when the catalogue has none.
  // Optional so a cached list from before it existed still parses.
  reasoning: z
    .object({
      mandatory: z.boolean().optional(),
      // A listing that is not a list of words reads as no listing, so one odd
      // model cannot fail the whole list's parse.
      supported_efforts: z.array(z.string()).nullable().optional().catch(null),
      // The catalogue's own word for the default level. Never shown as fact:
      // it can disagree with what the vendor says.
      default_effort: z.string().nullable().optional(),
      default_enabled: z.boolean().nullable().optional(),
      // Thinking is a token budget on this model, taken out of the reply's
      // max tokens, so a small max output can leave no room for the reply.
      supports_max_tokens: z.boolean().nullable().optional(),
    })
    .nullable()
    .optional(),
  // Q-121, added by the /models route (not by _normalise_model): how many
  // zero-data-retention endpoints take a chat request. 0 = the send point
  // refuses this model; null = the list was unknown, nothing is claimed.
  zdr_endpoints: z.number().nullable().optional(),
});

// Exact match of openrouter.py fetch_models() return shape
export const ModelListSchema = z.object({
  source: z.enum(["user", "public", "public_fallback"]),
  cached: z.boolean(),
  count: z.number(),
  models: z.array(ModelSchema),
  fallback_reason: z.string().optional(), // only present on public_fallback
  // Q-121: whether the zero-data-retention list was known for this answer.
  zdr_known: z.boolean().optional(),
});

export type Model = z.infer<typeof ModelSchema>;
export type ModelList = z.infer<typeof ModelListSchema>;
