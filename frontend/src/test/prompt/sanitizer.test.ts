/**
 * The TypeScript sanitizer against the SAME vectors as the backend
 * (shared/sanitizer_vectors.json, expected outputs computed by the audited
 * reference). Parity is byte-for-byte: the gauge counts what the backend
 * sends, so one differing character is a wrong number on screen (ST-8).
 */
import { describe, it, expect } from "vitest";

import vectors from "../../../../shared/sanitizer_vectors.json";
import table from "../../../../shared/prompt_sanitizer.json";
import {
  DEFAULT_NAME,
  EXPANSION_MAX,
  FIELD_MAX,
  GT,
  LT,
  NAME_MAX,
  NAME_PRECUT,
  OOC_REPL,
  RULES_VERSION,
  cpLength,
  foldLinebreaks,
  markers,
  sanitizeField,
  sanitizeName,
  sanitizeText,
  shadow,
  stripSent,
  stripSpace,
  type OpCounter,
} from "../../lib/prompt/sanitizer";

interface Vec {
  in: string;
  out: string;
  markers_in: string[];
  markers_out: string[];
  golden_replacement?: boolean;
}

const V = vectors as unknown as {
  constants: Record<string, string | number>;
  rules_version: number;
  unicode_version: string;
  attack: Vec[];
  clean: Vec[];
  known_pass: Vec[];
  names: Vec[];
  gate_inputs: { label: string; unit: string }[];
  fuzz: { in: string; out: string; name_out: string }[];
};

describe("constants come from one place", () => {
  it("match the vector file and the table", () => {
    expect(RULES_VERSION).toBe(V.rules_version);
    expect((table as { rules_version: number }).rules_version).toBe(V.rules_version);
    expect((table as { unicode_version: string }).unicode_version).toBe(V.unicode_version);
    expect({
      name_max: NAME_MAX, name_precut: NAME_PRECUT, field_max: FIELD_MAX,
      ooc_replacement: OOC_REPL, lt: LT, gt: GT, default_name: DEFAULT_NAME,
      expansion_max: EXPANSION_MAX,
    }).toEqual(V.constants);
  });
});

describe("ST-7 attack vectors", () => {
  it("the vector file is the full reference set", () => {
    expect(V.attack.length).toBe(111);
    expect(V.clean.length).toBe(35);
    expect(V.names.length).toBe(14);
    expect(V.known_pass.length).toBe(7);
  });

  it.each(V.attack.map((v, i) => [i, v] as const))("attack %i matches the backend byte for byte", (_i, v) => {
    // Positive control: the detector sees a marker in every raw attack.
    expect(v.markers_in.length).toBeGreaterThan(0);
    expect(markers(v.in).length).toBeGreaterThan(0);
    const out = sanitizeField(v.in);
    expect(out).toBe(v.out);
    expect(markers(out)).toEqual([]);
    if (v.golden_replacement) expect(out).toContain(OOC_REPL);
  });
});

describe("ST-7 ground control: clean text is untouched", () => {
  it.each(V.clean.map((v, i) => [i, v] as const))("clean %i", (_i, v) => {
    expect(sanitizeField(v.in)).toBe(v.in);
    expect(v.out).toBe(v.in);
  });
});

describe("known passes stay what the reference says (recorded, not fixed)", () => {
  it.each(V.known_pass.map((v, i) => [i, v] as const))("known %i", (_i, v) => {
    expect(sanitizeField(v.in)).toBe(v.out);
  });
});

describe("ST-6 names", () => {
  it.each(V.names.map((v, i) => [i, v] as const))("name %i", (_i, v) => {
    const out = sanitizeName(v.in);
    expect(out).toBe(v.out);
    expect(cpLength(out)).toBeLessThanOrEqual(NAME_MAX);
    expect(markers(out)).toEqual([]);
    expect(/[[\]<>{}\n]/.test(out)).toBe(false);
  });

  it("ground control: a plain name is unchanged", () => {
    expect(sanitizeName("Mira")).toBe("Mira");
  });

  it("the cut counts code points, not UTF-16 units (PK-28)", () => {
    const emoji = String.fromCodePoint(0x1f600);
    expect(sanitizeName(emoji.repeat(70))).toBe(emoji.repeat(NAME_MAX));
    expect(cpLength(emoji.repeat(3))).toBe(3);
  });

  it("an empty or all-marker name falls back to the default", () => {
    expect(sanitizeName("")).toBe(DEFAULT_NAME);
    expect(sanitizeName("<[{}]>")).toBe(DEFAULT_NAME);
  });

  it("a megabyte name is cut before the work", () => {
    const out = sanitizeName("\ufdfa".repeat(1 << 20));
    expect(cpLength(out)).toBeLessThanOrEqual(NAME_MAX);
  });
});

describe("ST-9 invariants on every attack output", () => {
  it("idempotent, NFKC-stable, NFKC output passes unchanged", () => {
    for (const v of V.attack) {
      const y = sanitizeField(v.in);
      expect(sanitizeField(y)).toBe(y);
      const n = y.normalize("NFKC");
      expect(markers(n)).toEqual([]);
      expect(sanitizeField(n)).toBe(n);
    }
  });

  it("positive control: the detector finds (( in a raw run", () => {
    expect(markers("(((x)))")).toContain("((");
  });
});

describe("ST-8 fuzz parity (600 generated strings)", () => {
  it("field and name outputs match the reference", () => {
    let changed = 0;
    for (const f of V.fuzz) {
      expect(sanitizeField(f.in)).toBe(f.out);
      expect(sanitizeName(f.in)).toBe(f.name_out);
      if (f.in !== f.out) changed++;
    }
    // Ground: the fuzz set exercises the rules, it is not all no-ops.
    expect(changed).toBeGreaterThan(100);
  });
});

describe("ST-10a gate lane: operation counts, never the clock", () => {
  const KIB = 1024;
  const gen = (unit: string, n: number) => unit.repeat(Math.floor(n / unit.length) + 1).slice(0, n);

  it.each(V.gate_inputs.map((g) => [g.label, g.unit] as const))("%s", (_label, unit) => {
    const counts: number[] = [];
    for (const n of [32 * KIB, 64 * KIB]) {
      const s = gen(unit, n);
      const sh = shadow(stripSent(foldLinebreaks(s))).sh;
      expect(cpLength(sh)).toBeLessThanOrEqual(EXPANSION_MAX * n);
      const ops: OpCounter = { close: 0 };
      sanitizeText(s, ops);
      expect(ops.close).toBeLessThanOrEqual(2 * n);
      counts.push(ops.close);
    }
    const growth = counts[0] ? counts[1] / counts[0] : 0;
    expect(growth).toBeLessThanOrEqual(2.2);
  });

  it("ground control: plain prose does no close searches", () => {
    const ops: OpCounter = { close: 0 };
    sanitizeText("The quiet harbour town. ".repeat(2000), ops);
    expect(ops.close).toBe(0);
  });
});

describe("the fold comes from the table, not from this runtime's Unicode", () => {
  it("a folded lookalike matches even where String.normalize would not be asked", () => {
    // U+1D40E MATHEMATICAL BOLD CAPITAL O: in the table as "O".
    const out = sanitizeField("(\u{1D40E}\u{1D40E}\u{1D402}: x)");
    expect(out).toContain(OOC_REPL);
  });

  it("stripSpace uses Python's whitespace set", () => {
    // U+0085 and U+001F are whitespace to Python, not to String.trim.
    expect(stripSpace("\u0085\u001fa\u001f")).toBe("a");
    expect(stripSpace("\ufeffa")).toBe("\ufeffa");
  });
});
