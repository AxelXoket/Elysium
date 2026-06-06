import { z } from "zod/v4";
import { MessageSchema } from "./chats";

// Exact match of completions.py return shape (lines 400-404)
export const CompletionResponseSchema = z.object({
  chat_id: z.number(),
  model_id: z.string(),
  user_message: MessageSchema,
  assistant_message: MessageSchema,
});

export type CompletionResponse = z.infer<typeof CompletionResponseSchema>;
