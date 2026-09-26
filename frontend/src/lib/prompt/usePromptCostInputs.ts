/**
 * What the context gauges need to know about prompt modules and the layer,
 * read from the same queries the prompt modules page uses.
 *
 * Only rows the server would send count: enabled AND switchable (the list's
 * `can_enable` is false for markers, empty text, text over the cap and
 * entries that never fire, which store.list_enabled skips too). Stored order.
 * While either query is loading or failed this reports "no module, layer off",
 * which is the gauge reading low rather than inventing a cost.
 */
import {
  usePromptModuleSettings,
  usePromptModules,
} from "@/components/settings/promptModuleQueries";

import type { ModuleInput } from "./assembly";

export interface PromptCostInputs {
  promptModules: ModuleInput[];
  promptLayerEnabled: boolean;
  replyLengthWords: number | null;
}

export function usePromptCostInputs(): PromptCostInputs {
  const modules = usePromptModules();
  const settings = usePromptModuleSettings();
  const rows = modules.data?.modules ?? [];
  return {
    promptModules: rows
      .filter((m) => m.enabled && m.can_enable)
      .sort((a, b) => a.sort_order - b.sort_order || a.id - b.id)
      .map((m) => ({ content: m.content, triggers: m.triggers })),
    promptLayerEnabled: settings.data?.prompt_layer_enabled ?? false,
    replyLengthWords: settings.data?.reply_length_words ?? null,
  };
}
