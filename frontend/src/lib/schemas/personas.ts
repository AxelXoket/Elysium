import { z } from "zod/v4";

export const PersonaSchema = z.object({
  id: z.number(),
  display_name: z.string(),
  description: z.string(),
  is_active: z.boolean(),
  created_at: z.string(),
  updated_at: z.string(),
});

export const PersonaListSchema = z.array(PersonaSchema);

export const PersonaCreateSchema = z.object({
  display_name: z.string(),
  description: z.string(),
});

export const PersonaPatchSchema = z.object({
  display_name: z.string().optional(),
  description: z.string().optional(),
});

export const PersonaSelectResponseSchema = z.object({
  ok: z.literal(true),
  selected_persona_id: z.number(),
});

export type Persona = z.infer<typeof PersonaSchema>;
export type PersonaCreate = z.infer<typeof PersonaCreateSchema>;
export type PersonaPatch = z.infer<typeof PersonaPatchSchema>;
export type PersonaSelectResponse = z.infer<typeof PersonaSelectResponseSchema>;
