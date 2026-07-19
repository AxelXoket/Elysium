import { request } from "./client";
import {
  ChatSchema,
  ChatListSchema,
  MessageListSchema,
  DeletedCountResponseSchema,
  ActivateVariantResponseSchema,
} from "../schemas/chats";
import { OkResponseSchema } from "../schemas/settings";
import type {
  Chat,
  Message,
  DeletedCountResponse,
  ActivateVariantResponse,
} from "../schemas/chats";
import type { OkResponse } from "../schemas/settings";

export function listChats(): Promise<Chat[]> {
  return request("/chats", ChatListSchema);
}

export function getChat(id: number): Promise<Chat> {
  return request(`/chats/${id}`, ChatSchema);
}

export function createChat(payload: {
  character_id: number;
  title?: string;
  model_id?: string;
}): Promise<Chat> {
  return request("/chats", ChatSchema, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function renameChat(chatId: number, title: string): Promise<Chat> {
  return request(`/chats/${chatId}`, ChatSchema, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

export function getMessages(chatId: number): Promise<Message[]> {
  return request(`/chats/${chatId}/messages`, MessageListSchema);
}

export function deleteChat(chatId: number): Promise<OkResponse> {
  return request(`/chats/${chatId}`, OkResponseSchema, {
    method: "DELETE",
  });
}

export function clearChat(chatId: number): Promise<DeletedCountResponse> {
  return request(`/chats/${chatId}/clear`, DeletedCountResponseSchema, {
    method: "POST",
  });
}

export function deleteMessageAndFollowing(
  chatId: number,
  messageId: number,
): Promise<DeletedCountResponse> {
  return request(
    `/chats/${chatId}/messages/${messageId}`,
    DeletedCountResponseSchema,
    {
      method: "DELETE",
    },
  );
}

/** Make one variant of the chat's last assistant group the active row. */
export function activateVariant(
  chatId: number,
  messageId: number,
): Promise<ActivateVariantResponse> {
  return request(
    `/chats/${chatId}/messages/${messageId}/activate`,
    ActivateVariantResponseSchema,
    {
      method: "POST",
    },
  );
}
