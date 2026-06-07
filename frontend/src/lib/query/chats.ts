import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { keys } from "./keys";
import { useErrorStore } from "../errors";
import {
  listChats,
  createChat,
  getMessages,
  deleteChat,
  clearChat,
  deleteMessageAndFollowing,
} from "../api/chats";
import type { Chat, Message } from "../schemas/chats";

export function useChats() {
  return useQuery({
    queryKey: keys.chats(),
    queryFn: listChats,
    staleTime: 30_000,
  });
}

export function useMessages(chatId: number | null) {
  return useQuery({
    queryKey: chatId != null ? keys.messages(chatId) : ["messages", "__none__"],
    queryFn: () => getMessages(chatId!),
    enabled: chatId != null,
    staleTime: 10_000,
  });
}

export function useCreateChat() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      character_id: number;
      title?: string;
    }) => createChat(payload),
    onSuccess: (created: Chat) => {
      qc.invalidateQueries({ queryKey: keys.chats() });
      qc.invalidateQueries({ queryKey: keys.messages(created.id) });
    },
  });
}

export function useDeleteChat() {
  const qc = useQueryClient();
  const pushError = useErrorStore((s) => s.pushError);
  return useMutation({
    mutationFn: (chatId: number) => deleteChat(chatId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.chats() });
    },
    onError: (err) => {
      pushError(err);
    },
  });
}

export function useClearChat() {
  const qc = useQueryClient();
  const pushError = useErrorStore((s) => s.pushError);
  return useMutation({
    mutationFn: (chatId: number) => clearChat(chatId),
    onSuccess: (_data, chatId) => {
      qc.setQueryData(keys.messages(chatId), []);
      qc.invalidateQueries({ queryKey: keys.chats() });
      qc.invalidateQueries({ queryKey: keys.messages(chatId) });
    },
    onError: (err) => {
      pushError(err);
    },
  });
}

export function useDeleteMessageAndFollowing() {
  const qc = useQueryClient();
  const pushError = useErrorStore((s) => s.pushError);
  return useMutation({
    mutationFn: (vars: { chatId: number; messageId: number }) =>
      deleteMessageAndFollowing(vars.chatId, vars.messageId),
    onSuccess: (_data, vars) => {
      qc.setQueryData<Message[]>(keys.messages(vars.chatId), (prev) => {
        if (!prev) return prev;
        return prev.filter((msg) => msg.id < vars.messageId);
      });
      qc.invalidateQueries({ queryKey: keys.chats() });
      qc.invalidateQueries({ queryKey: keys.messages(vars.chatId) });
    },
    onError: (err) => {
      pushError(err);
    },
  });
}
