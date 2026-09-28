import { z } from "zod/v4";

/** One note. The fields that matter to the screen are the ones that explain
 *  why a note is or is not reaching the model:
 *
 *  - `provenance` says who wrote it. It is set once at insert and no route can
 *    change it, so the badge on a row is a fact rather than a hint.
 *  - `retired_at` means a newer note replaced this one. It stays on screen and
 *    stops being sent - the rule is that a note never disappears.
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
  /** Present on the row and no longer meaningful. Nothing in the
   *  application has ever written it - there is not one UPDATE against the
   *  column in the tree - so it has been 1 on every row that ever existed,
   *  and the three filters that read it decided nothing. A limit is removed
   *  by deleting it, which is permanent on purpose. Optional here so the
   *  client neither requires it nor depends on it. */
  active: z.number().optional(),
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
 *  `endpoints` counts the rows on the zero-retention endpoint list that
 *  advertise structured outputs for it. A backup for the reader's request is
 *  expected to be another such row, so at 1 there is none, and when that
 *  machine is down extraction most likely stops. */
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

/** The one value of `model_id` that is not a model: "use the model I am
 *  chatting with". Stored verbatim and resolved by the backend on EVERY
 *  read to the chat's current model, so changing the chat model moves the
 *  extractor with it - a binding, not a one-time pick. The twin of
 *  `notebook_extract.CHAT_MODEL_BINDING`; both are pinned to
 *  shared/notebook_chat_model_binding.json by their own suites, because two
 *  copies of one string on two sides of a wall drift. */
export const CHAT_MODEL_BINDING = "elysium-internal/chat-model";

/** What the extractor reads before a run is started, from
 *  `GET /notebook/extract/plan/{chat_id}`. Spends nothing.
 *
 *  `cost_estimate` is null when the price is unknown, and null is what the
 *  strip renders as "unknown": a call whose price nobody knows is not a
 *  free call, and a screen that rounds unknown down to $0 says the
 *  opposite of the truth.
 *
 *  THREE NUMBERS, THREE NAMES, and no two of them are interchangeable:
 *  `daily_cap` is the day's ceiling, `live_reserve` is the slice of it held
 *  back for the conversations you are having, and `backfill_cap` is what is
 *  left for a job - the one `fits_today` is computed against. Each field
 *  below says which it is. This paragraph used to call `daily_cap` the job's
 *  ceiling, which is the bug that made two numbers share one name. */
export const BackfillPlanSchema = z.object({
  ready: z.boolean(),
  blocked_by: z.array(z.string()),
  /** The backend's own `Scope = Literal["next", "all", "range"]`, as an enum
   *  rather than as a free string. `planProse` branches on `range` to say
   *  "messages in that range" instead of "unread messages", so a value that
   *  drifted would describe a deliberate re-read as unread material. */
  scope: z.enum(["next", "all", "range"]),
  messages_total: z.number(),
  messages_unread: z.number(),
  chunks: z.number(),
  calls_planned: z.number(),
  /** The day's ceiling, the same number `GET /notebook/worker` reports. */
  daily_cap: z.number(),
  /** What a JOB may claim: the day's ceiling minus the live reader's
   *  reserve. This is the one `fits_today` is computed against and the one
   *  the strip's sentences count towards. */
  backfill_cap: z.number(),
  /** What the day's ceiling keeps back for the conversations you are
   *  having. Read by `capProse` when nothing fits: "50 of 60 used" and a
   *  refusal are contradictory on their face, and this is the number that
   *  reconciles them. */
  live_reserve: z.number(),
  calls_spent_today: z.number(),
  fits_today: z.boolean(),
  prompt_price: z.number().nullable(),
  cost_estimate: z.number().nullable(),
  model_id: z.string().nullable(),
  model_is_chat_bind: z.boolean(),
});
export type BackfillPlan = z.infer<typeof BackfillPlanSchema>;

/** `POST /notebook/extract/run/{chat_id}`. `started` false with a reason is
 *  the ordinary answer, not an error; the reason is one of the backend's
 *  refusal tokens and WorkerPanel's SWEEP_PROSE has a sentence for each. */
export const BackfillRunSchema = z.object({
  started: z.boolean(),
  job_id: z.number().optional(),
  calls_planned: z.number().optional(),
  reason: z.string().optional(),
});
export type BackfillRun = z.infer<typeof BackfillRunSchema>;

/** `POST /notebook/extract/stop/{chat_id}`. `nothing_running` is a refusal
 *  like the others. */
export const BackfillStopSchema = z.object({
  stopped: z.boolean(),
  job_id: z.number().optional(),
  calls_spent: z.number().optional(),
  reason: z.string().optional(),
});

/** `POST /notebook/extract/undo/{job_id}?chat_id=`. The two `kept_*`
 *  counts are the notes the reader made theirs since - pinned or edited -
 *  which undo leaves alone and says so. */
export const BackfillUndoSchema = z.object({
  job_id: z.number(),
  removed: z.number(),
  kept_pinned: z.number(),
  kept_edited: z.number(),
});
export type BackfillUndo = z.infer<typeof BackfillUndoSchema>;

/** One backfill job as `GET /notebook/worker` reports it: the chat's newest
 *  when a chat was asked about, the one in flight otherwise. The five
 *  states are the table's own CHECK. */
export const BackfillStatusSchema = z.object({
  job_id: z.number(),
  chat_id: z.number(),
  /** The table's own CHECK, as an enum rather than as a docstring. The
   *  strip treats `planned` as "still reading", so a backend typo read as
   *  a finished run for a job that had not started. */
  state: z.enum(["planned", "running", "stopped", "done", "failed"]),
  /** The same three words the API takes, and for the same reason as the
   *  enum above: a name outside the set is a request the backend refuses. */
  scope: z.enum(["next", "all", "range"]),
  calls_planned: z.number(),
  calls_spent: z.number(),
  notes_written: z.number(),
  messages_left: z.number(),
  /** NULLABLE, not nullish. The worker builds this block as a dict literal
   *  in which the key is always present, so `.nullish()` defends against a
   *  shape the backend cannot send while letting a real contract break
   *  through: a dropped key arrives as undefined, falls past every `case`
   *  in `outcomeProse`, and a run that stopped at today's limit is read out
   *  as a bare "Stopped" with no word about coming back tomorrow. */
  last_error: z.string().nullable(),
});
export type BackfillStatus = z.infer<typeof BackfillStatusSchema>;

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
    /** Every failure that cost money: the abandoned ones plus the writes
     *  that failed after the reply had already been sent, generated and
     *  billed. `abandoned` alone is a subset, and the panel used to draw its
     *  "nothing was lost" line from that subset - so a run that spent a call
     *  and threw the answer away was reported as costing nothing. */
    paid_and_lost: z.number().default(0),
    skip_reasons: z.record(z.string(), z.number()).default({}),
    /** WHY the failures failed, counted by class - the same breakdown
     *  `skip_reasons` gives for refusals. The column has always been
     *  written; nothing read it, so "it failed" and "your key was refused"
     *  arrived on screen as one sentence.
     *
     *  Open-ended on purpose: the backend writes an exception class name,
     *  optionally with one of this app's own reason codes after a colon, so
     *  this is a record rather than an enum and the panel needs a fallback
     *  for a name it has no words for. `.default({})` for the same reason
     *  as the two fields above - a backend that predates this key must
     *  render as "nothing to say", not as a parse failure that takes the
     *  whole card off the screen. */
    error_types: z.record(z.string(), z.number()).default({}),
  }),
  spend: z.object({
    calls: z.number(),
    tokens_in: z.number(),
    tokens_out: z.number(),
    cost: z.number(),
    /** How many of those calls the provider priced as nothing at all.
     *
     *  `cost` is NOT NULL in the database, so a call the provider did not
     *  price had to be stored as 0 - and the panel then rendered $0.00000,
     *  which says the call was free when what actually happened is that
     *  nobody knows what it cost. This is what lets the total say how much
     *  of itself is missing. `.default(0)` so an older backend still
     *  parses. */
    cost_unknown: z.number().default(0),
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
    cost_unknown: z.number().default(0),
  }).default({
    calls: 0, tokens_in: 0, tokens_out: 0, cost: 0, cost_unknown: 0,
  }),
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
    /** The total above is these two added together, and reporting only the
     *  sum let the second answer for the first: an overflow means the worker
     *  fell behind, an offer while it was down means the vault locked, and
     *  the vault locks on an idle timer many times a day. The backend has
     *  computed both since the counters were split; the client declared
     *  neither, and z.object strips what it does not declare. */
    queue_overflows: z.number().default(0),
    offers_while_down: z.number().default(0),
    refused_by_breaker: z.number().default(0),
    alive: z.boolean().default(true),
    died: z.string().nullish(),
    unhandled: z.number().default(0),
    /** Paid replies that landed nothing - a duplicate work key, an attempt
     *  reclaimed by a retry, a range cleared while the reply was out. Kept
     *  apart from `runs` because "it ran" and "it ran and wrote something"
     *  are different claims. */
    settled_empty: z.number().default(0),
    /** What the parser refused, by reason. Empty is the healthy answer. */
    dropped: z.record(z.string(), z.number()).default({}),
    /** True while a reading job someone started is in flight (the name is
     *  the one-press sweep's, whose route is gone), so the button can be
     *  disabled rather than refused. */
    sweeping: z.boolean().default(false),
    /** What nobody has read, counted once at unlock. Reported, never acted
     *  on: an automatic catch-up scan would spend the reader's own credits
     *  on a backlog they never asked anyone to read. */
    backlog: z.object({
      chats: z.number(),
      messages: z.number(),
    }).default({ chats: 0, messages: 0 }),
    last_error: z.string().nullish(),
    /** How much new material is worth a call. It shrinks with the batch
     *  under repeated failures - ONE rule for both numbers - and the two
     *  are reported together because a panel showing one without the other
     *  is where the belief that they were the same number came from. */
    trigger_threshold: z.number().default(0),
    /** Turns refused by THIS chat's own breaker rather than the global one,
     *  and how many separate chats are refusing at once. Counted apart: "the
     *  provider is down" and "one conversation is broken" are different
     *  answers and only one of them means the rest are fine. */
    refused_by_chat_breaker: z.number().default(0),
    tripped_chats: z.number().default(0),
    /** The selected chat's OWN breaker, or null when nothing is selected or
     *  the chat has never failed. Declared here or DISCARDED here: z.object
     *  strips what it was not told about, and a chat stopped on its own
     *  would show the reader a panel reading "Running." with no explanation
     *  of why nothing is happening in front of them. */
    chat: z.object({
      state: z.string(),
      failures: z.number(),
      total_failures: z.number().default(0),
      batch_size: z.number().default(0),
      trigger_threshold: z.number().default(0),
    }).nullish(),
    /** The backfill job block: REQUIRED and nullable, never optional. z.object
     *  strips a key it was not told about, so an optional block would let a
     *  backend that stopped sending it look exactly like "no job" - and the
     *  one thing the strip must not do is show a finished run for one that
     *  is still reading. Null is the honest "none"; absent is a contract
     *  break and fails the parse. */
    backfill: BackfillStatusSchema.nullable(),
  }),
  daily_cap: z.number(),
  /** The one skip reason that is about the SELECTED chat's note model
   *  (`model_not_extractable`), or null. The backend sends it only while a
   *  refusal of this chat newer than its last read names the model the chat
   *  uses now; `stats` stays install-wide, so its
   *  count of these rows is never shown as this chat's. `.nullish()` so an
   *  older backend that does not send it reads as "nothing to say". */
  chat_refusal: z.string().nullish(),
});

export type WorkerStatus = z.infer<typeof WorkerStatusSchema>;

/** `enabled` is the GLOBAL setting - what the switch writes. `effective` is
 *  what the chat in question will actually do, which is a different answer
 *  whenever the chat carries a per-chat override; `overridden` says which of
 *  the two decided it. The panel used to render `enabled` alone, so a chat
 *  opened from an imported card showed "on" while the extractor was
 *  correctly forcing review. Defaults keep an older backend parsing. */
export const AutoAcceptSchema = z.object({
  enabled: z.boolean(),
  effective: z.boolean().optional(),
  overridden: z.boolean().default(false),
});

/** The phrase that stops a turn in CODE rather than by asking the model.
 *  Empty means off. */
export const SafewordSchema = z.object({ word: z.string() });
