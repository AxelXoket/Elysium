import { z } from "zod/v4";

// Exact match of chats.py _chat_to_dict()
export const ChatSchema = z.object({
  id: z.number(),
  character_id: z.number(),
  character_name: z.string(), // from JOIN characters ch ON c.character_id = ch.id
  title: z.string().nullable(),
  model_id: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
  message_count: z.number(), // from COUNT(*) subquery
});

// Attachment metadata on message rows (uploads contract) - the binary itself
// is fetched separately via GET /uploads/images/{id}.
export const AttachmentSchema = z.object({
  id: z.number(),
  mime: z.string(),
  width: z.number(),
  height: z.number(),
});

// Exact match of chats.py _msg_to_dict()
export const MessageSchema = z.object({
  id: z.number(),
  chat_id: z.number(),
  role: z.enum(["user", "assistant"]), // system role not inserted via chats router
  content: z.string(),
  created_at: z.string(),
  // Empty array default. Non-empty on a user row (images the person attached)
  // and, once image output is switched on, on an assistant row too (a picture
  // the model produced). The backend never replays an assistant row's images to
  // the provider - see _IMAGE_REPLAY_ROLES - so they are display-only.
  attachments: z.array(AttachmentSchema).default([]),
  // Response variants ("swipes"). variant_group = id of the group's FIRST
  // row (null = never regenerated); one active row per group. index/count
  // are server-computed positions within the group. All defaulted so
  // optimistic cache writers and fixtures keep constructing Message
  // literals without them.
  variant_group: z.number().nullable().default(null),
  active: z.boolean().default(true),
  variant_index: z.number().default(0),
  variant_count: z.number().default(1),
  /** The reply stopped because it hit the token ceiling, not because the
   *  model finished its sentence. Nullish so a vault that predates the
   *  column still parses - z.object STRIPS unknown keys, so a field the
   *  server sends and this schema omits vanishes with no error at all. */
  truncated: z.boolean().nullish(),
});

// POST /chats/{id}/messages/{mid}/activate response
export const ActivateVariantResponseSchema = z.object({
  ok: z.literal(true),
  chat_id: z.number(),
  variant_group: z.number(),
  message: MessageSchema,
  deactivated_message_id: z.number().nullable(),
});

export const ChatListSchema = z.array(ChatSchema);
export const MessageListSchema = z.array(MessageSchema);
export const DeletedCountResponseSchema = z.object({
  ok: z.literal(true),
  deleted_count: z.number(),
});
export type Chat = z.infer<typeof ChatSchema>;
export type Attachment = z.infer<typeof AttachmentSchema>;
// z.input (not z.infer): `attachments` has a parse-time default, so the input
// type keeps it optional. Parsed rows always carry the array, but cache
// writers (optimistic messages) and test fixtures construct Message literals
// without it - readers must treat a missing array as empty.
export type Message = z.input<typeof MessageSchema>;
export type DeletedCountResponse = z.infer<typeof DeletedCountResponseSchema>;
export type ActivateVariantResponse = z.input<
  typeof ActivateVariantResponseSchema
>;
