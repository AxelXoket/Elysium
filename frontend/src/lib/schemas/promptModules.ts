/**
 * Prompt modules: the shapes the /prompt-modules routes answer with.
 *
 * Mirrors backend/prompt_modules/store.py `row_to_public` (a module row) and
 * the report dict parsers.py builds for each file type. A module is a file the
 * user brought in (.txt, .md, a SillyTavern file, or the app's own plain
 * JSON); a SillyTavern preset also leaves structure rows (`kind: "marker"`,
 * placeholders like the chat history) so the page can show where each entry
 * sat. The list answer carries each module's text, because the page counts
 * what the enabled modules cost per message on the same frontend mirror the
 * context meter uses. It never carries the original file text.
 *
 * Optional fields default to the answer that promises least, so a newer page
 * against an older server still parses and never shows a switch the server
 * has not said can be used.
 */
import { z } from "zod/v4";

export const PromptModuleSchema = z.object({
  id: z.number(),
  import_id: z.number().nullable().default(null),
  /** "module", or "marker" for a SillyTavern structure row. */
  kind: z.string().default("module"),
  name: z.string(),
  content: z.string().default(""),
  /** Plain JSON `note`: shown here, never sent to the model. */
  note: z.string().nullable().default(null),
  enabled: z.boolean(),
  sort_order: z.number(),
  revision: z.number().default(0),
  /** txt | md | plain_json | st_chat_preset | st_prompt_manager |
   *  st_master_export | st_sysprompt. A string so a new one parses. */
  source_type: z.string().default("txt"),
  source_filename: z.string().nullable().default(null),
  /** The entry's own state in SillyTavern; null when it was not in the
   *  order list, or the module did not come from SillyTavern. */
  st_enabled: z.boolean().nullable().default(null),
  st_role: z.string().nullable().default(null),
  st_injection_depth: z.number().nullable().default(null),
  st_injection_order: z.number().nullable().default(null),
  st_injection_trigger: z.array(z.string()).nullable().default(null),
  /** The app's generations this module rides on ("send", "regenerate").
   *  Missing means both: the counter then counts the module, which is the
   *  worst case, the same way the server's budget rule counts it. */
  triggers: z.array(z.string()).default(["send", "regenerate"]),
  /** Stable keys (parsers.py BADGE_*); the page translates them. */
  badges: z.array(z.string()).default([]),
  /** Whether the server will let this row be switched on. */
  can_enable: z.boolean().default(false),
  /** Why not: marker | empty_content | over_char_cap | never_triggers. */
  blocked_reason: z.string().nullable().default(null),
});
export type PromptModule = z.infer<typeof PromptModuleSchema>;

/** The list route may answer with the rows alone or wrapped; both parse to
 *  the wrapped shape so the page reads one thing. */
export const PromptModuleListSchema = z.preprocess(
  (raw) => (Array.isArray(raw) ? { modules: raw } : raw),
  z.object({ modules: z.array(PromptModuleSchema) }),
);
export type PromptModuleList = z.infer<typeof PromptModuleListSchema>;

const PresetFieldSchema = z.object({
  key: z.string(),
  /** sampler | generation_text | structure_text | prefill | format |
   *  logit_bias | extensions | connection | unknown */
  group: z.string().default("unknown"),
  applied: z.boolean().default(false),
  removed: z.boolean().optional(),
  value_hidden: z.boolean().optional(),
  value: z.union([z.string(), z.number(), z.boolean(), z.null()]).optional(),
  chars: z.number().optional(),
  type: z.string().optional(),
  keys: z.array(z.string()).optional(),
  regex_scripts: z.number().optional(),
  regex_names: z.array(z.string()).optional(),
});
export type PresetField = z.infer<typeof PresetFieldSchema>;

/**
 * What one import did. Every list defaults to empty: a plain text file has
 * none of the SillyTavern sections, and an absent section is "nothing to
 * report", never "not checked".
 */
export const PromptModuleImportReportSchema = z.object({
  source_type: z.string(),
  filename: z.string().nullable().default(null),
  modules_created: z.number().default(0),
  structure_rows: z.number().default(0),
  all_start_off: z.boolean().default(true),
  /** "st_preset_behaviour" means warning 3 applies. */
  warnings: z.array(z.string()).default([]),
  /** JSON paths removed as secrets before the file was stored (never values). */
  secrets_removed: z.array(z.string()).default([]),
  /** "100001" | "100000" | "flat" | "prompts_array". */
  order_source: z.string().nullable().default(null),
  per_character_orders_unused: z.number().default(0),
  markers: z.array(z.string()).default([]),
  /** Badge key to how many entries carry it. */
  badges: z.record(z.string(), z.number()).default({}),
  never_triggered: z
    .array(
      z.object({
        identifier: z.string(),
        triggers: z.array(z.string()).default([]),
      }),
    )
    .default([]),
  order_ids_missing_from_prompts: z.array(z.string()).default([]),
  invalid_entries: z.array(z.number()).default([]),
  duplicate_identifiers: z.array(z.string()).default([]),
  unknown_entry_keys: z
    .array(z.object({ identifier: z.string(), keys: z.array(z.string()) }))
    .default([]),
  preset_fields: z.array(PresetFieldSchema).default([]),
  /** Macro to how many times the macro engine leaves it as it is. */
  unprocessed_macros: z.record(z.string(), z.number()).default({}),
  unsupported_sections: z
    .array(z.object({ section: z.string(), name: z.string() }))
    .default([]),
  unknown_keys: z.array(z.string()).default([]),
  sysprompt_unknown_keys: z.array(z.string()).default([]),
  export_envelope: z
    .object({ unknown_keys: z.array(z.string()).default([]) })
    .nullable()
    .default(null),
});
export type PromptModuleImportReport = z.infer<
  typeof PromptModuleImportReportSchema
>;

/** POST /import: the report, the new import's id, and the rows it made. */
export const PromptModuleImportResponseSchema = z.object({
  import_id: z.number().nullable().default(null),
  report: PromptModuleImportReportSchema,
  modules: z.array(PromptModuleSchema).default([]),
});
export type PromptModuleImportResponse = z.infer<
  typeof PromptModuleImportResponseSchema
>;

export const PromptModuleSettingsSchema = z.object({
  /** The fixed roleplay layer. Off by default (Ek P F). */
  prompt_layer_enabled: z.boolean().default(false),
  /** Null is "adaptive": no length sentence is sent. */
  reply_length_words: z.number().nullable().default(null),
});
export type PromptModuleSettings = z.infer<typeof PromptModuleSettingsSchema>;

/** POST /enable-preset-defaults: what was switched on, and the entries that
 *  SillyTavern had on but that cannot be switched on here, with the reason. */
export const EnablePresetDefaultsResponseSchema = z.object({
  enabled_ids: z.array(z.number()).default([]),
  left_out: z
    .array(z.object({ id: z.number(), reason: z.string() }))
    .default([]),
});
export type EnablePresetDefaultsResponse = z.infer<
  typeof EnablePresetDefaultsResponseSchema
>;

/** Any 2xx body: the page refetches the list after every write, so it
 *  reads nothing from these answers and does not pin their shape. */
export const PromptModuleWriteSchema = z.unknown();

/**
 * The body of a `prompt_modules_over_budget` refusal (budget.py `check`).
 * Turning a module on, or a preset's set, is refused whole when the enabled
 * modules would take more than half of what the history has left (H4); the
 * answer names the modules and what each costs.
 */
export const PromptModulesOverBudgetSchema = z.object({
  detail: z.literal("prompt_modules_over_budget"),
  modules: z.array(
    z.object({
      id: z.number().nullable().default(null),
      name: z.string(),
      tokens: z.number(),
    }),
  ),
  total_tokens: z.number().optional(),
  room_tokens: z.number().optional(),
});
export type PromptModulesOverBudget = z.infer<
  typeof PromptModulesOverBudgetSchema
>;
