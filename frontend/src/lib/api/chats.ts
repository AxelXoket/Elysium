import { request } from "./client";
import { ChatSchema, ChatListSchema, MessageListSchema } from "../schemas/chats";
import type { Chat, Message } from "../schemas/chats";

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

export function getMessages(chatId: number): Promise<Message[]> {
  return request(`/chats/${chatId}/messages`, MessageListSchema);
}
