/**
 * What the enabled modules cost per message, counted the way the request
 * counts it.
 *
 * The server builds each enabled module as: macros rendered, then the
 * sanitizer, then joined with the module separator at the very top of the
 * system message (Ek O 6), and the H4 budget counts that text plus one
 * separator per module. This file does the same with the frontend mirrors of
 * both steps (lib/prompt/assembly.ts, the gauge's own path), so the
 * number on the settings page is the number the request will spend, give or
 * take the character and persona names.
 *
 * The names are the one thing the page cannot know: the settings page is not
 * inside a chat, and modules go to every chat. `{{char}}` and `{{user}}` are
 * rendered as themselves, so each counts as eight characters, which is the
 * length of a typical name. Everything else the macro engine does (comments
 * and `{{original}}` vanish, `{{trim}}` trims) happens for real, because a
 * long `{{// ...}}` comment would otherwise inflate the count for text that
 * never reaches the model.
 */
import { CHARS_PER_TOKEN } from "@/lib/context/estimateContextUsage";
import { moduleCost, moduleFires, moduleTexts } from "@/lib/prompt/assembly";
import { cpLength } from "@/lib/prompt/sanitizer";

import { PROMPT_MODULE_LIMITS } from "./promptModuleText";

/** The stand-in names: `{{char}}` and `{{user}}` render as themselves. */
export const NAME_STAND_INS = { char: "{{char}}", user: "{{user}}" } as const;

/**
 * Characters one module adds, in code points (the unit the server counts
 * in): its text as the request prepares it (lib/prompt/assembly.ts
 * moduleTexts: macros, sanitizer, trim; a blank module is dropped and adds
 * nothing) plus the separator after it (moduleCost).
 *
 * Both preparation steps refuse text over their size ceiling by throwing. A
 * stored module cannot reach that ceiling (the upload cap is far below it),
 * but a throw here would take the whole settings page down with it, so the
 * raw text is counted instead: an estimate that is slightly off beats a
 * blank page.
 */
export function moduleChars(content: string): number {
  try {
    return moduleCost(
      moduleTexts([{ content }], NAME_STAND_INS.char, NAME_STAND_INS.user),
    );
  } catch {
    return cpLength(content) + PROMPT_MODULE_LIMITS.module_separator.length;
  }
}

/** Tokens, with the app's one characters-per-token estimate, rounded up. */
export function charsToTokens(chars: number): number {
  return Math.ceil(chars / CHARS_PER_TOKEN);
}

/** The generation an ordinary message is: backend parsers.GEN_SEND. */
export const SEND_GENERATION = "send";

export interface ModuleCounterValue {
  /** How many modules are on AND go with an ordinary message. */
  enabled: number;
  /** Their prepared text plus separators, in characters. */
  chars: number;
  /** The same, in estimated tokens: the X of the counter sentence. */
  tokens: number;
}

/**
 * The counter: N modules, about X tokens on every message.
 *
 * "Every message" is an ordinary send (contract A4). A module that is on but
 * only rides on regenerate does not go with a message, so it is not in N or
 * X; it still counts for whether the roleplay layer is sent, which is
 * decided by "is any module on" and lives in the page, not here.
 */
export function moduleCounter(
  modules: ReadonlyArray<{
    enabled: boolean;
    content: string;
    triggers?: readonly string[];
  }>,
): ModuleCounterValue {
  const on = modules.filter(
    (m) => m.enabled && moduleFires(m)
      && (m.triggers ?? [SEND_GENERATION]).includes(SEND_GENERATION),
  );
  const chars = on.reduce((sum, m) => sum + moduleChars(m.content), 0);
  return { enabled: on.length, chars, tokens: charsToTokens(chars) };
}
