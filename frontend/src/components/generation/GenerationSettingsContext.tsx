/* eslint-disable react-refresh/only-export-components -- context module intentionally co-locates its hook/constants with the provider; fast-refresh boundary accepted */
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { GenerationParams } from "@/lib/schemas/completions";
import type { Model } from "@/lib/schemas/models";
import {
  CONTEXT_BUDGET_MIN,
  getModelContextLength,
  getModelMaxCompletionTokens,
} from "@/lib/models";

export interface GenerationSettingsValues {
  temperature: number;
  top_p: number;
  top_k: number;
  repetition_penalty: number;
  max_tokens: number;
  seed: string;
  context_budget_tokens: number;
}

export const GENERATION_SETTINGS_DEFAULTS: GenerationSettingsValues = {
  temperature: 0.8,
  top_p: 0.9,
  top_k: 40,
  repetition_penalty: 1.05,
  max_tokens: 1024,
  seed: "",
  context_budget_tokens: 16384,
};

export const MAX_TOKENS_FALLBACK_MAX = 8192;
export const CONTEXT_BUDGET_FALLBACK_MAX = 32768;

/**
 * UI-side ceiling for the context budget control. Matches the contract
 * maximum for context_budget_tokens (512-2,000,000) so a model advertising
 * a larger context can never offer schema-invalid slider values. The payload
 * builders clamp independently; this keeps the UI consistent with them.
 */
export const CONTEXT_BUDGET_UI_MAX = 2_000_000;

/** Maximum number of stop sequences the UI accepts (kept small on purpose). */
export const MAX_STOP_SEQUENCES = 4;

// Single source lives in lib/models; re-exported for dialog convenience.
export { CONTEXT_BUDGET_MIN };

/** Seed bounds from the backend contract: -(2^31) to 2^31 - 1. */
export const SEED_MIN = -2147483648;
export const SEED_MAX = 2147483647;

interface GenerationSettingsContextValue {
  settings: GenerationSettingsValues;
  setSetting: <K extends keyof GenerationSettingsValues>(
    key: K,
    value: GenerationSettingsValues[K],
  ) => void;
  stopSequences: string[];
  setStopSequences: (sequences: string[]) => void;
  resetAll: (model?: Model | null) => void;
  getRequestSettings: () => {
    generationParams: GenerationParams;
    contextBudgetTokens: number;
  };
}

const GenerationSettingsContext =
  createContext<GenerationSettingsContextValue | null>(null);

export function getMaxTokensUiMax(model: Model | null | undefined): number {
  return getModelMaxCompletionTokens(model) ?? MAX_TOKENS_FALLBACK_MAX;
}

export function getContextBudgetUiMax(model: Model | null | undefined): number {
  const max = getModelContextLength(model) ?? CONTEXT_BUDGET_FALLBACK_MAX;
  return Math.min(max, CONTEXT_BUDGET_UI_MAX);
}

export function clampNumber(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(Math.max(value, min), max);
}

export function getModelAwareGenerationDefaults(
  model: Model | null | undefined,
): GenerationSettingsValues {
  const maxTokensMax = getMaxTokensUiMax(model);
  const contextBudgetMax = getContextBudgetUiMax(model);

  return {
    ...GENERATION_SETTINGS_DEFAULTS,
    max_tokens: Math.min(GENERATION_SETTINGS_DEFAULTS.max_tokens, maxTokensMax),
    context_budget_tokens: Math.min(
      GENERATION_SETTINGS_DEFAULTS.context_budget_tokens,
      contextBudgetMax,
    ),
  };
}

function parseSeed(seed: string): number | undefined {
  const trimmed = seed.trim();
  if (!/^-?\d+$/.test(trimmed)) return undefined;
  // Clamp (not reject) into the contract range so oversized seeds still work.
  return Math.min(Math.max(Number(trimmed), SEED_MIN), SEED_MAX);
}

export function GenerationSettingsProvider({
  children,
}: {
  children: ReactNode;
}) {
  const [settings, setSettings] = useState<GenerationSettingsValues>(
    GENERATION_SETTINGS_DEFAULTS,
  );
  // In-memory only, like the rest of the settings - never persisted.
  const [stopSequences, setStopSequences] = useState<string[]>([]);

  const setSetting = useCallback(
    <K extends keyof GenerationSettingsValues,>(
      key: K,
      value: GenerationSettingsValues[K],
    ) => {
      setSettings((current) => ({ ...current, [key]: value }));
    },
    [],
  );

  const resetAll = useCallback((model?: Model | null) => {
    setSettings(getModelAwareGenerationDefaults(model));
    setStopSequences([]);
  }, []);

  const getRequestSettings = useCallback(() => {
    const generationParams: GenerationParams = {
      temperature: settings.temperature,
      top_p: settings.top_p,
      top_k: settings.top_k,
      repetition_penalty: settings.repetition_penalty,
      max_tokens: settings.max_tokens,
    };
    const seed = parseSeed(settings.seed);
    if (seed != null) {
      generationParams.seed = seed;
    }
    // Always array form; omitted entirely while no sequences are set.
    if (stopSequences.length > 0) {
      generationParams.stop = [...stopSequences];
    }

    return {
      generationParams,
      contextBudgetTokens: settings.context_budget_tokens,
    };
  }, [settings, stopSequences]);

  const value = useMemo(
    () => ({
      settings,
      setSetting,
      stopSequences,
      setStopSequences,
      resetAll,
      getRequestSettings,
    }),
    [settings, setSetting, stopSequences, resetAll, getRequestSettings],
  );

  return (
    <GenerationSettingsContext.Provider value={value}>
      {children}
    </GenerationSettingsContext.Provider>
  );
}

export function useGenerationSettings() {
  const context = useContext(GenerationSettingsContext);
  if (!context) {
    throw new Error("useGenerationSettings must be used inside its provider");
  }
  return context;
}
