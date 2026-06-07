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

// Exact match of chats.py _msg_to_dict()
export const MessageSchema = z.object({
  id: z.number(),
  chat_id: z.number(),
  role: z.enum(["user", "assistant"]), // system role not inserted via chats router
  content: z.string(),
  created_at: z.string(),
});

export const ChatListSchema = z.array(ChatSchema);
export const MessageListSchema = z.array(MessageSchema);
export const DeletedCountResponseSchema = z.object({
  ok: z.literal(true),
  deleted_count: z.number(),
});
export type Chat = z.infer<typeof ChatSchema>;
export type Message = z.infer<typeof MessageSchema>;
export type DeletedCountResponse = z.infer<typeof DeletedCountResponseSchema>;
