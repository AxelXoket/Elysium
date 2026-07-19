import { useEffect } from "react";
import { useUiStore } from "@/lib/store/uiStore";
import { useChats } from "@/lib/query/chats";
import { useCharacters } from "@/lib/query/characters";
import { useModels } from "@/lib/query/models";

/**
 * useStaleSelectionReconciliation - clears persisted selections that no
 * longer exist on the server.
 *
 * uiStore persists selectedChatId/selectedCharacterId/selectedModelId in
 * localStorage, which survives DB resets and model-list changes. A stale id
 * would leave the app pointing at a chat/character/model that 404s.
 *
 * Rules:
 *  - Only reconcile when the corresponding list query is SUCCESS and NOT
 *    currently fetching. The `isFetching` guard is essential: right after
 *    creating an entity we `select` its id and invalidate the list, which
 *    keeps `isSuccess` true while serving STALE data during the refetch.
 *    reconciling in that window would see the fresh id missing from the old
 *    list and immediately clear a perfectly valid selection. Waiting for the
 *    refetch to settle avoids that race.
 *  - NEVER clear while loading or on error - e.g. the models request can 401
 *    before an API key is set, and wiping the selection then would lose
 *    perfectly valid state.
 *
 * Mounted once in AppShell.
 */
export function useStaleSelectionReconciliation(): void {
  const selectedChatId = useUiStore((s) => s.selectedChatId);
  const selectedCharacterId = useUiStore((s) => s.selectedCharacterId);
  const selectedModelId = useUiStore((s) => s.selectedModelId);
  const selectChat = useUiStore((s) => s.selectChat);
  const selectCharacter = useUiStore((s) => s.selectCharacter);
  const selectModel = useUiStore((s) => s.selectModel);

  const chats = useChats();
  const characters = useCharacters();
  const models = useModels();

  useEffect(() => {
    if (!chats.isSuccess || chats.isFetching || selectedChatId == null) return;
    if (!chats.data.some((chat) => chat.id === selectedChatId)) {
      selectChat(null);
    }
  }, [chats.isSuccess, chats.isFetching, chats.data, selectedChatId, selectChat]);

  useEffect(() => {
    if (!characters.isSuccess || characters.isFetching || selectedCharacterId == null) return;
    if (!characters.data.some((c) => c.id === selectedCharacterId)) {
      // Note: selectCharacter(null) also clears the chat selection (store
      // behavior) - correct here, since chats cascade with their character.
      selectCharacter(null);
    }
  }, [
    characters.isSuccess,
    characters.isFetching,
    characters.data,
    selectedCharacterId,
    selectCharacter,
  ]);

  useEffect(() => {
    if (!models.isSuccess || models.isFetching || selectedModelId == null) return;
    if (!models.data.models.some((m) => m.id === selectedModelId)) {
      selectModel(null);
    }
  }, [models.isSuccess, models.isFetching, models.data, selectedModelId, selectModel]);
}
