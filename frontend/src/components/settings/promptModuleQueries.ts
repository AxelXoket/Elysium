/**
 * React Query hooks for the prompt modules page.
 *
 * Kept beside the page rather than in lib/query because nothing else reads
 * them. The keys sit under one root so a single invalidate sweeps both the
 * list and the settings, and the vault lock clears them with the rest of the
 * cache.
 */
import { useIsMutating, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useGenerationSettings } from "@/components/generation/GenerationSettingsContext";
import { useUiStore } from "@/lib/store/uiStore";
import {
  deletePromptModule,
  enablePresetDefaults,
  getPromptModuleSettings,
  importPromptModuleFile,
  listPromptModules,
  patchPromptModule,
  patchPromptModuleImport,
  clearChatVariables,
  putPromptModuleSettings,
  PROMPT_MODULE_LIST_KEY,
  type PromptModuleBudgetInputs,
  type PromptModulePatch,
} from "@/lib/api/promptModules";
import {
  PromptModuleSchema,
  type PromptModule,
  type PromptModuleList,
  type PromptModuleSettings,
} from "@/lib/schemas/promptModules";

export const promptModuleKeys = {
  root: () => ["promptModules"] as const,
  list: () => PROMPT_MODULE_LIST_KEY,
  settings: () => ["promptModules", "settings"] as const,
  /** Every write that changes the list carries this key, so one count says
   *  whether any of them is still settling. */
  write: () => ["promptModules", "write"] as const,
};

export function usePromptModules() {
  return useQuery({ queryKey: promptModuleKeys.list(), queryFn: listPromptModules });
}

export function usePromptModuleSettings() {
  return useQuery({
    queryKey: promptModuleKeys.settings(),
    queryFn: getPromptModuleSettings,
  });
}

/** Every write refreshes both queries: turning a module on changes whether
 *  the layer is sent, so the settings half of the page depends on the list. */
function useInvalidateAll() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: promptModuleKeys.root() });
}

export function useImportPromptModule() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationKey: promptModuleKeys.write(),
    mutationFn: (file: File) => importPromptModuleFile(file),
    onSuccess: () => invalidate(),
  });
}

/**
 * What a toggle is checked against: the open chat and the window the next
 * request would use. Read at click time through `getRequestSettings`, the
 * same function the send path calls, so the toggle and the request cannot
 * disagree about the window.
 */
function useBudgetInputs(): () => PromptModuleBudgetInputs {
  const { getRequestSettings } = useGenerationSettings();
  const chatId = useUiStore((s) => s.selectedChatId);
  const modelId = useUiStore((s) => s.selectedModelId);
  return () => {
    const { generationParams, contextBudgetTokens } = getRequestSettings();
    const positive = (n: number | null | undefined) =>
      typeof n === "number" && Number.isInteger(n) && n > 0 ? n : null;
    return {
      chat_id: positive(chatId),
      context_budget_tokens: positive(contextBudgetTokens),
      max_tokens: positive(generationParams.max_tokens),
      model_id: modelId ?? null,
    };
  };
}

export function usePatchPromptModule() {
  const qc = useQueryClient();
  const invalidate = useInvalidateAll();
  const budget = useBudgetInputs();
  return useMutation({
    mutationKey: promptModuleKeys.write(),
    mutationFn: ({ id, patch }: { id: number; patch: PromptModulePatch }) =>
      patchPromptModule(id, patch, budget()),
    // The answer is the row as the vault now holds it, so the list shows it
    // at once. A move is left to the refetch: it renumbers the other rows too.
    onSuccess: (answer, { patch }) => {
      if (patch.sort_order !== undefined) return;
      const row = PromptModuleSchema.safeParse(answer);
      if (!row.success) return;
      qc.setQueryData<PromptModuleList>(promptModuleKeys.list(), (old) =>
        old
          ? { ...old, modules: old.modules.map((m) => (m.id === row.data.id ? row.data : m)) }
          : old,
      );
    },
    // Settled, not success: a refused toggle must put the switch back to what
    // the vault holds, and the refetch is what does that. The promise is
    // returned so the write stays pending until the list is fresh, and a
    // second click cannot land on the old list.
    onSettled: () => invalidate(),
  });
}

/**
 * True while any list write, or the refetch that follows it, is out: an
 * import, a toggle, a move, a rename, a preset's set or a delete. A switch
 * clicked in that time would act on a list that is about to change.
 */
export function usePromptModuleWriting(): boolean {
  return useIsMutating({ mutationKey: promptModuleKeys.write() }) > 0;
}

/**
 * The same question asked of the cache at this instant. The hook above
 * answers from the last render, and two clicks can land before the next one;
 * a write is in the cache from the moment it is started.
 */
export function useIsPromptModuleWriting(): () => boolean {
  const qc = useQueryClient();
  return () => qc.isMutating({ mutationKey: promptModuleKeys.write() }) > 0;
}

/**
 * The row as the cache holds it now, not as the last render saw it. A click
 * reads the switch's next value from here, so a render that lags the cache
 * cannot send the same intent twice.
 */
export function useLatestPromptModule(): (id: number) => PromptModule | undefined {
  const qc = useQueryClient();
  return (id) =>
    qc
      .getQueryData<PromptModuleList>(promptModuleKeys.list())
      ?.modules.find((m) => m.id === id);
}

export function useEnablePresetDefaults() {
  const invalidate = useInvalidateAll();
  const budget = useBudgetInputs();
  return useMutation({
    mutationKey: promptModuleKeys.write(),
    mutationFn: (importId: number) => enablePresetDefaults(importId, budget()),
    onSettled: () => invalidate(),
  });
}

export function usePatchPromptModuleImport() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: ({ id, historyCleanup }: { id: number; historyCleanup: boolean }) =>
      patchPromptModuleImport(id, { history_cleanup: historyCleanup }),
    onSettled: () => void invalidate(),
  });
}

export function useClearChatVariables() {
  return useMutation({ mutationFn: (chatId: number) => clearChatVariables(chatId) });
}

export function useDeletePromptModule() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationKey: promptModuleKeys.write(),
    mutationFn: (id: number) => deletePromptModule(id),
    onSettled: () => invalidate(),
  });
}

export function useSavePromptModuleSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (next: PromptModuleSettings) => putPromptModuleSettings(next),
    onSuccess: (saved) => qc.setQueryData(promptModuleKeys.settings(), saved),
    onSettled: () =>
      void qc.invalidateQueries({ queryKey: promptModuleKeys.settings() }),
  });
}
