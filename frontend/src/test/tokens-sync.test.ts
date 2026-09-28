/**
 * tokens-sync.test.ts - lib/theme/tokens.ts is index.css, twice (T-82).
 *
 * tokens.ts is a hand-written mirror of the @theme block: names for the CSS
 * side, numbers for motion. A mirror written by hand drifts, and until this
 * file it drifted in silence. The guard runs in two modes, because the two
 * kinds of field can go wrong in two ways:
 *
 *   - NAME equality, both directions, for the `var()` fields: every
 *     `--duration-*`, `--ease-*` and `--color-es-*` token in the sheet has a
 *     field, and every field names a token the sheet defines. Plus the whole
 *     block: every OTHER token @theme declares is written down as one this
 *     file deliberately does not mirror, so a token added under a fourth
 *     prefix cannot drift in unnoticed.
 *   - VALUE equality for `durationSeconds` and `easeValues`: `180ms` is
 *     parsed and compared with 0.18, `cubic-bezier(0.4, 0, 0.2, 1)` is
 *     parsed and compared number by number with `[0.4, 0, 0.2, 1]`, and the
 *     `ease-out` keyword is compared with the bezier the CSS spec gives it.
 *
 * Both lists are read from their files: the CSS side is parsed with postcss,
 * the TS side is imported. Nothing is retyped here.
 *
 * Why the SOURCE sheet and not only the compiled one: Tailwind emits a
 * @theme token into the compiled sheet only when something references it.
 * Measured on this tree: `--font-heading` never reaches the compiled
 * `:root`, and an unreferenced probe token added to @theme did not either.
 * So a token added to the source and forgotten in tokens.ts, which is the
 * drift this file exists to catch, would be invisible in the compiled sheet
 * and visible only here. The compiled sheet is read as well, as the check
 * that the values the browser receives are the values the source declares.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import postcss from "postcss";
import { beforeAll, describe, expect, it } from "vitest";

import { durationSeconds, easeValues, tokens, typeScale } from "@/lib/theme/tokens";
import { loadGlassRightCss } from "./helpers/glassSurfaceCss";
import { parseBezier, parseSheet, rootTokens, timeMs } from "./helpers/compiledSheet";

const SOURCE_SHEET = path.resolve(__dirname, "..", "index.css");

/** The one namespace reset the @theme block carries. Not a token. */
const NAMESPACE_RESET = "--text-*";

/** The @theme block of the source sheet, parsed rather than string-matched. */
function themeTokens(): Map<string, string> {
  const root = postcss.parse(readFileSync(SOURCE_SHEET, "utf-8"));
  const out = new Map<string, string>();
  root.walkAtRules("theme", (at) => {
    at.each((node) => {
      if (node.type !== "decl") return;
      // ONE name, not a shape. `--text-*: initial` is not a token, it is
      // the instruction that clears Tailwind's stock steps out of that
      // namespace before ours are declared, and asking tokens.ts to mirror a
      // wildcard is meaningless. It is skipped BY NAME rather than by its
      // `-*` ending, because a shape amnesty would silently swallow the next
      // namespace reset somebody adds - which is the same argument this
      // file's own roster makes about prefixes. fontScale.test.ts asserts
      // the reset does its job, so this is a filter and not a blind spot.
      if (node.prop === NAMESPACE_RESET) return;
      out.set(node.prop, node.value.trim());
    });
  });
  return out;
}

/**
 * The type scale's tokens, step by step: `--text-<step>` plus the sub-tokens
 * Tailwind reads beside it (`--line-height` always; `--letter-spacing` when
 * the tracking is not zero; `--font-weight` when the step carries a weight
 * of its own). Derived from typeScale so the sheet is held to the table and
 * not to a second list typed here.
 */
function textTokenFields(): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [step, field] of Object.entries(tokens.text)) {
    const name = cssNameOf(field);
    const spec = typeScale[step as keyof typeof typeScale];
    out[step] = field;
    out[`${step}LineHeight`] = `var(${name}--line-height)`;
    if (spec.tracking !== "0") out[`${step}Tracking`] = `var(${name}--letter-spacing)`;
    if (spec.weight !== null) out[`${step}Weight`] = `var(${name}--font-weight)`;
  }
  return out;
}

const FAMILIES = [
  { family: "colors", prefix: "--color-es-", fields: tokens.colors },
  { family: "duration", prefix: "--duration-", fields: tokens.duration },
  { family: "ease", prefix: "--ease-", fields: tokens.ease },
  { family: "text", prefix: "--text-", fields: textTokenFields() },
] as const;

/** The token a `var(--name)` field names. */
function cssNameOf(field: string): string {
  const m = /^var\((--[\w-]+)\)$/.exec(field);
  if (!m) throw new Error(`not a var() field: ${field}`);
  return m[1];
}

/** Every token name tokens.ts mirrors, across all three families. */
function mirroredNames(): Set<string> {
  return new Set(
    FAMILIES.flatMap(({ fields }) => Object.values(fields).map(cssNameOf)),
  );
}

/**
 * The @theme tokens tokens.ts deliberately does NOT mirror, by exact name.
 *
 * T-82 is a claim about the WHOLE block, not about three prefixes. Checking
 * only `--color-es-`, `--duration-` and `--ease-` leaves every other token
 * unwatched: a `--radius-probe` added to @theme with nothing on the TS side
 * left this file green, which is the drift the row exists to catch, one
 * prefix over. So the block is accounted for in full now - a token is either
 * mirrored by a field or written down here - and the roster is by NAME
 * rather than by prefix, because a prefix amnesty would re-open the same
 * hole for the next `--radius-*` somebody adds.
 *
 * Two reasons cover all of it, and neither of them is "we forgot":
 *
 *   - shadcn's CLI-generated aliases and the font stacks. They belong to the
 *     shadcn setup, they are consumed through Tailwind utilities, and no JS
 *     call site reads one.
 *   - the radius, shadow and layout scales. Consumed in CSS and in Tailwind
 *     class names; nothing hands one to a JS API as a value, which is the
 *     only reason the motion tokens need a mirror at all.
 *
 * Add a field to tokens.ts, or add the name here with its reason. There is
 * deliberately no third way out.
 */
const NOT_MIRRORED_IN_TS: readonly string[] = [
  // Font stacks: CSS and Tailwind only.
  "--font-heading",
  "--font-sans",
  "--font-serif",
  // shadcn CLI-generated aliases onto the shadcn :root variables.
  "--color-sidebar",
  "--color-sidebar-ring",
  "--color-sidebar-border",
  "--color-sidebar-accent",
  "--color-sidebar-accent-foreground",
  "--color-sidebar-primary",
  "--color-sidebar-primary-foreground",
  "--color-sidebar-foreground",
  "--color-chart-1",
  "--color-chart-2",
  "--color-chart-3",
  "--color-chart-4",
  "--color-chart-5",
  "--color-ring",
  "--color-input",
  "--color-border",
  "--color-destructive",
  "--color-accent",
  "--color-accent-foreground",
  "--color-muted",
  "--color-muted-foreground",
  "--color-secondary",
  "--color-secondary-foreground",
  "--color-primary",
  "--color-primary-foreground",
  "--color-popover",
  "--color-popover-foreground",
  "--color-card",
  "--color-card-foreground",
  "--color-foreground",
  "--color-background",
  // The radius scale: one place to tune the app's sharpness, read in CSS.
  "--radius-sm",
  "--radius-md",
  "--radius-lg",
  "--radius-xl",
  "--radius-2xl",
  "--radius-3xl",
  "--radius-4xl",
  "--radius-shell",
  "--radius-panel",
  "--radius-canvas",
  "--radius-card",
  "--radius-input",
  "--radius-icon-btn",
  "--radius-badge",
  "--radius-app-frame",
  // The shadow scale: values long enough that a JS mirror would be a second
  // copy of the same string with nothing gained.
  "--shadow-card",
  "--shadow-panel",
  "--shadow-bubble",
  "--shadow-composer",
  "--shadow-app-frame",
  // Layout: the two panel widths and the frame's ceiling. The JS side reads
  // the COMPUTED width off the element, never the token.
  "--sidebar-width",
  "--right-panel-width",
  "--app-frame-max-w",
  "--app-frame-max-h",
];

/** Token names that are neither mirrored nor written down as not mirrored. */
function unaccounted(names: Iterable<string>, mirrored: Set<string>): string[] {
  return [...names]
    .filter((n) => !mirrored.has(n) && !NOT_MIRRORED_IN_TS.includes(n))
    .sort();
}

let theme: Map<string, string>;
let compiled: Map<string, string>;

beforeAll(async () => {
  theme = themeTokens();
  compiled = rootTokens(parseSheet(await loadGlassRightCss()));
}, 60_000);

describe("names, both directions", () => {
  it("GROUND: the @theme block was found and carries the token this test will lean on", () => {
    expect(theme.size, "no @theme declarations parsed").toBeGreaterThan(50);
    expect(theme.get("--duration-fast")).toBeDefined();
    expect(cssNameOf(tokens.duration.fast)).toBe("--duration-fast");
  });

  it("GROUND for the accounting: a token neither side knows is reported, one either side knows is not", () => {
    // The whole-block case below passes when its list is empty, so the
    // predicate is measured against known answers first: an invented token
    // is reported, a mirrored one is not, and one on the not-mirrored roster
    // is not either.
    const mirrored = mirroredNames();
    expect(unaccounted(["--radius-probe"], mirrored)).toEqual(["--radius-probe"]);
    expect(unaccounted(["--duration-fast"], mirrored)).toEqual([]);
    expect(unaccounted(["--radius-sm"], mirrored)).toEqual([]);
  });

  it("every token the @theme block defines is accounted for (T-82)", () => {
    const mirrored = mirroredNames();
    expect(mirrored.size, "tokens.ts mirrors nothing").toBeGreaterThan(40);
    expect(NOT_MIRRORED_IN_TS.length, "nothing is written down as unmirrored")
      .toBeGreaterThan(0);
    expect(
      unaccounted(theme.keys(), mirrored),
      "declared in @theme, neither mirrored in tokens.ts nor written down as " +
        "deliberately not mirrored",
    ).toEqual([]);
  });

  it("the not-mirrored roster is honest in both directions", () => {
    const mirrored = mirroredNames();
    expect(
      NOT_MIRRORED_IN_TS.filter((n) => !theme.has(n)),
      "written down as an unmirrored @theme token, but @theme no longer declares it",
    ).toEqual([]);
    expect(
      NOT_MIRRORED_IN_TS.filter((n) => mirrored.has(n)),
      "written down as unmirrored and mirrored by a field at the same time",
    ).toEqual([]);
    expect(
      new Set(NOT_MIRRORED_IN_TS).size,
      "the roster lists a name twice",
    ).toBe(NOT_MIRRORED_IN_TS.length);
  });

  it.each(FAMILIES)("$family: the sheet and tokens.ts name the same tokens", ({ prefix, fields }) => {
    const inCss = [...theme.keys()].filter((k) => k.startsWith(prefix)).sort();
    const inTs = Object.values(fields).map(cssNameOf).sort();
    expect(inCss.length, `no ${prefix} tokens in @theme`).toBeGreaterThan(0);
    const missingInTs = inCss.filter((n) => !inTs.includes(n));
    const missingInCss = inTs.filter((n) => !inCss.includes(n));
    expect(missingInTs, "declared in index.css, absent from tokens.ts").toEqual([]);
    expect(missingInCss, "named in tokens.ts, absent from index.css").toEqual([]);
  });
});

describe("values, number by number", () => {
  it("typeScale carries every --text-* step with its line height, tracking and weight", () => {
    const px = (v: string | undefined, what: string): number => {
      const m = /^(\d+(?:\.\d+)?)px$/.exec(v ?? "");
      expect(m, `${what} is not a px value in @theme: ${v}`).not.toBeNull();
      return Number(m![1]);
    };
    for (const [step, spec] of Object.entries(typeScale)) {
      const name = cssNameOf(tokens.text[step as keyof typeof tokens.text]);
      expect(px(theme.get(name), name)).toBe(spec.px);
      expect(px(theme.get(`${name}--line-height`), `${name}--line-height`)).toBe(spec.lineHeight);
      const tracking = theme.get(`${name}--letter-spacing`);
      if (spec.tracking === "0") expect(tracking, `${name} carries a tracking the table does not`).toBeUndefined();
      else expect(tracking, `${name} tracking`).toBe(spec.tracking);
      const weight = theme.get(`${name}--font-weight`);
      if (spec.weight === null) expect(weight, `${name} carries a weight the table does not`).toBeUndefined();
      else expect(Number(weight), `${name} weight`).toBe(spec.weight);
    }
  });

  it("durationSeconds carries every --duration-* token as seconds", () => {
    expect(Object.keys(durationSeconds).sort()).toEqual(Object.keys(tokens.duration).sort());
    for (const [key, field] of Object.entries(tokens.duration)) {
      const name = cssNameOf(field);
      const ms = timeMs(theme.get(name) ?? "");
      expect(ms, `${name} is not a time in @theme`).not.toBeNull();
      expect(
        durationSeconds[key as keyof typeof durationSeconds],
        `${name} is ${ms}ms in index.css`,
      ).toBeCloseTo(ms! / 1000, 6);
    }
  });

  it("easeValues carries every --ease-* token as the same four numbers", () => {
    expect(Object.keys(easeValues).sort()).toEqual(Object.keys(tokens.ease).sort());
    for (const [key, field] of Object.entries(tokens.ease)) {
      const name = cssNameOf(field);
      const curve = parseBezier(theme.get(name) ?? "");
      const ours = easeValues[key as keyof typeof easeValues];
      expect(ours.length).toBe(4);
      for (let i = 0; i < 4; i += 1) {
        expect(ours[i], `${name}[${i}] is ${curve[i]} in index.css`).toBeCloseTo(curve[i], 6);
      }
    }
  });
});

describe("the compiled sheet ships the source values", () => {
  it("every motion token reaches the browser with the value the source declares", () => {
    // The motion tokens are all referenced, so all of them are expected to
    // be emitted; a colour token nothing references may legitimately be
    // dropped by Tailwind and is compared only when present. The type steps
    // are the same: the block is `@theme inline`, so a step used only as a
    // utility is inlined into it and reaches :root only where index.css
    // reads it through var().
    const squash = (v: string) => v.replace(/\s+/g, "");
    for (const { prefix } of FAMILIES) {
      for (const [name, value] of theme) {
        if (!name.startsWith(prefix)) continue;
        const emitted = compiled.get(name);
        if (prefix !== "--color-es-" && prefix !== "--text-") {
          expect(emitted, `${name} did not reach the compiled sheet`).toBeDefined();
        }
        if (emitted !== undefined) {
          expect(squash(emitted), name).toBe(squash(value));
        }
      }
    }
  });
});
