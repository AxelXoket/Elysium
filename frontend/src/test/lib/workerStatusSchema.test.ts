/**
 * The worker status wire contract, at the one place a new field can break it.
 *
 * `z.object` STRIPS what it does not declare and REFUSES what it declares as
 * required and does not receive. Both halves have cost this schema something
 * already: five worker fields were computed on every request and thrown away
 * on arrival because nobody declared them, and a required field is how a
 * client that ships ahead of its backend takes the whole card off the screen
 * rather than rendering one line short.
 *
 * So a new breakdown is added with `.default({})`, and this is where that is
 * measured rather than asserted in a comment.
 */
import { describe, it, expect } from "vitest";

import { WorkerStatusSchema } from "@/lib/schemas/notebook";

/** A body from a backend that predates the field under test: every key the
 *  schema has ever required, and nothing else. */
function olderBackendBody() {
  return {
    stats: { done: 3, failed: 1, skipped: 0 },
    spend: { calls: 3, tokens_in: 900, tokens_out: 40, cost: 0.0004 },
    worker: {
      state: "closed", failures: 0, total_failures: 0,
      queued: 0, dropped_offers: 0, runs: 3, batch_size: 20,
      // The one block that is REQUIRED rather than defaulted, on purpose:
      // see "the backfill block" below.
      backfill: null,
    },
    daily_cap: 60,
  };
}

describe("the backfill block", () => {
  it("null parses as no job", () => {
    const parsed = WorkerStatusSchema.parse(olderBackendBody());
    expect(parsed.worker.backfill).toBeNull();
  });

  it("is REQUIRED: a body without it fails the parse rather than reading as no job", () => {
    // The one block that is not defaulted, on purpose. z.object strips a
    // key it was not told about, so an optional block would let a backend
    // that stopped sending it look exactly like "no job is running" - and
    // the strip would show a finished run for one that is still reading.
    const body = olderBackendBody();
    const { backfill: _dropped, ...worker } = body.worker;
    void _dropped;
    expect(() => WorkerStatusSchema.parse({ ...body, worker })).toThrow();
  });

  it("GROUND: a full block keeps every key", () => {
    const body = olderBackendBody();
    const block = {
      job_id: 17, chat_id: 4, state: "running", scope: "all",
      calls_planned: 21, calls_spent: 8, notes_written: 14,
      messages_left: 240, last_error: null,
    };
    const parsed = WorkerStatusSchema.parse({
      ...body, worker: { ...body.worker, backfill: block },
    });
    expect(parsed.worker.backfill).toEqual(block);
  });
});

describe("the worker status a backend without the failure census sends", () => {
  it("still parses", () => {
    // The direct measurement of `.default({})`. A required `error_types`
    // would throw here, and the panel renders nothing at all when the parse
    // throws - so the reader would lose the money line, the failure lines
    // and the skip reasons to gain a breakdown their backend cannot send.
    const parsed = WorkerStatusSchema.parse(olderBackendBody());
    expect(parsed.stats.done).toBe(3);
    expect(parsed.stats.failed).toBe(1);
  });

  it("reads the missing census as nothing to say, not as absent", () => {
    // `{}` rather than undefined is what lets the panel iterate it without
    // a guard at every call site, which is how `skip_reasons` is written.
    const parsed = WorkerStatusSchema.parse(olderBackendBody());
    expect(parsed.stats.error_types).toEqual({});
  });

  it("GROUND: a body that DOES carry the census keeps every key of it", () => {
    // Positive control. A schema that dropped the field on the floor would
    // satisfy both assertions above - `{}` is exactly what a stripped
    // unknown key leaves behind - and the panel would render nothing while
    // every test here stayed green.
    const body = olderBackendBody();
    const parsed = WorkerStatusSchema.parse({
      ...body,
      stats: {
        ...body.stats,
        error_types: { ReadTimeout: 2, write_RuntimeError: 1 },
      },
    });
    expect(parsed.stats.error_types)
      .toEqual({ ReadTimeout: 2, write_RuntimeError: 1 });
  });

  it("refuses a census whose counts are not numbers", () => {
    // The record's value type is load-bearing: the panel prints these
    // straight into a sentence, so a string here would render as itself.
    //
    // The failure is checked at its PATH rather than with a bare toThrow:
    // the rest of this body is valid, so a bare toThrow would also be
    // satisfied by an unrelated parse error introduced somewhere else and
    // would go on reporting this field as guarded.
    const body = olderBackendBody();
    const out = WorkerStatusSchema.safeParse({
      ...body,
      stats: { ...body.stats, error_types: { ReadTimeout: "two" } },
    });
    expect(out.success).toBe(false);
    expect(out.error!.issues.map((i) => i.path.join(".")))
      .toContain("stats.error_types.ReadTimeout");
  });

  it("GROUND: the same body with a number parses", () => {
    // Positive control for the refusal above: a schema that refused every
    // census would satisfy it and take the whole feature off the screen.
    const body = olderBackendBody();
    const out = WorkerStatusSchema.safeParse({
      ...body,
      stats: { ...body.stats, error_types: { ReadTimeout: 2 } },
    });
    expect(out.success).toBe(true);
  });
});
