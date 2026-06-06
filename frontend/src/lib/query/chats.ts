import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { keys } from "./keys";
import { listChats, createChat, getMessages } from "../api/chats";
import type { Chat } from "../schemas/chats";

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

