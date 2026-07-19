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
  // Empty array default; only ever non-empty on role="user" rows.
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
