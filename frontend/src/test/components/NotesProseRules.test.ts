/**
 * Every sentence the Notes tab's prose modules hand the screen is
 * short (at most 12 words), free of the listed jargon, and ASCII-dashed.
 *
 * Walks the modules' own exports, so a sentence added later is held too:
 * strings, the values of exported tables, and the counted builders called at
 * 1 and 2. The rule is written once here for the gate lane.
 */
import { describe, expect, it } from "vitest";

import * as noteProse from "@/components/notebook/noteProse";
import * as limitsProse from "@/components/notebook/limitsProse";
import * as reasonProse from "@/components/notebook/reasonProse";
import * as workerProse from "@/components/notebook/workerProse";
import * as backfillProse from "@/components/notebook/backfillProse";
import {
  CHAT_STATE_PROSE, DROP_PROSE, EMPTY_PROSE, FAILURE_PROSE, SKIP_PROSE,
  STATE_PROSE, SWEEP_PROSE,
} from "@/components/notebook/WorkerPanel";
import { CHAT_MODEL_PRICE_WARNING } from "@/components/notebook/ExtractionSettings";

const JARGON = /\b(id|ids|job|jobs|unit|units|stretch|extraction|breaker|backfill)\b/i;
const DASHES = /[\u2010-\u2015\u2212]/;

/** Every string reachable from a value: itself, a table's values, a
 *  builder's answers at the counts 1 and 2. */
function strings(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(strings);
  if (typeof value === "function") {
    const out: string[] = [];
    for (const args of [[1, 2, 0.5], [2, 3, 0.5]]) {
      try {
        // A builder of a whole wire body answers "undefined ..." to bare
        // numbers; those are held by their own tests with real bodies.
        out.push(...strings((value as (...a: number[]) => unknown)(...args))
          .filter((s) => !s.includes("undefined")));
      } catch { /* not a counted builder */ }
    }
    return out;
  }
  if (value && typeof value === "object") return Object.values(value).flatMap(strings);
  return [];
}

function sentences(text: string): string[] {
  return text.split(/(?<=[.!?])\s+/).filter((s) => /[A-Za-z0-9]/.test(s));
}

const SOURCES: Record<string, unknown> = {
  noteProse, limitsProse, reasonProse, workerProse,
  backfillProse: { ...backfillProse },
  CHAT_STATE_PROSE, DROP_PROSE, EMPTY_PROSE, FAILURE_PROSE, SKIP_PROSE,
  STATE_PROSE, SWEEP_PROSE, CHAT_MODEL_PRICE_WARNING,
};

describe("the Notes tab's sentences", () => {
  const all = Object.entries(SOURCES).flatMap(([from, v]) =>
    strings(v).map((s) => [from, s] as const));

  it("GROUND: the walk reaches the tables and the builders", () => {
    expect(all.length).toBeGreaterThan(80);
    expect(all.some(([, s]) => s === reasonProse.REASON.modelListUnavailable)).toBe(true);
    expect(all.some(([, s]) => s === workerProse.CENSUS.abandoned(2))).toBe(true);
  });

  it("holds every sentence to 12 words", () => {
    const long = all.flatMap(([from, s]) => sentences(s)
      .filter((x) => x.split(/\s+/).filter((w) => /[A-Za-z0-9]/.test(w)).length > 12)
      .map((x) => `${from}: ${x}`));
    expect(long).toEqual([]);
  });

  it("uses none of the listed jargon and no long dash", () => {
    expect(all.filter(([, s]) => JARGON.test(s)).map(([f, s]) => `${f}: ${s}`)).toEqual([]);
    expect(all.filter(([, s]) => DASHES.test(s)).map(([f, s]) => `${f}: ${s}`)).toEqual([]);
  });

  it("POSITIVE CONTROL: the rules catch what they are for", () => {
    const bad = "This sentence goes on and on with far more than twelve words in it today.";
    expect(sentences(bad)[0].split(/\s+/).length).toBeGreaterThan(12);
    expect(JARGON.test("the backfill job")).toBe(true);
    expect(DASHES.test("a \u2014 b")).toBe(true);
  });
});
