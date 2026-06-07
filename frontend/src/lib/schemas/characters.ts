import { z } from "zod/v4";

// Exact match of characters.py _row_to_dict()
export const CharacterSchema = z.object({
  id: z.number(),
  name: z.string(),
  description: z.string(),
  personality: z.string(),
  scenario: z.string(),
  first_mes: z.string(),
  mes_example: z.string(),
  system_prompt: z.string(),
  post_history_instruction: z.string(),
  tags: z.array(z.string()),
  created_at: z.string(),
  // raw_json: intentionally absent — backend _row_to_dict() excludes it
});

export const CharacterListSchema = z.array(CharacterSchema);
export const CharacterPatchSchema = CharacterSchema.omit({
  id: true,
  created_at: true,
}).partial();
export type Character = z.infer<typeof CharacterSchema>;
export type CharacterPatch = z.infer<typeof CharacterPatchSchema>;
