import { useMutation, useQueryClient } from "@tanstack/react-query";
import { keys } from "./keys";
import { completeChat, regenerateMessage } from "../api/completions";
import { useErrorStore } from "../errors";
import { buildCompletionPayload } from "../generation";
import type { GenerationParams } from "../schemas/completions";
import type { Model } from "../schemas/models";
import type { Message } from "../schemas/chats";

interface CompletionMutationVars {
  chatId: number;
  message: string;
  modelId: string;
  generationParams?: GenerationParams;
  personaId?: number | null;
  contextBudgetTokens?: number | null;
  model?: Pick<
    Model,
    "supported_parameters" | "max_completion_tokens" | "context_length"
  > | null;
}

interface RegenerateMutationVars {
  chatId: number;
  messageId: number;
  modelId: string;
  generationParams?: GenerationParams;
  personaId?: number | null;
  contextBudgetTokens?: number | null;
}

/** Negative IDs are used for optimistic messages — never sent to backend. */
const OPTIMISTIC_ID_BASE = -1000;
let _optimisticCounter = 0;
function nextOptimisticId(): number {
  _optimisticCounter -= 1;
  return OPTIMISTIC_ID_BASE + _optimisticCounter;
}

export function useSendMessage() {
  const qc = useQueryClient();
  const pushError = useErrorStore((s) => s.pushError);

  return useMutation({
    mutationFn: (vars: CompletionMutationVars) =>
      completeChat(vars.chatId, buildCompletionPayload({
        message: vars.message,
        modelId: vars.modelId,
        generationParams: vars.generationParams,
        personaId: vars.personaId,
        contextBudgetTokens: vars.contextBudgetTokens,
        model: vars.model,
      }) as Parameters<typeof completeChat>[1]),

    onMutate: async (vars) => {
      // 1. Cancel in-flight refetches so they don't overwrite our optimistic update
      await qc.cancelQueries({ queryKey: keys.messages(vars.chatId) });

      // 2. Snapshot previous messages for rollback
      const previousMessages = qc.getQueryData<Message[]>(
        keys.messages(vars.chatId),
      );

      // 3. Append optimistic user message to cache
      const optimisticMessage: Message = {
        id: nextOptimisticId(),
        chat_id: vars.chatId,
        role: "user",
        content: vars.message,
        created_at: new Date().toISOString(),
      };

      qc.setQueryData<Message[]>(keys.messages(vars.chatId), (prev) => [
        ...(prev ?? []),
        optimisticMessage,
      ]);

      // 4. Return context for rollback
      return { previousMessages, submittedText: vars.message };
    },

    onSuccess: (data, vars) => {
      // Replace optimistic + append assistant — single atomic cache update
      qc.setQueryData<Message[]>(keys.messages(vars.chatId), (prev) => {
        const existing = prev ?? [];
        // Remove any optimistic messages (negative ids)
        const withoutOptimistic = existing.filter((m) => m.id > 0);
        // Deduplicate by id before appending
        const existingIds = new Set(withoutOptimistic.map((m) => m.id));
        const toAdd = [data.user_message, data.assistant_message].filter(
          (m) => !existingIds.has(m.id),
        );
        return [...withoutOptimistic, ...toAdd];
      });
      // Refresh chats list (message_count, updated_at)
      qc.invalidateQueries({ queryKey: keys.chats() });
    },

    onError: (_err, vars, context) => {
      // Rollback to pre-optimistic state
      if (context?.previousMessages) {
        qc.setQueryData(keys.messages(vars.chatId), context.previousMessages);
      }
      // Push safe error to FE-1A error store
      pushError(_err);
    },
  });
}

export function useRegenerateMessage() {
  const qc = useQueryClient();
  const pushError = useErrorStore((s) => s.pushError);
  return useMutation({
    mutationFn: (vars: RegenerateMutationVars) =>
      regenerateMessage(vars.chatId, vars.messageId, {
        model_id: vars.modelId,
        generation_params: vars.generationParams,
        persona_id: vars.personaId,
        context_budget_tokens: vars.contextBudgetTokens,
      }),
    onSuccess: (data, vars) => {
      qc.setQueryData<Message[]>(keys.messages(vars.chatId), (prev) => {
        const existing = prev ?? [];
        const withoutTarget = existing.filter((m) => m.id !== vars.messageId);
        const existingIds = new Set(withoutTarget.map((m) => m.id));
        const next = [...withoutTarget];
        if (!existingIds.has(data.user_message.id)) {
          next.push(data.user_message);
        }
        if (!existingIds.has(data.assistant_message.id)) {
          next.push(data.assistant_message);
        }
        return next.sort((a, b) => a.id - b.id);
      });
      qc.invalidateQueries({ queryKey: keys.chats() });
    },
    onError: (err) => {
      pushError(err);
    },
  });
}
