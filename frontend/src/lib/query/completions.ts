import { useMutation, useQueryClient } from "@tanstack/react-query";
import { keys } from "./keys";
import { completeChat } from "../api/completions";
import type { Message } from "../schemas/chats";

export function useSendMessage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { chatId: number; message: string; modelId: string }) =>
      completeChat(vars.chatId, {
        message: vars.message,
        model_id: vars.modelId,
      }),
    onSuccess: (data, vars) => {
      // Append returned messages to cache immediately (no refetch latency).
      // These are confirmed-persisted server state, not optimistic.
      qc.setQueryData<Message[]>(keys.messages(vars.chatId), (prev) => {
        const existing = prev ?? [];
        // Deduplicate by id before appending
        const existingIds = new Set(existing.map((m) => m.id));
        const toAdd = [data.user_message, data.assistant_message].filter(
          (m) => !existingIds.has(m.id),
        );
        return [...existing, ...toAdd];
      });
      // Invalidate chats list to refresh message_count and updated_at
      qc.invalidateQueries({ queryKey: keys.chats() });
    },
  });
}
