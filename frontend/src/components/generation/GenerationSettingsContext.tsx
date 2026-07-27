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
import { useSettings, useSetStopSequences } from "@/lib/query/settings";
import { useErrorStore } from "@/lib/errors";
import { useUiStore } from "@/lib/store/uiStore";
import type { GenPersistedSettings } from "@/lib/store/uiStore";

/** Stable empty list: a fresh `[]` each render would re-run every memo below. */
const EMPTY_STOP: string[] = [];

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

// v1.1 FF7: bridge the in-context values to the neutral persisted names.
function hydrateFromPersisted(p: GenPersistedSettings): GenerationSettingsValues {
  return {
    temperature: p.genTemperature,
    top_p: p.genTopP,
    top_k: p.genTopK,
    repetition_penalty: p.genRepetitionPenalty,
    max_tokens: p.genMaxOutput,
    seed: p.genSeed,
    context_budget_tokens: p.genContextBudget,
  };
}

function toPersisted(v: GenerationSettingsValues): GenPersistedSettings {
  return {
    genTemperature: v.temperature,
    genTopP: v.top_p,
    genTopK: v.top_k,
    genRepetitionPenalty: v.repetition_penalty,
    genMaxOutput: v.max_tokens,
    genSeed: v.seed,
    genContextBudget: v.context_budget_tokens,
  };
}

export function GenerationSettingsProvider({
  children,
}: {
  children: ReactNode;
}) {
  // v1.1 FF7: hydrate the sampling scalars from the persisted (neutral-named)
  // uiStore slice so a vault re-lock (which remounts this provider) no longer
  // wipes them. getRequestSettings clamps independently, so a value exceeding
  // a newly-selected model's cap self-heals at request time.
  const [settings, setSettings] = useState<GenerationSettingsValues>(() =>
    hydrateFromPersisted(useUiStore.getState()),
  );
  // Stop sequences are USER CONTENT (character names), so localStorage is
  // closed to them (privacy rule S-09b). They live in the ENCRYPTED settings
  // table instead - which keeps that rule and stops them being retyped every
  // session and lost on every vault lock, as they were while in-memory only.
  //
  // Server state is the source of truth; this mirrors it so typing stays
  // instant and one save is issued per change rather than per keystroke.
  const settingsQuery = useSettings();
  const saveStopSequences = useSetStopSequences();
  const serverStopSequences = settingsQuery.data?.stop_sequences;
  const [stopDraft, setStopDraft] = useState<string[] | null>(null);
  const stopSequences = stopDraft ?? serverStopSequences ?? EMPTY_STOP;

  // `mutate` is a stable reference in TanStack Query; depending on the whole
  // mutation object would rebuild this callback - and the context value memo
  // below it - on every render.
  const saveStop = saveStopSequences.mutate;
  const setStopSequences = useCallback(
    (next: string[]) => {
      setStopDraft(next);
      saveStop(next, {
        // Retire the mirror only if it is still THIS save's value. Two quick
        // edits otherwise let the first response clear the second one's draft,
        // flashing the older list back on screen until the second lands - the
        // same shape as the voice-settings draft bug.
        onSuccess: () =>
          setStopDraft((current) => (current === next ? null : current)),
        onError: (err) => {
          // Drop the mirror on failure too, so the chips revert to what the
          // vault actually holds. Keeping it would leave the UI showing a
          // value that was never saved, contradicting its own error toast.
          setStopDraft((current) => (current === next ? null : current));
          useErrorStore.getState().pushError(err);
        },
      });
    },
    [saveStop],
  );

  const setSetting = useCallback(
    <K extends keyof GenerationSettingsValues,>(
      key: K,
      value: GenerationSettingsValues[K],
    ) => {
      setSettings((current) => {
        const next = { ...current, [key]: value };
        // Write-through to the persisted slice in the same callback (NOT an
        // effect - lint rule react-hooks/set-state-in-effect).
        useUiStore.getState().setGenSettings(toPersisted(next));
        return next;
      });
    },
    [],
  );

  const resetAll = useCallback(
    (model?: Model | null) => {
      const defaults = getModelAwareGenerationDefaults(model);
      setSettings(defaults);
      setStopSequences([]);
      useUiStore.getState().setGenSettings(toPersisted(defaults));
    },
    [setStopSequences],
  );

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
    [
      settings,
      setSetting,
      stopSequences,
      setStopSequences,
      resetAll,
      getRequestSettings,
    ],
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
