import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
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
import { stopChat } from "@/lib/chat/streamRegistry";
import { useDraftStore } from "@/lib/store/draftStore";
import { isApiError } from "../api/client";
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
    onMutate: (chatId) => {
      // Kill the chat's in-flight stream FIRST (v1.1 FF1/H7): otherwise
      // tokens keep burning and its `done` writes the reply into a cache
      // whose chat no longer exists.
      stopChat(chatId);
    },
    onSuccess: (_data, chatId) => {
      // The chat is really gone, so its unsent composer text and every edit
      // buffer held against its messages point at nothing. Cleared HERE and
      // never on the failure path: a delete that did not happen has to leave
      // the drafts exactly where they were.
      useDraftStore.getState().forgetChat(chatId);
      qc.invalidateQueries({ queryKey: keys.chats() });
      // The chat is gone - drop its message cache entirely instead of leaving
      // a stale entry behind.
      qc.removeQueries({ queryKey: keys.messages(chatId) });
      // The notes went with the chat server-side; leaving them cached means
      // the next chat to reuse this id renders somebody else's notebook.
      qc.removeQueries({ queryKey: keys.notebookEntries(chatId) });
      qc.removeQueries({ queryKey: keys.notebookBoundaries(chatId) });
    },
    onError: (err, chatId) => {
      // 404 IS the deletion the user asked for - the chat is already gone -
      // so its drafts go with it. The sibling message-delete states this same
      // rule; leaving the two disagreeing was the inconsistency.
      if (isApiError(err) && err.status === 404) {
        useDraftStore.getState().forgetChat(chatId);
        qc.removeQueries({ queryKey: keys.messages(chatId) });
        qc.removeQueries({ queryKey: keys.notebookEntries(chatId) });
        qc.removeQueries({ queryKey: keys.notebookBoundaries(chatId) });
        qc.invalidateQueries({ queryKey: keys.chats() });
      }
      pushError(err);
    },
  });
}

export function useClearChat() {
  const qc = useQueryClient();
  const pushError = useErrorStore((s) => s.pushError);
  return useMutation({
    mutationFn: (chatId: number) => clearChat(chatId),
    onMutate: (chatId) => {
      // Same rationale as useDeleteChat: abort before emptying, or `done`
      // resurrects the cleared chat (v1.1 FF1/H7). The server-side stale
      // guard (H12/I9) backs this up for the in-transit window.
      stopChat(chatId);
    },
    onSuccess: (_data, chatId) => {
      // Messages are known to be empty - set directly; only the chat list
      // (message_count/updated_at) needs a refetch.
      qc.setQueryData(keys.messages(chatId), []);
      // Every message went, so every edit buffer in this chat is orphaned.
      // The COMPOSER draft deliberately survives: clearing a chat empties its
      // history, it does not throw away the sentence the user is still in the
      // middle of writing.
      useDraftStore.getState().forgetChatMessages(chatId);
      // Clearing a chat clears its notebook AND its chat-scoped limits, so
      // the cache must not keep showing what the server just discarded. The
      // limits half was missing while both sibling sweeps - delete chat and
      // delete character - removed both keys.
      qc.removeQueries({ queryKey: keys.notebookEntries(chatId) });
      qc.removeQueries({ queryKey: keys.notebookBoundaries(chatId) });
      qc.invalidateQueries({ queryKey: keys.chats() });
    },
    onError: (err) => {
      pushError(err);
    },
  });
}

/**
 * Drop the edit buffers of the rows a delete is about to destroy.
 *
 * Read from the cache BEFORE the rows are removed from it, because that is
 * the only place the doomed id set exists: the server answers with a count,
 * never with the ids, and by the time the cache has been rewritten they are
 * gone. The anchor rule is the same one the cache surgery and the backend
 * both use, so the three cannot disagree about what "and following" means.
 */
function forgetDoomedEditDrafts(
  qc: QueryClient,
  chatId: number,
  messageId: number,
): void {
  const before = qc.getQueryData<Message[]>(keys.messages(chatId));
  if (!before) {
    // No cache to read the doomed set from. Forgetting the pressed row's own
    // buffer is strictly better than forgetting nothing: it is the one the
    // user is most likely holding, and it is certainly gone.
    useDraftStore.getState().forgetMessages(chatId, [messageId]);
    return;
  }
  const target = before.find((m) => m.id === messageId);
  const start = target ? messageAnchor(target) : messageId;
  useDraftStore.getState().forgetMessages(
    chatId,
    before.filter((m) => m.id >= start).map((m) => m.id),
  );
}

export function useDeleteMessageAndFollowing() {
  const qc = useQueryClient();
  const pushError = useErrorStore((s) => s.pushError);
  return useMutation({
    mutationFn: (vars: { chatId: number; messageId: number }) =>
      deleteMessageAndFollowing(vars.chatId, vars.messageId),
    onSuccess: (_data, vars) => {
      forgetDoomedEditDrafts(qc, vars.chatId, vars.messageId);
      // Deleting a turn also deletes the unreviewed suggestions that came
      // from it, and rolls back how far the extractor has read. Without this
      // the panel keeps offering proposals the server destroyed, and pressing
      // Keep on one answers 404.
      qc.invalidateQueries({ queryKey: keys.notebookEntries(vars.chatId) });
      qc.setQueryData<Message[]>(keys.messages(vars.chatId), (prev) => {
        if (!prev) return prev;
        return removeMessageAndFollowingFromCache(prev, vars.messageId);
      });
      qc.invalidateQueries({ queryKey: keys.chats() });
      qc.invalidateQueries({ queryKey: keys.messages(vars.chatId) });
    },
    onError: (err, vars) => {
      // 404 = the row is already gone server-side (a ghost the abort race
      // left in the cache - v1.1 D2). "Already deleted" IS the deletion the
      // user asked for: drop it locally and resync, so the ghost cannot
      // survive the toast.
      if (isApiError(err) && err.status === 404) {
        // A 404 IS the deletion the user asked for - the rows do not exist -
        // so the buffers go with them. Every OTHER error leaves them alone:
        // the messages are still there and so is the text.
        // `chat_not_found` reaches this branch too, and it means something
        // larger died than the rows after one message: the whole chat is
        // gone, so the composer buffer and EVERY edit buffer in it are
        // orphaned, including ones before the pressed row.
        if (err.detail === "chat_not_found") {
          useDraftStore.getState().forgetChat(vars.chatId);
        } else {
          forgetDoomedEditDrafts(qc, vars.chatId, vars.messageId);
        }
        qc.setQueryData<Message[]>(keys.messages(vars.chatId), (prev) =>
          prev
            ? removeMessageAndFollowingFromCache(prev, vars.messageId)
            : prev,
        );
        qc.invalidateQueries({ queryKey: keys.messages(vars.chatId) });
        // chat_not_found reaches here too - refresh the list as well.
        qc.invalidateQueries({ queryKey: keys.chats() });
      }
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
