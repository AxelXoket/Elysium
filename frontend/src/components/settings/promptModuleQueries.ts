/**
 * React Query hooks for the prompt modules page.
 *
 * Kept beside the page rather than in lib/query because nothing else reads
 * them. The keys sit under one root so a single invalidate sweeps both the
 * list and the settings, and the vault lock clears them with the rest of the
 * cache.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useGenerationSettings } from "@/components/generation/GenerationSettingsContext";
import { useUiStore } from "@/lib/store/uiStore";
import {
  deletePromptModule,
  enablePresetDefaults,
  getPromptModuleSettings,
  importPromptModuleFile,
  listPromptModules,
  patchPromptModule,
  putPromptModuleSettings,
  PROMPT_MODULE_LIST_KEY,
  type PromptModuleBudgetInputs,
  type PromptModulePatch,
} from "@/lib/api/promptModules";
import type { PromptModuleSettings } from "@/lib/schemas/promptModules";

export const promptModuleKeys = {
  root: () => ["promptModules"] as const,
  list: () => PROMPT_MODULE_LIST_KEY,
  settings: () => ["promptModules", "settings"] as const,
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
    mutationFn: (file: File) => importPromptModuleFile(file),
    onSuccess: () => void invalidate(),
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
  const invalidate = useInvalidateAll();
  const budget = useBudgetInputs();
  return useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: PromptModulePatch }) =>
      patchPromptModule(id, patch, budget()),
    // Settled, not success: a refused toggle must put the switch back to what
    // the vault holds, and the refetch is what does that.
    onSettled: () => void invalidate(),
  });
}

export function useEnablePresetDefaults() {
  const invalidate = useInvalidateAll();
  const budget = useBudgetInputs();
  return useMutation({
    mutationFn: (importId: number) => enablePresetDefaults(importId, budget()),
    onSettled: () => void invalidate(),
  });
}

export function useDeletePromptModule() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: (id: number) => deletePromptModule(id),
    onSettled: () => void invalidate(),
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
