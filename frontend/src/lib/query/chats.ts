import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { keys } from "./keys";
import { useErrorStore } from "../errors";
import {
  listChats,
  createChat,
  renameChat,
  getMessages,
  deleteChat,
  clearChat,
  deleteMessageAndFollowing,
  activateVariant,
} from "../api/chats";
import { removeMessageAndFollowingFromCache, messageAnchor } from "@/lib/chat";
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
    // Errors surface inline in ChatCreateDialog - deliberately no onError
    // toast here (one-surface rule).
  });
}

export function useRenameChat() {
  const qc = useQueryClient();
  const pushError = useErrorStore((s) => s.pushError);
  return useMutation({
    mutationKey: ["renameChat"],
    mutationFn: (vars: { chatId: number; title: string }) =>
      renameChat(vars.chatId, vars.title),
    onMutate: async (vars) => {
      // Cancel in-flight list refetches so they don't clobber the optimistic title
      await qc.cancelQueries({ queryKey: keys.chats() });
      const previousChats = qc.getQueryData<Chat[]>(keys.chats());
      qc.setQueryData<Chat[]>(keys.chats(), (prev) =>
        prev?.map((c) =>
          c.id === vars.chatId ? { ...c, title: vars.title } : c,
        ),
      );
      return { previousChats };
    },
    onError: (err, _vars, context) => {
      if (context?.previousChats) {
        qc.setQueryData(keys.chats(), context.previousChats);
      }
      // The chat list has no inline error surface - rename errors toast
      // (one-surface rule).
      pushError(err);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: keys.chats() });
    },
  });
}

export function useDeleteChat() {
  const qc = useQueryClient();
  const pushError = useErrorStore((s) => s.pushError);
  return useMutation({
    mutationFn: (chatId: number) => deleteChat(chatId),
    onSuccess: (_data, chatId) => {
      qc.invalidateQueries({ queryKey: keys.chats() });
      // The chat is gone - drop its message cache entirely instead of leaving
      // a stale entry behind.
      qc.removeQueries({ queryKey: keys.messages(chatId) });
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
      // Messages are known to be empty - set directly; only the chat list
      // (message_count/updated_at) needs a refetch.
      qc.setQueryData(keys.messages(chatId), []);
      qc.invalidateQueries({ queryKey: keys.chats() });
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
        return removeMessageAndFollowingFromCache(prev, vars.messageId);
      });
      qc.invalidateQueries({ queryKey: keys.chats() });
      qc.invalidateQueries({ queryKey: keys.messages(vars.chatId) });
    },
    onError: (err) => {
      pushError(err);
    },
  });
}

/** Pure transform: make one row the sole active member of its group. */
function applyActiveFlip(
  messages: readonly Message[] | undefined,
  anchor: number,
  activeId: number,
): Message[] | undefined {
  if (!messages) return messages;
  return messages.map((m) =>
    messageAnchor(m) === anchor ? { ...m, active: m.id === activeId } : m,
  );
}

/**
 * Switch which variant of the last assistant group is active. Optimistic:
 * the group's flags flip immediately (the carousel animates from cache
 * state). No invalidation on success - a refetch here would race a fast
 * second arrow press.
 *
 * Race rules (arrow mashing):
 *  - onSuccess re-applies the flip GROUP-WIDE (a single-row patch could
 *    resurrect a stale active flag beside a newer optimistic flip, leaving
 *    two active rows and snapping the bubble back), and is skipped entirely
 *    while another activate is still pending - the last mutation settles the
 *    final state.
 *  - onError rolls back ONLY this mutation's group (a full snapshot restore
 *    could erase state committed concurrently), then invalidates as a
 *    resync net.
 */
export function useActivateVariant() {
  const qc = useQueryClient();
  const pushError = useErrorStore((s) => s.pushError);
  return useMutation({
    mutationKey: ["activateVariant"],
    mutationFn: (vars: { chatId: number; messageId: number }) =>
      activateVariant(vars.chatId, vars.messageId),
    onMutate: async (vars) => {
      await qc.cancelQueries({ queryKey: keys.messages(vars.chatId) });
      const prev = qc.getQueryData<Message[]>(keys.messages(vars.chatId));
      const target = prev?.find((m) => m.id === vars.messageId);
      if (!target) return { anchor: null, prevActiveId: null };
      const anchor = messageAnchor(target);
      const prevActive = prev?.find(
        (m) => messageAnchor(m) === anchor && m.active !== false,
      );
      qc.setQueryData<Message[]>(keys.messages(vars.chatId), (list) =>
        applyActiveFlip(list, anchor, vars.messageId),
      );
      return { anchor, prevActiveId: prevActive?.id ?? null };
    },
    onSuccess: (data, vars) => {
      // A newer activate is in flight - its optimistic flip is the truth;
      // applying this (older) response would fight it.
      if (qc.isMutating({ mutationKey: ["activateVariant"] }) > 1) return;
      qc.setQueryData<Message[]>(keys.messages(vars.chatId), (list) => {
        const flipped = applyActiveFlip(
          list,
          data.variant_group,
          data.message.id,
        );
        return flipped?.map((m) =>
          m.id === data.message.id ? { ...m, ...data.message } : m,
        );
      });
    },
    onError: (err, vars, context) => {
      if (context?.anchor != null && context.prevActiveId != null) {
        qc.setQueryData<Message[]>(keys.messages(vars.chatId), (list) =>
          applyActiveFlip(list, context.anchor!, context.prevActiveId!),
        );
      }
      // Resync net: whatever the interleaving was, the server settles it.
      qc.invalidateQueries({ queryKey: keys.messages(vars.chatId) });
      pushError(err);
    },
  });
}
