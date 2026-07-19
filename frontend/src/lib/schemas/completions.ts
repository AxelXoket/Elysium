import { z } from "zod/v4";
import { MessageSchema } from "./chats";

export const GenerationParamsSchema = z.object({
  temperature: z.number().min(0).max(2).nullable().optional(),
  top_p: z.number().min(0).max(1).nullable().optional(),
  top_k: z.number().int().min(0).max(131072).nullable().optional(),
  repetition_penalty: z.number().min(0.001).max(2).nullable().optional(),
  max_tokens: z.number().int().min(1).max(131072).nullable().optional(),
  seed: z.number().int().nullable().optional(),
  // Contract: string or [string], non-empty (backend rejects empty strings).
  stop: z
    .union([z.string().min(1), z.array(z.string().min(1)).min(1)])
    .nullable()
    .optional(),
});

export const CompletionRequestSchema = z.object({
  message: z.string(),
  model_id: z.string(),
  generation_params: GenerationParamsSchema.optional(),
  persona_id: z.number().nullable().optional(),
  context_budget_tokens: z.number().int().min(512).max(2_000_000).nullable().optional(),
  // Upload ids from POST /uploads/images (max 4 per message). Omitted when
  // the message has no images - never sent as an empty array.
  attachments: z.array(z.number().int().positive()).max(4).optional(),
});

// Regenerate carries no attachments field (contract: regenerate is unchanged).
export const RegenerateRequestSchema = CompletionRequestSchema.omit({
  message: true,
  attachments: true,
});

// Exact match of completions.py return shape
export const CompletionResponseSchema = z.object({
  chat_id: z.number(),
  model_id: z.string(),
  user_message: MessageSchema,
  assistant_message: MessageSchema,
  // Regenerate only: the sibling variant that was active before this one.
  deactivated_message_id: z.number().nullable().optional(),
});

export type GenerationParams = z.infer<typeof GenerationParamsSchema>;
export type CompletionRequest = z.infer<typeof CompletionRequestSchema>;
export type RegenerateRequest = z.infer<typeof RegenerateRequestSchema>;
// z.input (not z.infer): the nested MessageSchema defaults `attachments` at
// parse time, so the input type keeps it optional - fixtures construct
// response literals without it (see the Message type note in ./chats).
export type CompletionResponse = z.input<typeof CompletionResponseSchema>;
