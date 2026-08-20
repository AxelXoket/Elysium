import { z } from "zod/v4";

/** One note. The fields that matter to the screen are the ones that explain
 *  why a note is or is not reaching the model:
 *
 *  - `provenance` says who wrote it. It is set once at insert and no route can
 *    change it, so the badge on a row is a fact rather than a hint.
 *  - `retired_at` means a newer note replaced this one. It stays on screen and
 *    stops being sent - the owner's rule is that a note never disappears.
 *  - `excluded_reason` means the ceiling bit this turn. Same idea: visible,
 *    not sent, and the reason is written down rather than guessed at.
 */
export const NotebookEntrySchema = z.object({
  id: z.number(),
  chat_id: z.number(),
  position: z.number(),
  kind: z.string(),
  text: z.string(),
  evidence: z.string().nullish(),
  durability: z.string(),
  importance: z.number(),
  pinned: z.number(),
  retired_at: z.string().nullish(),
  superseded_by: z.number().nullish(),
  excluded_reason: z.string().nullish(),
  status: z.string(),
  provenance: z.string(),
  source_message_id: z.number().nullish(),
  /** WHOSE words the quote came from: `user`, `assistant`, or null for a note
   *  somebody typed themselves.
   *
   *  It is the one thing no verifier can supply. Every groundedness checker
   *  asks whether a claim is supported by its source, which the verbatim
   *  check already answers; none can ask whether the SOURCE was invented, and
   *  when the chat model quotes its own reply that check passes by
   *  construction. Marked rather than acted on: at the measured fabrication
   *  rate a review queue would be almost entirely correct notes. */
  evidence_role: z.string().nullish(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type NotebookEntry = z.infer<typeof NotebookEntrySchema>;

/** `notebook_chars` is measured by the backend and carried, not recomputed
 *  here. The same shape as `/tts/voice-mode`'s `prompt_chars`, and for the
 *  same reason: the voice block is the one part of the fixed cost the
 *  estimator does not rebuild, and the one part that cannot drift. The
 *  character header drifted once because two languages built the same string.
 *  `.default(0)` so an older backend still parses. */
export const NotebookListSchema = z.object({
  entries: z.array(NotebookEntrySchema),
  notebook_chars: z.number().default(0),
});

export const BoundarySchema = z.object({
  id: z.number(),
  scope: z.string(),
  chat_id: z.number().nullish(),
  label: z.string(),
  phrasing: z.string(),
  severity: z.string(),
  polarity: z.string(),
  on_violation: z.string(),
  source: z.string(),
  rating_ceiling: z.string().nullish(),
  exempt_from_trim: z.number(),
  last_confirmed_at: z.string().nullish(),
  active: z.number(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type Boundary = z.infer<typeof BoundarySchema>;

export const BoundaryListSchema = z.object({
  boundaries: z.array(BoundarySchema),
  /** Whether this chat follows the global set. Carried with the rows because
   *  the switch has to render in the position it is actually in - a local
   *  default reads "on" after every remount, which is the "you believe it is
   *  in force and it is not" failure this whole panel warns about.
   *  `.default(true)` so the global list route (which has no chat) parses. */
  use_global: z.boolean().default(true),
});

export const NotebookOkSchema = z.object({ ok: z.boolean() });

export const UseGlobalSchema = z.object({
  ok: z.boolean(),
  use_global: z.boolean(),
});

/** A model a background extraction may use. The list is already filtered to
 *  endpoints that keep no data AND honour a strict JSON schema, so nothing
 *  here needs to be re-checked on screen - a model that cannot do the job has
 *  no business being pickable and then failing at request time.
 *
 *  `endpoints` is how many independent providers can serve it. With provider
 *  fallback disabled a single-endpoint model is pinned to one machine, and
 *  when that machine is down extraction simply stops. */
export const ExtractionModelSchema = z.object({
  id: z.string(),
  provider: z.string().nullish(),
  prompt_price: z.number(),
  context_length: z.number().nullish(),
  endpoints: z.number(),
});

export const ExtractionModelListSchema = z.object({
  models: z.array(ExtractionModelSchema),
});

export const ExtractSettingsSchema = z.object({
  /** null means extraction never runs. There is no default on purpose. */
  model_id: z.string().nullable(),
  prompt_language: z.string().default("en"),
});

/** What one dry run produced, beside the text it read.
 *
 *  `source` and `raw` travel together because a dry run whose output cannot be
 *  compared against its input is a number, not evidence - and the whole point
 *  of this screen is looking at the six ways a small model mishandles a
 *  non-English transcript. */
export const DryRunSchema = z.object({
  model_id: z.string(),
  prompt_language: z.string(),
  source: z.string(),
  raw: z.string().nullish(),
  proposals: z.array(z.object({
    text: z.string(),
    evidence: z.string(),
    kind: z.string(),
    durability: z.string(),
    importance: z.number(),
    supersedes: z.number().nullish(),
  })),
  /** Returned by the model MINUS what survived the code filter. The gap is the
   *  interesting number: it is where ungrounded quotes and off-enum answers
   *  land, and it is invisible in the proposals alone. */
  dropped: z.number().default(0),
  /** The same total, broken out by REASON. One integer cannot tell "a quote
   *  was invented" - the defence working - from "a Turkish quote failed a byte
   *  comparison" - the defence eating a true fact, which is what an unfolded
   *  NFD diacritic or a curly apostrophe used to do silently. */
  dropped_by_reason: z.record(z.string(), z.number()).default({}),
  failure: z.string().nullish(),
  usage: z.object({
    tokens_in: z.number().nullish(),
    tokens_out: z.number().nullish(),
    cost: z.number().nullish(),
    request_id: z.string().nullish(),
    finish_reason: z.string().nullish(),
  }),
});

export type DryRunResult = z.infer<typeof DryRunSchema>;

/** What the background extractor has done, and whether it may keep going.
 *
 *  A47: a refused extraction is not silent. Without these counters, "the
 *  notebook has proposed nothing this week" and "the notebook refused sixty
 *  times for a reason nobody can see" are the same screen. */
export const WorkerStatusSchema = z.object({
  stats: z.object({
    done: z.number(),
    failed: z.number(),
    skipped: z.number(),
    /** Calls that were made and never settled - the app killed with its
     *  window, or the vault locked mid-request. Counted inside `failed` as
     *  well, so the two must not be added together. Defaulted so an older
     *  server that does not send it renders as zero rather than crashing. */
    abandoned: z.number().default(0),
    skip_reasons: z.record(z.string(), z.number()).default({}),
  }),
  spend: z.object({
    calls: z.number(),
    tokens_in: z.number(),
    tokens_out: z.number(),
    cost: z.number(),
  }),
  /** The same shape, summed over every day this vault has ever run rather
   *  than just today. `spend` is what governs the daily cap; this is not -
   *  it never gates anything, it only tells the reader how much has gone out
   *  the door in total. `.default(...)` so a backend that predates this field
   *  still parses instead of the whole worker card vanishing - z.object
   *  strips unknown keys silently and would do the same to a missing one. */
  spend_lifetime: z.object({
    calls: z.number(),
    tokens_in: z.number(),
    tokens_out: z.number(),
    cost: z.number(),
  }).default({ calls: 0, tokens_in: 0, tokens_out: 0, cost: 0 }),
  worker: z.object({
    /** closed = running · open = cooling down · stopped = waiting for you */
    state: z.string(),
    failures: z.number(),
    total_failures: z.number(),
    queued: z.number(),
    dropped_offers: z.number(),
    runs: z.number(),
    batch_size: z.number(),
    /** Declared here or DISCARDED here. These five are the whole reason the
     *  status screen exists - `alive` and `died` are the "the background task
     *  crashed" signal - and zod's z.object STRIPS unknown keys, so the
     *  backend computed them on every request and the client threw them away.
     *  A dead worker went on reporting "Running." with a growing queue, which
     *  is the exact failure the fields were added to prevent. */
    refused_by_breaker: z.number().default(0),
    alive: z.boolean().default(true),
    died: z.string().nullish(),
    unhandled: z.number().default(0),
    last_error: z.string().nullish(),
  }),
  daily_cap: z.number(),
});

export type WorkerStatus = z.infer<typeof WorkerStatusSchema>;

export const AutoAcceptSchema = z.object({ enabled: z.boolean() });

/** The phrase that stops a turn in CODE rather than by asking the model.
 *  Empty means off. */
export const SafewordSchema = z.object({ word: z.string() });
