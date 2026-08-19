import { z } from "zod/v4";

/** One note. The fields that matter to the screen are the ones that explain
 *  why a note is or is not reaching the model:
 *
 *  - `provenance` says who wrote it. It is set once at insert and no route can
 *    change it, so the badge on a row is a fact rather than a hint.
 *  - `retired_at` means a newer note replaced this one. It stays on screen and
 *    stops being sent - the owner's rule is that a note never disappears.
 *  - `excluded_reason` means the ceiling bit this turn. Same idea: visible,
 *    not sent, and the reason is written down rather than guessed at.
 */
export const NotebookEntrySchema = z.object({
  id: z.number(),
  chat_id: z.number(),
  position: z.number(),
  kind: z.string(),
  text: z.string(),
  evidence: z.string().nullish(),
  durability: z.string(),
  importance: z.number(),
  pinned: z.number(),
  retired_at: z.string().nullish(),
  superseded_by: z.number().nullish(),
  excluded_reason: z.string().nullish(),
  status: z.string(),
  provenance: z.string(),
  source_message_id: z.number().nullish(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type NotebookEntry = z.infer<typeof NotebookEntrySchema>;

/** `notebook_chars` is measured by the backend and carried, not recomputed
 *  here. The same shape as `/tts/voice-mode`'s `prompt_chars`, and for the
 *  same reason: the voice block is the one part of the fixed cost the
 *  estimator does not rebuild, and the one part that cannot drift. The
 *  character header drifted once because two languages built the same string.
 *  `.default(0)` so an older backend still parses. */
export const NotebookListSchema = z.object({
  entries: z.array(NotebookEntrySchema),
  notebook_chars: z.number().default(0),
});

export const BoundarySchema = z.object({
  id: z.number(),
  scope: z.string(),
  chat_id: z.number().nullish(),
  label: z.string(),
  phrasing: z.string(),
  severity: z.string(),
  polarity: z.string(),
  on_violation: z.string(),
  source: z.string(),
  rating_ceiling: z.string().nullish(),
  exempt_from_trim: z.number(),
  last_confirmed_at: z.string().nullish(),
  active: z.number(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type Boundary = z.infer<typeof BoundarySchema>;

export const BoundaryListSchema = z.object({
  boundaries: z.array(BoundarySchema),
  /** Whether this chat follows the global set. Carried with the rows because
   *  the switch has to render in the position it is actually in - a local
   *  default reads "on" after every remount, which is the "you believe it is
   *  in force and it is not" failure this whole panel warns about.
   *  `.default(true)` so the global list route (which has no chat) parses. */
  use_global: z.boolean().default(true),
});

export const NotebookOkSchema = z.object({ ok: z.boolean() });

export const UseGlobalSchema = z.object({
  ok: z.boolean(),
  use_global: z.boolean(),
});
