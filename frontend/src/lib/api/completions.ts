import { request } from "./client";
import { CompletionResponseSchema } from "../schemas/completions";
import type {
  CompletionRequest,
  CompletionResponse,
  RegenerateRequest,
} from "../schemas/completions";

export function completeChat(
  chatId: number,
  payload: CompletionRequest,
): Promise<CompletionResponse> {
  return request(`/chats/${chatId}/complete`, CompletionResponseSchema, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function regenerateMessage(
  chatId: number,
  messageId: number,
  payload: RegenerateRequest,
): Promise<CompletionResponse> {
  return request(
    `/chats/${chatId}/messages/${messageId}/regenerate`,
    CompletionResponseSchema,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}
