import { request } from "./client";
import { CompletionResponseSchema } from "../schemas/completions";
import type { CompletionResponse } from "../schemas/completions";

export function completeChat(
  chatId: number,
  payload: { message: string; model_id: string },
): Promise<CompletionResponse> {
  return request(`/chats/${chatId}/complete`, CompletionResponseSchema, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
