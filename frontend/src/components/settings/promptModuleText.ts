/**
 * The owner-approved warnings for prompt modules, and the few numbers the
 * page needs before it asks the server anything.
 *
 * The sources of truth are shared/prompt_module_warnings.json (texts) and
 * shared/prompt_module_limits.json (numbers). Frontend code does not import
 * from the shared folder (build contract A3), so this file is a mirror, and
 * PromptModulesSection.test.tsx holds it to both shared files byte for byte
 * and holds the shared texts to a SHA-256 of the owner's wording. A mirror
 * that drifts, or a text that loses a word, is a red test.
 */

export const PROMPT_MODULE_WARNINGS = {
  import_consent:
    "This file is sent to the model with every character and every message while it is on. You are responsible for its content; the app adds nothing on its own. The model provider's terms still apply, and any violation counts against your API key.",
  counter: "Active modules: {n}, about {x} tokens per message.",
  st_preset:
    "This preset was written for SillyTavern and may not behave exactly the same here.",
} as const;

/** The counter sentence with its two numbers in. Only the placeholders
 *  change; every other byte is the owner's. */
export function formatModuleCounter(enabled: number, tokens: number): string {
  const fmt = new Intl.NumberFormat("en-US");
  return PROMPT_MODULE_WARNINGS.counter
    .replace("{n}", fmt.format(enabled))
    .replace("{x}", fmt.format(tokens));
}

/** Mirror of shared/prompt_module_limits.json, the fields the page reads. */
export const PROMPT_MODULE_LIMITS = {
  txt_md_max_bytes: 98304,
  json_max_bytes: 1048576,
  module_max_chars: 24000,
  module_name_max_chars: 64,
  module_separator: "\n\n",
  reply_length_words_allowed: [150, 300],
  plain_json_format: "elysium.prompt-modules",
  filename_header: "X-Prompt-Module-Filename",
} as const;

/** File types the import accepts. */
export const PROMPT_MODULE_ACCEPT = ".txt,.md,.json";

/**
 * The byte cap for a file, by its extension, or null for a type the import
 * does not take. Checked before sending so a file that the server would
 * refuse unread is refused here without leaving the page.
 */
export function byteCapFor(fileName: string): number | null {
  const lower = fileName.toLowerCase();
  if (lower.endsWith(".txt") || lower.endsWith(".md")) {
    return PROMPT_MODULE_LIMITS.txt_md_max_bytes;
  }
  if (lower.endsWith(".json")) return PROMPT_MODULE_LIMITS.json_max_bytes;
  return null;
}

/**
 * Tokens per word for a reply-length check (A9 condition 3).
 *
 * Measured, not guessed: a hand-written Turkish roleplay reply of 123 words is
 * 311 o200k tokens (2.53 per word); Sonnet 5's tokenizer makes about 30
 * percent more, so 3.3. Turkish is the heavier of the two languages this app
 * is used in (the English version of the same reply is 1.19 per word), and a
 * warning that comes a little early costs less than a reply cut mid-sentence.
 */
export const WORDS_TO_TOKENS = 3.3;

/** Whether a reply of `words` words can fit in `maxTokens` output tokens. */
export function lengthFitsMaxTokens(words: number, maxTokens: number): boolean {
  return Math.ceil(words * WORDS_TO_TOKENS) <= maxTokens;
}

// Badges -------------------------------------------------------------------
//
// The server sends stable keys (backend/prompt_modules/parsers.py BADGE_*);
// this names the ones the page knows. A key it does not know is shown as the
// key itself rather than hidden, because a badge exists to say "this is not
// what it was", and dropping one says the opposite.

/** Where a SillyTavern entry sat, for the depth badge. */
export interface BadgePlacement {
  st_injection_depth?: number | null;
  st_injection_order?: number | null;
  st_injection_trigger?: string[] | null;
}

export function badgeLabel(code: string, placement?: BadgePlacement): string {
  switch (code) {
    case "after_history":
      return "Went after the chat history in SillyTavern";
    case "in_chat_depth": {
      const depth = placement?.st_injection_depth;
      const order = placement?.st_injection_order;
      const at = depth != null ? ` at depth ${depth}` : "";
      const ord = order != null ? `, order ${order}` : "";
      return `Went inside the chat${at}${ord} in SillyTavern`;
    }
    case "role_user":
      return "Had the user role, sent here as system text";
    case "role_assistant":
      return "Had the assistant role, sent here as system text";
    case "extension":
      return "Added by a SillyTavern extension";
    case "not_in_order":
      return "Not in the SillyTavern order, so it was not sent there";
    case "trigger_regenerate_only":
      return "Only on regenerate";
    case "trigger_send_only":
      return "Only on send, not on regenerate";
    case "never_triggers": {
      const which = placement?.st_injection_trigger?.length
        ? `: ${placement.st_injection_trigger.join(", ")}`
        : "";
      return `Never fires in this app${which}`;
    }
    case "over_char_cap":
      return "Over the size limit, cannot be turned on";
    case "empty_content":
      return "No text";
    default:
      return code;
  }
}


// Sources and modes ----------------------------------------------------------

/** Source types that came from SillyTavern, for warning 3. */
export function isSillyTavernKind(kind: string): boolean {
  return kind.startsWith("st_");
}

/** The report key that means warning 3 applies. */
export const ST_WARNING_KEY = "st_preset_behaviour";

/**
 * Which of the three assemblies a request gets (Ek O 8), from the two facts
 * the page has: is any module on, and is the layer switch on.
 *   a: modules first, no layer
 *   b: the layer, with the card in its slots
 *   c: neither, the layer-off assembly
 */
export type PromptMode = "a" | "b" | "c";

export function promptMode(anyModuleOn: boolean, layerSwitch: boolean): PromptMode {
  if (anyModuleOn) return "a";
  return layerSwitch ? "b" : "c";
}
