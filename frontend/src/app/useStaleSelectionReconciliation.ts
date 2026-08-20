import { useEffect, useRef } from "react";
import { useUiStore } from "@/lib/store/uiStore";
import { useChats } from "@/lib/query/chats";
import { useCharacters } from "@/lib/query/characters";
import { useModels } from "@/lib/query/models";
import { useSettings, useSetSelectedModel } from "@/lib/query/settings";

/**
 * useStaleSelectionReconciliation - clears persisted selections that no
 * longer exist on the server, and (for the model) bridges uiStore's
 * in-memory selection to the vault.
 *
 * uiStore persists selectedChatId/selectedCharacterId in localStorage, which
 * survives DB resets and model-list changes. selectedModelId does NOT persist
 * there (v1.2 privacy fix - it is a model NAME, not a bare id) and instead
 * round-trips through the encrypted settings table; the hydrate/push effects
 * below are that round trip. A stale id in any of the three would leave the
 * app pointing at a chat/character/model that 404s.
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
 * Mounted once in AppShell, which only renders after the vault is unlocked -
 * so none of this runs, and selectedModelId stays whatever it already was in
 * memory (null on a fresh launch), while the vault is locked.
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

  // ── the model selection's vault round trip (v1.2 privacy fix) ───────────
  const settingsQuery = useSettings();
  const saveSelectedModel = useSetSelectedModel();
  const saveModel = saveSelectedModel.mutate;
  // Has the one-time hydration read happened yet?
  const hydratedRef = useRef(false);
  // What we believe the vault currently holds, so a later render only pushes
  // on a REAL change - never as an echo of a value this same hook just set.
  const lastKnownRef = useRef<string | null>(null);

  useEffect(() => {
    if (!settingsQuery.isSuccess) return;
    if (!hydratedRef.current) {
      hydratedRef.current = true;
      const vaultValue = settingsQuery.data.selected_model_id;
      lastKnownRef.current = vaultValue;
      // Only hydrate into an EMPTY selection. If something is already chosen
      // in memory - the ordinary case on a vault re-lock/unlock within the
      // same session, since this store is a module singleton that outlives
      // the component - the in-memory value wins, the same tie-break
      // narrationMigration.ts uses for its own device-vs-vault disagreement:
      // it is what the picker has been SHOWING, so it is the answer the user
      // believes is selected. STOP here rather than falling into the push
      // check below: `selectModel` schedules a state update this render does
      // not see yet, so `selectedModelId` below is still the PRE-hydration
      // value for one more pass - comparing it now would read as "changed"
      // and immediately re-push the value hydration only just set.
      if (selectedModelId == null && vaultValue != null) {
        selectModel(vaultValue);
        return;
      }
      // Nothing to hydrate FROM (vault empty), or the device value wins over
      // the vault's - either way `selectedModelId` here is already accurate
      // (this pass never called `selectModel`), so it is safe to fall
      // through to the ordinary push check immediately below.
    }
    if (selectedModelId === lastKnownRef.current) return;
    lastKnownRef.current = selectedModelId;
    saveModel(selectedModelId);
  }, [settingsQuery.isSuccess, settingsQuery.data, selectedModelId, selectModel, saveModel]);

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
