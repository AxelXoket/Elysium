import type { Model } from "@/lib/schemas/models";
import { getReasoningMode } from "./modelHelpers";

/**
 * The thinking choice words. The source of truth is
 * shared/thinking_effort_contract.json, which the backend's constants are
 * held to. App code cannot import from shared/ (the dev server only serves
 * frontend/), so this is a mirror and
 * frontend/src/test/components/ThinkingEffort.test.tsx holds it equal to
 * the file.
 */
export const REASONING_EFFORT_ORDER = [
  "minimal",
  "low",
  "medium",
  "high",
  "xhigh",
  "max",
] as const;
export const THINKING_OFF = "off";
export const THINKING_DEFAULT = "default";
export const THINKING_CHOICES = [
  THINKING_OFF,
  THINKING_DEFAULT,
  ...REASONING_EFFORT_ORDER,
] as const;

/** The levels an untouched mandatory model takes its lowest from. "max" is
 *  left out, as on the server: a model listing only "max" is left to the
 *  provider untouched, which is what it always got. */
export const UNTOUCHED_MANDATORY_ORDER = REASONING_EFFORT_ORDER.slice(0, -1);

export type EffortLevel = (typeof REASONING_EFFORT_ORDER)[number];
export type ThinkingChoice = (typeof THINKING_CHOICES)[number];

type ReasoningModel = Pick<Model, "supported_parameters" | "reasoning">;

/** What each choice reads as in the select. */
export const THINKING_CHOICE_LABELS: Record<ThinkingChoice, string> = {
  off: "Off",
  default: "Provider default",
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra high",
  max: "Max",
};

/** The levels this model lists, in the canonical order. "none" and any word
 *  this app does not know are left out; Off covers "none". */
export function listedEfforts(
  model: ReasoningModel | null | undefined,
): EffortLevel[] {
  const raw = model?.reasoning?.supported_efforts;
  if (!Array.isArray(raw)) return [];
  return REASONING_EFFORT_ORDER.filter((level) => raw.includes(level));
}

/**
 * The choices the select offers for this model, read the way the server
 * reads the same catalogue (openrouter.reasoning_param):
 *  - no reasoning listed, or no reasoning object: nothing to choose;
 *  - optional: Off, Provider default, then each listed level;
 *  - mandatory with levels: Provider default, then each listed level;
 *  - mandatory without levels: nothing to choose.
 */
export function thinkingOptions(
  model: ReasoningModel | null | undefined,
): ThinkingChoice[] {
  const mode = getReasoningMode(model);
  const levels = listedEfforts(model);
  if (mode === "optional") return [THINKING_OFF, THINKING_DEFAULT, ...levels];
  if (mode === "mandatory" && levels.length > 0) {
    return [THINKING_DEFAULT, ...levels];
  }
  return [];
}

/** What the request carries when nobody picked anything: Off on optional
 *  reasoning; on mandatory the lowest listed level, or Provider default for
 *  a model that lists only "max". Null when there is nothing to pick. */
export function untouchedThinkingChoice(
  model: ReasoningModel | null | undefined,
): ThinkingChoice | null {
  const mode = getReasoningMode(model);
  if (mode === "optional") return THINKING_OFF;
  const levels = listedEfforts(model);
  if (mode !== "mandatory" || levels.length === 0) return null;
  const lowest = UNTOUCHED_MANDATORY_ORDER.find((level) => levels.includes(level));
  return lowest ?? THINKING_DEFAULT;
}

/** Whether Provider default is the same as Off for this model: its reasoning
 *  is optional and the model list says it is off unless asked. */
export function providerDefaultIsOff(
  model: ReasoningModel | null | undefined,
): boolean {
  return (
    getReasoningMode(model) === "optional" &&
    model?.reasoning?.default_enabled === false
  );
}

/** The choice to show: the stored one while this model still offers it,
 *  otherwise the untouched one, which is also what the server sends for a
 *  stored word the model no longer lists. */
export function resolveThinkingChoice(
  model: ReasoningModel | null | undefined,
  stored: string | null | undefined,
): ThinkingChoice | null {
  const options = thinkingOptions(model);
  const hit = options.find((option) => option === stored);
  return hit ?? untouchedThinkingChoice(model);
}

/**
 * Share of max tokens a level becomes on a budget-based model, as OpenRouter
 * documents it, and the floor its thinking budget never goes under.
 * Provider default counts as the floor alone: the least any thinking takes.
 */
export const THINKING_BUDGET_RATIO: Record<EffortLevel, number> = {
  minimal: 0.1,
  low: 0.2,
  medium: 0.5,
  high: 0.8,
  xhigh: 0.95,
  max: 0.95,
};
export const THINKING_BUDGET_FLOOR = 1024;
export const THINKING_BUDGET_CEILING = 128000;
/** Room the reply keeps after the budget before the warning shows; the
 *  app's own default max output. */
export const THINKING_REPLY_ROOM_MIN = 1024;

/** The thinking budget a budget-based model takes out of `maxTokens`. */
export function thinkingBudget(choice: ThinkingChoice, maxTokens: number): number {
  if (choice === THINKING_OFF) return 0;
  const ratio = choice === THINKING_DEFAULT ? 0 : THINKING_BUDGET_RATIO[choice];
  return Math.max(
    Math.min(Math.floor(maxTokens * ratio), THINKING_BUDGET_CEILING),
    THINKING_BUDGET_FLOOR,
  );
}

/** Whether to warn that max output is too small for the chosen thinking.
 *  Only budget-based models: elsewhere the level is not a share of it. */
export function thinkingBudgetTooSmall(
  model: ReasoningModel | null | undefined,
  choice: ThinkingChoice | null,
  maxTokens: number,
): boolean {
  if (!model?.reasoning?.supports_max_tokens || choice == null) return false;
  if (choice === THINKING_OFF) return false;
  // Provider default on a model that is off unless asked is no thinking,
  // so there is no budget to warn about.
  if (choice === THINKING_DEFAULT && providerDefaultIsOff(model)) return false;
  return maxTokens - thinkingBudget(choice, maxTokens) < THINKING_REPLY_ROOM_MIN;
}
