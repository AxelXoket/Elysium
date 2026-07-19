/**
 * estimateContextUsage.ts - Live context usage estimate for the current chat.
 *
 * Mirrors backend budget math in routers/completions.py - keep in sync.
 *
 * The backend (backend/routers/completions.py + backend/config.py) budgets the
 * prompt in CHARACTERS with a fixed chars-per-token estimate, then trims the
 * oldest history messages until the rest fits. This module replays that exact
 * arithmetic on the frontend so the UI can show an estimated
 * tokens-used vs capacity meter that updates live.
 *
 * Backend formulas mirrored here (cited per step below):
 *  - config.py:        CHARS_PER_TOKEN_ESTIMATE = 3, CONTEXT_SAFETY_MARGIN = 256
 *  - completions.py:   _DEFAULT_CONTEXT_LEN = 32000, _DEFAULT_MAX_TOKENS = 2048
 *  - completions.py:   _build_system_block() - "[Label]\n{value}" sections
 *                      joined by "\n\n", empty (stripped) sections skipped
 *  - completions.py:   persona_block = active persona description (stripped)
 *  - completions.py:   effective_tokens = budget set ? min(budget, model_ctx)
 *                      : model_ctx; safety = min(256, effective // 8);
 *                      context_budget_chars = max(0, effective - safety) * 3;
 *                      max_tokens_chars = max_tokens_val * 3, halved to
 *                      floor(context_budget_chars / 2) when it exceeds the
 *                      budget; available = context_budget_chars - max_tokens_chars
 *  - completions.py:   _assemble_messages() - drop OLDEST history messages
 *                      until the remainder fits the available budget
 *
 * Known, deliberate approximations (this is an ESTIMATE, never exact):
 *  - The backend appends post_history_instruction AFTER trimming without
 *    counting it against the trim budget; we include its length in the fixed
 *    cost so the estimate stays conservative.
 *  - The backend additionally reserves the pending user message being sent;
 *    the preview has no pending message, so it reflects the state right now.
 *  - Python len() counts code points, JS .length counts UTF-16 code units -
 *    they differ on astral characters (emoji). Chars-per-token is itself an
 *    estimate, so this is noise.
 *  - Image attachments are charged at a flat IMAGE_TOKEN_ESTIMATE per
 *    attachment, matching the backend's per-image budget estimate.
 */

import type { Character } from "../schemas/characters";
import type { Message } from "../schemas/chats";
import type { GenerationParams } from "../schemas/completions";
import type { Model } from "../schemas/models";
import type { Persona } from "../schemas/personas";
import {
  clampContextBudget,
  clampMaxTokens,
  filterParamsByModel,
} from "../generation";
import {
  getModelContextLength,
  getModelMaxCompletionTokens,
} from "../models";
import { findActivePersona } from "../personas";
import { isMessageActive } from "../chat";

// ── Constants (backend parity) ───────────────────────────────────

/** config.py CHARS_PER_TOKEN_ESTIMATE - deliberately conservative (3, not 4). */
export const CHARS_PER_TOKEN = 3;

/** config.py CONTEXT_SAFETY_MARGIN - tokens reserved as safety buffer. */
const CONTEXT_SAFETY_MARGIN = 256;

/** completions.py _DEFAULT_CONTEXT_LEN - used when model metadata is missing. */
const DEFAULT_CONTEXT_LEN = 32000;

/** completions.py _DEFAULT_MAX_TOKENS - used when model metadata is missing. */
const DEFAULT_MAX_TOKENS = 2048;

/**
 * Estimated token cost of one image attachment on a history message
 * (backend IMAGE_TOKEN_ESTIMATE). Charged as IMAGE_TOKEN_ESTIMATE *
 * CHARS_PER_TOKEN characters against that message during trimming.
 */
export const IMAGE_TOKEN_ESTIMATE = 1100;

/** Meter turns amber at this used/capacity percentage. */
export const CONTEXT_WARNING_PERCENT = 75;

/** Meter turns danger-red at this used/capacity percentage. */
export const CONTEXT_DANGER_PERCENT = 92;

// ── Types ────────────────────────────────────────────────────────

export interface ContextUsageInput {
  /** Selected model, or null/undefined when none selected. */
  model?: Model | null;
  /** The current chat's character (chats.character_id), or null. */
  character?: Character | null;
  /** Full persona list - the active one becomes the persona block. */
  personas?: readonly Persona[] | null;
  /** Current chat history; null/undefined when no chat is selected. */
  messages?: readonly Message[] | null;
  /** User-set generation params (pre-filtering, as held by the UI). */
  generationParams?: GenerationParams | null;
  /** User-set app-level context budget in tokens; null/undefined = not set. */
  contextBudgetTokens?: number | null;
}

export interface ContextUsageEstimate {
  /** Estimated prompt tokens used right now (fixed blocks + kept history). */
  usedTokens: number;
  /** Estimated prompt capacity after safety margin and output reservation. */
  capacityTokens: number;
  /** Tokens reserved for the model's output (max_tokens reservation). */
  reservedOutputTokens: number;
  /** usedTokens / capacityTokens as a percentage, clamped to 0-100. */
  percent: number;
  /** History messages that fit within the budget. */
  includedMessages: number;
  /** Oldest history messages the backend would drop. */
  droppedMessages: number;
  /** Total history messages in the chat. */
  totalMessages: number;
  /** Always true - this is chars/3 math, never a real tokenizer count. */
  isEstimate: true;
}

export type ContextUsageState = "normal" | "warning" | "danger";

// ── System block (completions.py _build_system_block) ───────────

/** Section order and labels - must match _build_system_block exactly. */
const SYSTEM_BLOCK_SECTIONS: readonly (readonly [
  label: string,
  field: "system_prompt" | "description" | "personality" | "scenario" | "mes_example",
])[] = [
  ["System Prompt", "system_prompt"],
  ["Description", "description"],
  ["Personality", "personality"],
  ["Scenario", "scenario"],
  ["Example Dialogue", "mes_example"],
];

/**
 * Build the system-role block from character fields, mirroring
 * completions.py _build_system_block(): each non-empty (trimmed) section is
 * rendered as "[Label]\n{value}" and sections are joined by "\n\n".
 */
export function buildSystemBlock(character: Character): string {
  const sections: string[] = [];
  for (const [label, field] of SYSTEM_BLOCK_SECTIONS) {
    const value = character[field].trim();
    if (value) {
      sections.push(`[${label}]\n${value}`);
    }
  }
  return sections.join("\n\n");
}

// ── Per-message cost ─────────────────────────────────────────────

/**
 * Character cost of one history message: content length plus a flat
 * IMAGE_TOKEN_ESTIMATE * CHARS_PER_TOKEN per attachment.
 *
 * `attachments` is read defensively - the Message schema may gain an optional
 * attachments array; until then the safe optional read yields 0.
 */
function messageChars(message: Message): number {
  const attachments = (
    message as Message & { attachments?: readonly { id: number }[] }
  ).attachments;
  const attachmentCount = attachments?.length ?? 0;
  return (
    message.content.length +
    attachmentCount * IMAGE_TOKEN_ESTIMATE * CHARS_PER_TOKEN
  );
}

// ── Estimator ────────────────────────────────────────────────────

/**
 * Estimate how much of the model's context the current chat uses right now.
 *
 * Returns null when the estimate cannot be formed (no model, no character,
 * or no chat/messages). An empty chat ([]) is a valid input.
 *
 * The generation params and budget are first passed through the same
 * filter/clamp helpers as buildCompletionPayload (lib/generation), so the
 * estimate reflects the request the backend would actually receive - e.g. a
 * user max_tokens the model does not advertise is never sent, and the
 * backend then falls back to model metadata.
 */
export function estimateContextUsage(
  input: ContextUsageInput,
): ContextUsageEstimate | null {
  const { model, character, messages: rawMessages } = input;
  if (!model || !character || rawMessages == null) return null;

  // completions.py history queries filter `active = 1` - inactive variant
  // siblings (GET /messages returns them for the carousel) never reach the
  // provider payload, so they must not count against the budget here either.
  const messages = rawMessages.filter(isMessageActive);

  // What the outgoing request would carry (mirrors buildCompletionPayload):
  // params filtered by model support, max_tokens/budget clamped.
  const filteredParams = filterParamsByModel(input.generationParams, model);
  const requestMaxTokens = clampMaxTokens(filteredParams?.max_tokens, model);
  const requestBudget = clampContextBudget(input.contextBudgetTokens, model);

  // completions.py: model_ctx / meta_max_tokens fall back to defaults when
  // metadata is missing or non-positive (the helpers return null for those).
  const modelCtx = getModelContextLength(model) ?? DEFAULT_CONTEXT_LEN;
  const metaMaxTokens =
    getModelMaxCompletionTokens(model) ?? DEFAULT_MAX_TOKENS;

  // completions.py: effective_tokens = min(user_budget, model_ctx) when the
  // budget is set, otherwise model_ctx.
  const effectiveTokens =
    requestBudget != null ? Math.min(requestBudget, modelCtx) : modelCtx;

  // completions.py: max_tokens_val = req max_tokens if truthy else metadata.
  // (clampMaxTokens never returns < 1, so a plain null-check matches.)
  const maxTokensVal = requestMaxTokens ?? metaMaxTokens;

  // completions.py: safety = min(CONTEXT_SAFETY_MARGIN, effective_tokens // 8)
  const safety = Math.min(
    CONTEXT_SAFETY_MARGIN,
    Math.floor(effectiveTokens / 8),
  );

  // completions.py: context_budget_chars = max(0, effective - safety) * 3
  const contextBudgetChars =
    Math.max(0, effectiveTokens - safety) * CHARS_PER_TOKEN;

  // completions.py: reservation, halved when it exceeds the whole budget.
  let maxTokensChars = maxTokensVal * CHARS_PER_TOKEN;
  if (maxTokensChars > contextBudgetChars) {
    maxTokensChars = Math.max(0, Math.floor(contextBudgetChars / 2));
  }

  // completions.py _assemble_messages: available = budget - reservation.
  const availableChars = contextBudgetChars - maxTokensChars;

  // Fixed prompt cost: system block + persona block (+ post-history
  // instruction - see the approximation note in the module doc comment).
  const systemBlock = buildSystemBlock(character);
  const personaBlock = (findActivePersona(input.personas)?.description ?? "")
    .trim();
  const phi = character.post_history_instruction.trim();
  const fixedChars = systemBlock.length + personaBlock.length + phi.length;

  // completions.py _assemble_messages: trim history from the OLDEST end
  // until it fits what is left after the fixed cost.
  const perMessageChars = messages.map(messageChars);
  let historyChars = perMessageChars.reduce((sum, chars) => sum + chars, 0);
  const remaining = availableChars - fixedChars;
  let droppedMessages = 0;
  while (historyChars > remaining && droppedMessages < messages.length) {
    historyChars -= perMessageChars[droppedMessages];
    droppedMessages += 1;
  }

  const totalMessages = messages.length;
  const includedMessages = totalMessages - droppedMessages;
  const usedChars = fixedChars + historyChars;

  const usedTokens = Math.ceil(usedChars / CHARS_PER_TOKEN);
  const capacityTokens = Math.floor(availableChars / CHARS_PER_TOKEN);
  const reservedOutputTokens = Math.floor(maxTokensChars / CHARS_PER_TOKEN);
  const percent =
    capacityTokens > 0
      ? Math.min(100, Math.max(0, (usedTokens / capacityTokens) * 100))
      : usedTokens > 0
        ? 100
        : 0;

  return {
    usedTokens,
    capacityTokens,
    reservedOutputTokens,
    percent,
    includedMessages,
    droppedMessages,
    totalMessages,
    isEstimate: true,
  };
}

// ── Display helpers ──────────────────────────────────────────────

/**
 * Meter severity for a used/capacity percentage:
 * normal below 75, warning at 75-91.99, danger at 92 and above.
 */
export function getContextUsageState(percent: number): ContextUsageState {
  if (percent >= CONTEXT_DANGER_PERCENT) return "danger";
  if (percent >= CONTEXT_WARNING_PERCENT) return "warning";
  return "normal";
}

/**
 * Compact token count for meter labels, following the panel's K style:
 * values under 1000 render as-is, larger ones as one-decimal K with a
 * trailing ".0" trimmed (950 -> "950", 8064 -> "8.1K", 128000 -> "128K").
 */
export function formatTokensCompact(tokens: number): string {
  if (tokens < 1000) return String(tokens);
  const rendered = (tokens / 1000).toFixed(1);
  return `${rendered.endsWith(".0") ? rendered.slice(0, -2) : rendered}K`;
}
