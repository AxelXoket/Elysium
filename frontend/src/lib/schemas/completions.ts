import { z } from "zod/v4";
import { MessageSchema } from "./chats";

export const GenerationParamsSchema = z.object({
  temperature: z.number().min(0).max(2).nullable().optional(),
  top_p: z.number().min(0).max(1).nullable().optional(),
  top_k: z.number().int().min(0).max(131072).nullable().optional(),
  repetition_penalty: z.number().min(0.001).max(2).nullable().optional(),
  max_tokens: z.number().int().min(1).max(131072).nullable().optional(),
  seed: z.number().int().nullable().optional(),
});

export const CompletionRequestSchema = z.object({
  message: z.string(),
  model_id: z.string(),
  generation_params: GenerationParamsSchema.optional(),
  persona_id: z.number().nullable().optional(),
  context_budget_tokens: z.number().int().min(512).max(2_000_000).nullable().optional(),
});

export const RegenerateRequestSchema = CompletionRequestSchema.omit({
  message: true,
});

// Exact match of completions.py return shape (lines 400-404)
export const CompletionResponseSchema = z.object({
  chat_id: z.number(),
  model_id: z.string(),
  user_message: MessageSchema,
  assistant_message: MessageSchema,
});

export type GenerationParams = z.infer<typeof GenerationParamsSchema>;
export type CompletionRequest = z.infer<typeof CompletionRequestSchema>;
export type RegenerateRequest = z.infer<typeof RegenerateRequestSchema>;
export type CompletionResponse = z.infer<typeof CompletionResponseSchema>;
