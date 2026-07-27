/**
 * tts.ts - Zod schemas for the voice subsystem (/tts/*).
 *
 * Mirrors docs/frontend_contract.md. Two shapes matter more than the rest:
 *
 *  - `readiness` travels WITH every model row: "you can see its settings, and
 *    you can see it will not run" is the product rule, so the verdict is never
 *    a separate fetch the UI could forget to make.
 *  - `issues[]` carry machine codes from the shared error vocabulary - the UI
 *    renders them through getErrorMessage(), never through the prose `detail`
 *    (which is diagnostic English, not user-facing text).
 *
 * Objects deliberately tolerate additive backend fields (zod strips unknown
 * keys) so a contract-compatible backend upgrade never breaks the client.
 */

import { z } from "zod/v4";

// ── readiness ────────────────────────────────────────────────────────────────

export const TtsIssueSchema = z.object({
  code: z.string(),
  severity: z.enum(["blocker", "warning"]),
  detail: z.string().default(""),
  transient: z.boolean().default(false),
  action: z.string().nullable().default(null),
});

export const TtsFitSchema = z.object({
  fits: z.boolean(),
  estimate_mb: z.number(),
  free_mb: z.number(),
  total_mb: z.number(),
  used_by_others_mb: z.number(),
  headroom_mb: z.number(),
  gpu_available: z.boolean(),
  reason: z.string().nullable().default(null),
  detail: z.string().default(""),
});

export const TtsReadinessSchema = z.object({
  uid: z.string(),
  engine_id: z.string(),
  runnable: z.boolean(),
  settings_available: z.boolean(),
  runtime_state: z.string(),
  issues: z.array(TtsIssueSchema).default([]),
  languages: z.array(z.string()).default([]),
  fit: TtsFitSchema.nullable().default(null),
});

// ── discovery ────────────────────────────────────────────────────────────────

export const TtsModelSchema = z.object({
  uid: z.string(),
  engine_id: z.string(),
  name: z.string(),
  path: z.string(),
  variant: z.string().nullable().default(null),
  source: z.string().default("signature"),
  incomplete: z.boolean().default(false),
  missing: z.array(z.string()).default([]),
  readiness: TtsReadinessSchema,
});

export const TtsScanSchema = z.object({
  models: z.array(TtsModelSchema).default([]),
  unrecognized: z
    .array(
      z.object({
        path: z.string(),
        reason: z.string(),
        code: z.string().default("tts_model_unrecognized"),
      }),
    )
    .default([]),
  roots: z.array(z.string()).default([]),
});

// ── per-model settings ───────────────────────────────────────────────────────

/** One tunable knob, described well enough to render without knowing the
 * engine - the whole settings UI is generated from these. */
export const TtsParamSchema = z.object({
  name: z.string(),
  type: z.enum(["float", "int", "bool", "enum", "text", "voice_ref"]),
  default: z.unknown(),
  label: z.string(),
  help: z.string().default(""),
  minimum: z.number().nullable().default(null),
  maximum: z.number().nullable().default(null),
  step: z.number().nullable().default(null),
  choices: z.array(z.string()).nullable().default(null),
  group: z.string().default("general"),
  advanced: z.boolean().default(false),
});

/** A union row: the spec, plus whether it can do anything on this engine. */
export const TtsMatrixRowSchema = TtsParamSchema.extend({
  editable: z.boolean().default(true),
  /**
   * supported   - this engine really has it
   * unsupported - it does not
   * dead        - it ACCEPTS the value and never applies it (a dial that
   *               cannot move anything is worse than no dial)
   * app_level   - Elysium implements it, so it works on every engine
   */
  status: z
    .enum(["supported", "unsupported", "dead", "app_level"])
    .default("supported"),
  reason: z.string().default(""),
  implemented_by: z.enum(["engine", "elysium"]).optional(),
});
export type TtsMatrixRow = z.infer<typeof TtsMatrixRowSchema>;

export const TtsSchemaSchema = z.object({
  uid: z.string(),
  engine_id: z.string(),
  display_name: z.string(),
  variant: z.string().nullable().default(null),
  capabilities: z.object({
    voice_cloning: z.boolean().default(false),
    needs_reference_transcript: z.boolean().default(false),
    inline_prosody_tags: z.boolean().default(false),
    streaming: z.boolean().default(false),
    /** Can it HEAR a clip and draft its words? No shipped engine can, and
     *  a button that only ever produces an error toast is a broken promise. */
    transcribes_reference: z.boolean().default(false),
    languages: z.array(z.string()).default([]),
    native_sample_rate: z.number().default(24000),
  }),
  params: z.array(TtsParamSchema).default([]),
  /**
   * Every setting EVERY engine has, annotated for this one (V9-5).
   *
   * Separate from `params` on purpose: `params` is what save and validation
   * work from, and merging the two would make the save path guess which knobs
   * are real. Defaulted to `[]` so an older backend simply renders the old
   * panel instead of an error.
   */
  matrix: z.array(TtsMatrixRowSchema).default([]),
});

export type TtsParamValue = string | number | boolean;

export const TtsValuesSchema = z.object({
  uid: z.string(),
  values: z.record(z.string(), z.union([z.string(), z.number(), z.boolean()])),
  source_map: z.record(z.string(), z.string()).default({}),
});

// ── live state ───────────────────────────────────────────────────────────────

export const TtsStateSchema = z.object({
  state: z.string(),
  uid: z.string().nullable().default(null),
  engine_id: z.string().nullable().default(null),
  vram_mb: z.number().nullable().default(null),
  error_code: z.string().nullable().default(null),
  error_detail: z.string().default(""),
  idle_seconds: z.number().nullable().default(null),
});

export const TtsActiveSchema = z.object({
  uid: z.string().nullable().default(null),
  state: z.string(),
  engine_id: z.string().nullable().default(null),
  vram_mb: z.number().nullable().default(null),
  error_code: z.string().nullable().default(null),
  readiness: TtsReadinessSchema.nullable().default(null),
  /** At least one engine runtime is registered and still on disk. Lets the
   *  chat tell "voice does not exist here" (stay silent) apart from "voice is
   *  installed but no model is selected" (say so) - the second state used to
   *  look exactly like a fresh install. */
  voice_installed: z.boolean().default(false),
});

export const TtsSpeakSchema = z.object({
  audio_id: z.string(),
  sample_rate: z.number().nullable().default(null),
  seconds: z.number().nullable().default(null),
  truncated: z.boolean().default(false),
});

// ── engine setup (app-owned provisioning) ────────────────────────────────────

export const TtsRuntimesSchema = z.object({
  runtimes: z
    .array(
      z.object({
        engine_id: z.string(),
        state: z.string(), // missing | ready | broken
        python: z.string().nullable().default(null),
        error_code: z.string().nullable().default(null),
      }),
    )
    .default([]),
  engines: z
    .array(z.object({ engine_id: z.string(), display_name: z.string() }))
    .default([]),
});

export const TtsInstallPlanSchema = z.object({
  engine_id: z.string(),
  env_dir: z.string(),
  requirements: z.string(),
  python_version: z.string(),
  download_mb: z.number(),
  gpu_available: z.boolean().default(true),
});

export const TtsInstallJobSchema = z.object({
  engine_id: z.string(),
  // idle | preparing | installing | verifying | done | failed | cancelled
  state: z.string(),
  log: z.array(z.string()).default([]),
  error_code: z.string().nullable().default(null),
  error_detail: z.string().default(""),
  running: z.boolean().default(false),
});

// ── reference voices ─────────────────────────────────────────────────────────

export const TtsVoiceSchema = z.object({
  voice_id: z.string(),
  label: z.string(),
  audio_name: z.string(),
  transcript: z.string().default(""),
  transcript_source: z.string().default("none"), // user | auto | none
  seconds: z.number().nullable().default(null),
  needs_conversion: z.boolean().default(false),
  has_transcript: z.boolean().default(false),
});

export const TtsVoiceListSchema = z.object({
  voices: z.array(TtsVoiceSchema).default([]),
});

// ── voice mode (the global toggle + G2 gauge input) ──────────────────────────

export const TtsVoiceModeSchema = z.object({
  enabled: z.boolean(),
  /** True only when the delivery prompt would ACTUALLY inject right now
   * (toggle on AND a tag-capable engine selected) - the context gauge charges
   * the block exactly when this is true, mirroring the backend budget. */
  active: z.boolean(),
  prompt_chars: z.number().default(0),
});

export type TtsIssue = z.infer<typeof TtsIssueSchema>;
export type TtsFit = z.infer<typeof TtsFitSchema>;
export type TtsReadiness = z.infer<typeof TtsReadinessSchema>;
export type TtsModel = z.infer<typeof TtsModelSchema>;
export type TtsScan = z.infer<typeof TtsScanSchema>;
export type TtsParam = z.infer<typeof TtsParamSchema>;
export type TtsModelSchemaInfo = z.infer<typeof TtsSchemaSchema>;
export type TtsValues = z.infer<typeof TtsValuesSchema>;
export type TtsState = z.infer<typeof TtsStateSchema>;
export type TtsActive = z.infer<typeof TtsActiveSchema>;
export type TtsSpeak = z.infer<typeof TtsSpeakSchema>;
export type TtsRuntimes = z.infer<typeof TtsRuntimesSchema>;
export type TtsInstallPlan = z.infer<typeof TtsInstallPlanSchema>;
export type TtsInstallJob = z.infer<typeof TtsInstallJobSchema>;
export type TtsVoice = z.infer<typeof TtsVoiceSchema>;
export type TtsVoiceMode = z.infer<typeof TtsVoiceModeSchema>;
