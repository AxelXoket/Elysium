/**
 * The TypeScript half of the shared macro contract.
 *
 * `shared/macro_vectors.json` is asserted by BOTH suites - this file and
 * `backend/tests/test_card_macros.py`. Every vector names an exact output or
 * the exact refusal, so a disagreement between the two engines turns one of
 * the two suites red. Nothing here retypes a vector or a constant.
 */
import { describe, it, expect } from "vitest";

import doc from "../../../../shared/macro_vectors.json";
import {
  DEFAULT_USER_NAME,
  FIELD_MAX_CODE_POINTS,
  FIELD_TOO_LARGE_CODE,
  MacroOutputTooLargeError,
  renderMacros,
  splitExamples,
  type MacroContext,
} from "@/lib/prompt/cardMacros";

type Part = string | { repeat: string; times: number };
type Field = string | Part[];

interface RenderVector {
  id: string;
  note: string;
  text: Field;
  char?: Field;
  user?: Field;
  expect?: Field;
  error?: string;
}

interface SplitVector {
  id: string;
  note: string;
  text: Field;
  user?: Field;
  char?: Field;
  expect: string[];
}

/** Same decoder as the Python test: a string, or parts joined in order. */
function decode(value: Field): string {
  if (typeof value === "string") return value;
  return value.map((p) => (typeof p === "string" ? p : p.repeat.repeat(p.times))).join("");
}

function ctxOf(v: { char?: Field; user?: Field }): MacroContext {
  return {
    char: v.char === undefined ? doc.default_ctx.char : decode(v.char),
    user: v.user === undefined ? doc.default_ctx.user : decode(v.user),
  };
}

const RENDER = doc.render as RenderVector[];
const SPLIT = doc.split as SplitVector[];
const PIPELINE = doc.pipeline as SplitVector[];

describe("the vector file", () => {
  it("has ground, positive and refusal cases (a do-nothing engine cannot pass)", () => {
    expect(RENDER.length).toBeGreaterThanOrEqual(25);
    const unchanged = RENDER.filter((v) => v.expect !== undefined && decode(v.expect) === decode(v.text));
    const changed = RENDER.filter((v) => v.expect !== undefined && decode(v.expect) !== decode(v.text));
    const refused = RENDER.filter((v) => v.error !== undefined);
    expect(unchanged.length).toBeGreaterThan(0);
    expect(changed.length).toBeGreaterThan(0);
    expect(refused.length).toBeGreaterThan(0);
  });

  it("carries the constants this module exports", () => {
    expect(FIELD_MAX_CODE_POINTS).toBe(doc.constants.field_max_code_points);
    expect(DEFAULT_USER_NAME).toBe(doc.constants.default_user_name);
    expect(FIELD_TOO_LARGE_CODE).toBe(doc.constants.field_too_large_code);
    expect(new MacroOutputTooLargeError().code).toBe(doc.constants.field_too_large_code);
  });
});

describe("renderMacros", () => {
  for (const v of RENDER) {
    it(`${v.id}: ${v.note}`, () => {
      const text = decode(v.text);
      const ctx = ctxOf(v);
      if (v.error !== undefined) {
        let caught: unknown = null;
        try {
          renderMacros(text, ctx);
        } catch (e) {
          caught = e;
        }
        expect(caught).toBeInstanceOf(MacroOutputTooLargeError);
        expect((caught as MacroOutputTooLargeError).code).toBe(v.error);
        expect((caught as Error).message).toBe(v.error);
      } else {
        // Compared as a boolean first: a failing 1 MiB diff is unreadable.
        const out = renderMacros(text, ctx);
        const want = decode(v.expect as Field);
        expect(out === want).toBe(true);
        expect(Array.from(out).length).toBe(Array.from(want).length);
      }
    });
  }

  /** How much slower the full count of a one-part repeated vector renders
   *  than a quarter of it. Linear work gives about 4, quadratic about 16, so
   *  the gate sits at 8: a factor of two of room on either side.
   *
   *  The gate asserts a RATIO, never milliseconds, and the ratio is built to
   *  survive a busy machine (the full suite runs files in parallel):
   *  - each round times the quarter and the full text back to back, so load
   *    that comes and goes hits both halves of one round alike;
   *  - each side of a round is a best of three, which drops a single GC pause
   *    or scheduler stall;
   *  - the answer is the MEDIAN of the rounds, so a few spoiled rounds do not
   *    move it. One warm-up round runs first and is not counted. */
  const GROWTH_FACTOR = 4;
  const LINEAR_CEILING = 8;
  const ROUNDS = 7;
  function growth(id: string): { ratio: number; out: string; v: RenderVector } {
    const v = RENDER.find((x) => x.id === id) as RenderVector;
    const [part] = v.text as Part[];
    if (typeof part === "string") throw new Error(`${id} is not a repeat vector`);
    const ctx = ctxOf(v);
    const time = (t: string) => {
      let best = Infinity;
      for (let k = 0; k < 3; k++) {
        const t0 = performance.now();
        renderMacros(t, ctx);
        best = Math.min(best, performance.now() - t0);
      }
      return best;
    };
    const small = part.repeat.repeat(Math.floor(part.times / GROWTH_FACTOR));
    const full = part.repeat.repeat(part.times);
    time(small);
    time(full);
    const ratios: number[] = [];
    for (let r = 0; r < ROUNDS; r++) {
      const s = time(small);
      ratios.push(time(full) / Math.max(s, 1e-3));
    }
    ratios.sort((a, b) => a - b);
    const ratio = ratios[Math.floor(ROUNDS / 2)];
    return { ratio, out: renderMacros(full, ctx), v };
  }

  it("grows linearly with the macro count (MV18 shape, 4x the input under 8x the time)", () => {
    const { ratio, out, v } = growth("MV18");
    expect(out === decode(v.expect as Field)).toBe(true);
    expect(ratio).toBeLessThan(LINEAR_CEILING);
  });

  it("stays linear on many unclosed comments (MV34 shape, 4x the input under 8x the time)", () => {
    const { ratio, out, v } = growth("MV34");
    expect(out === decode(v.expect as Field)).toBe(true);
    expect(ratio).toBeLessThan(LINEAR_CEILING);
  });
});

describe("splitExamples", () => {
  for (const v of SPLIT) {
    it(`${v.id}: ${v.note}`, () => {
      expect(splitExamples(decode(v.text))).toEqual(v.expect);
    });
  }

  for (const v of PIPELINE) {
    it(`${v.id}: ${v.note}`, () => {
      const ctx = ctxOf(v);
      expect(splitExamples(decode(v.text)).map((b) => renderMacros(b, ctx))).toEqual(v.expect);
      // Positive control: the reverse order gives a different answer.
      expect(splitExamples(renderMacros(decode(v.text), ctx))).not.toEqual(v.expect);
    });
  }
});
