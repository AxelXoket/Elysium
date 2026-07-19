import { useMutation, useQueryClient } from "@tanstack/react-query";
import { keys } from "./keys";
import { completeChat, regenerateMessage } from "../api/completions";
import { useErrorStore } from "../errors";
import { buildCompletionPayload, buildRegeneratePayload } from "../generation";
import type { GenerationParams } from "../schemas/completions";
import type { Model } from "../schemas/models";
import type { Message } from "../schemas/chats";

/** Stable mutation keys - used by ChatCanvas to derive per-chat pending state. */
export const SEND_MESSAGE_MUTATION_KEY = ["sendMessage"] as const;
export const REGENERATE_MESSAGE_MUTATION_KEY = ["regenerateMessage"] as const;

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
  model?: Pick<
    Model,
    "supported_parameters" | "max_completion_tokens" | "context_length"
  > | null;
}

/** Negative IDs are used for optimistic messages - never sent to backend. */
const OPTIMISTIC_ID_BASE = -1000;
let _optimisticCounter = 0;
/** Shared with the streaming path (useStreamingCompletion) - same id space. */
export function nextOptimisticId(): number {
  _optimisticCounter -= 1;
  return OPTIMISTIC_ID_BASE + _optimisticCounter;
}

/**
 * Non-streaming send. Production sends go through useStreamingCompletion
 * (SSE); this mutation is kept as the documented non-streaming fallback and
 * remains fully functional.
 */
export function useSendMessage() {
  const qc = useQueryClient();

  return useMutation({
    mutationKey: SEND_MESSAGE_MUTATION_KEY,
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

      // 2. Snapshot previous messages - fallback only, targeted removal is preferred
      const previousMessages = qc.getQueryData<Message[]>(
        keys.messages(vars.chatId),
      );

      // 3. Append optimistic user message to cache
      const optimisticId = nextOptimisticId();
      const optimisticMessage: Message = {
        id: optimisticId,
        chat_id: vars.chatId,
        role: "user",
        content: vars.message,
        created_at: new Date().toISOString(),
      };

      qc.setQueryData<Message[]>(keys.messages(vars.chatId), (prev) => [
        ...(prev ?? []),
        optimisticMessage,
      ]);

      // 4. Return context for reconciliation/rollback
      return { previousMessages, optimisticId, submittedText: vars.message };
    },

    onSuccess: (data, vars, context) => {
      // Replace own optimistic + append assistant - single atomic cache update
      qc.setQueryData<Message[]>(keys.messages(vars.chatId), (prev) => {
        const existing = prev ?? [];
        // Remove ONLY this mutation's optimistic message - a concurrent send's
        // optimistic entry must survive until its own settle.
        const withoutOwnOptimistic =
          context?.optimisticId != null
            ? existing.filter((m) => m.id !== context.optimisticId)
            : existing;
        // Deduplicate by id before appending
        const existingIds = new Set(withoutOwnOptimistic.map((m) => m.id));
        const toAdd = [data.user_message, data.assistant_message].filter(
          (m) => !existingIds.has(m.id),
        );
        return [...withoutOwnOptimistic, ...toAdd];
      });
      // Refresh chats list (message_count, updated_at)
      qc.invalidateQueries({ queryKey: keys.chats() });
    },

    // Send errors surface in the Composer banner (single surface) - no toast here.
    onError: (_err, vars, context) => {
      if (context?.optimisticId != null) {
        // Remove ONLY this mutation's optimistic message. Restoring the full
        // snapshot could resurrect stale state after a concurrent send committed.
        qc.setQueryData<Message[]>(keys.messages(vars.chatId), (prev) =>
          prev ? prev.filter((m) => m.id !== context.optimisticId) : prev,
        );
      } else if (context?.previousMessages) {
        // Fallback when targeted removal is impossible
        qc.setQueryData(keys.messages(vars.chatId), context.previousMessages);
      }
    },
  });
}

/**
 * Non-streaming regenerate. Production regenerations go through
 * useStreamingCompletion (SSE); this mutation is kept as the documented
 * non-streaming fallback and remains fully functional.
 */
export function useRegenerateMessage() {
  const qc = useQueryClient();
  const pushError = useErrorStore((s) => s.pushError);

  return useMutation({
    mutationKey: REGENERATE_MESSAGE_MUTATION_KEY,
    mutationFn: (vars: RegenerateMutationVars) =>
      regenerateMessage(vars.chatId, vars.messageId, buildRegeneratePayload({
        modelId: vars.modelId,
        generationParams: vars.generationParams,
        personaId: vars.personaId,
        contextBudgetTokens: vars.contextBudgetTokens,
        model: vars.model,
      }) as Parameters<typeof regenerateMessage>[2]),

    onMutate: async (vars) => {
      // Cancel in-flight refetches so a racing refetch can't clobber the
      // onSuccess reconciliation.
      await qc.cancelQueries({ queryKey: keys.messages(vars.chatId) });

      // Snapshot for defensive rollback. NO optimistic cache change: the old
      // assistant message stays visible; the bubble shows a pending state.
      const previousMessages = qc.getQueryData<Message[]>(
        keys.messages(vars.chatId),
      );

      return { previousMessages };
    },

    onSuccess: (data, vars) => {
      // Variant append (mirrors the streaming done transform): deactivate
      // the previous sibling in place, dedupe-append the new active row.
      qc.setQueryData<Message[]>(keys.messages(vars.chatId), (prev) => {
        const existing = prev ?? [];
        const deactivatedId = data.deactivated_message_id ?? null;
        const anchor =
          data.assistant_message.variant_group ?? data.assistant_message.id;
        const next = existing.map((m) =>
          deactivatedId != null && m.id === deactivatedId
            ? { ...m, active: false, variant_group: m.variant_group ?? anchor }
            : m,
        );
        const existingIds = new Set(next.map((m) => m.id));
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

    // Regenerate errors surface as a toast (single surface for regenerate).
    onError: (err, vars, context) => {
      // Defensive restore - no optimistic change was made, but a racing cache
      // write between onMutate and settle would be rolled back here.
      if (context?.previousMessages) {
        qc.setQueryData(keys.messages(vars.chatId), context.previousMessages);
      }
      pushError(err);
    },
  });
}
