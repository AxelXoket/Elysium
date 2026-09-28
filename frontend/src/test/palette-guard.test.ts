/**
 * palette-guard.test.ts - stylesheet-level design invariants, enforced
 * instead of remembered: the Azure palette, and the motion promise.
 *
 * This theme retired green and amber on purpose: the surfaces are blue and
 * neutral, and the ONE warm accent that survived is the muted rose of
 * --color-es-danger, used for destructive and cannot-run states.
 *
 * That decision lived only in a designer's head and in review comments - so it
 * decayed exactly the way undocumented decisions do: the voice settings page
 * shipped with a brown issue block and an amber warning line, quietly bringing
 * back the two hues the theme had killed. Nothing failed, nothing warned; the
 * page just stopped looking like the app.
 *
 * So the rule is checked mechanically now. Every warm or green colour literal
 * in the stylesheet must be on the allowlist below, WITH a reason. A new one
 * fails this test and forces the same conversation the theme decision had.
 */
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

import type { Declaration, Root, Rule } from "postcss";
import { beforeAll, describe, expect, it } from "vitest";

import {
  LITERAL_CEILING,
  TOKENISED_FAMILIES,
  TOKENISED_FLOOR,
} from "@/lib/theme/paletteBudget";
import { inAtRule, parseSheet, resolvedSelectors } from "./helpers/compiledSheet";
import { loadGlassRightCss } from "./helpers/glassSurfaceCss";

const CSS = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "index.css",
);

/**
 * The colour debt count, and the method IS the rule (KURAL 2.5).
 *
 * Written out here rather than imported so that the rules and the number are
 * read together. Step by step, and each step is a decision somebody could
 * otherwise walk around:
 *
 *  - The sheet is PARSED, not scanned. The counter this replaced blanked
 *    comments with a regex first, because a raw grep counts the prose ABOUT
 *    colours as colours and reported 547 where the answer is 523. A parser
 *    has no such problem: a comment is a node of its own and `walkDecls`
 *    never visits one. The blanking step is gone, and the rule it stood for
 *    is asserted instead, in the positive control below.
 *  - The forms counted are `#hex` (3 to 8 digits), `rgb(`, `rgba(`, `hsl(`.
 *  - Named colours are NOT counted, and neither are `transparent` and
 *    `currentColor`: those are the absence of a colour, not a choice of one.
 *    A ceiling that does not say so can be walked around by writing
 *    `transparent` until the number fits.
 *  - A property starting `--` is a TOKEN DEFINITION; anything else is a
 *    DIRECT declaration. That distinction is the whole point: moving a
 *    colour from the second bucket to the first is the work.
 *  - One declaration may carry several colours, and each is counted, because
 *    a two-stop gradient is two decisions.
 *  - Tailwind's OWN generated rules are skipped, by `generatedByTailwind`
 *    below. lib/theme/paletteBudget.ts carries the reason and the
 *    measurement that shows the remainder is the same 262 on the source file
 *    and on the compiled sheet.
 */
function countColourLiterals(root: Root): { direct: number; tokenised: number } {
  const forms = [/#[0-9a-fA-F]{3,8}\b/g, /\brgb\(/g, /\brgba\(/g, /\bhsl\(/g];
  let direct = 0;
  let tokenised = 0;
  root.walkDecls((decl) => {
    if (generatedByTailwind(decl)) return;
    const n = forms.reduce((sum, f) => sum + (decl.value.match(f) ?? []).length, 0);
    if (n === 0) return;
    if (decl.prop.startsWith("--")) tokenised += n;
    else direct += n;
  });
  return { direct, tokenised };
}

/**
 * A declaration Tailwind emitted for itself, rather than one this app wrote.
 *
 * A LOCATION rule and not a value rule, which is the point: `@property
 * --tw-shadow`, `@layer utilities` and `@layer properties` are the library's
 * own output, and a ceiling that counted them would move the day a component
 * started using a utility. Everything else in the compiled sheet came from
 * index.css - the top level, `@theme inline` and the media blocks alike - so
 * a literal written inside `@media (prefers-reduced-transparency: reduce)`
 * counts exactly as one written at the top level.
 */
function generatedByTailwind(decl: Declaration): boolean {
  return inAtRule(decl, (at) =>
    at.name === "property" ||
    (at.name === "layer" && /^(utilities|properties)$/.test(at.params.trim())));
}

/**
 * Every direct colour literal inside a rule whose selector names one of the
 * emptied families, as `selector -> value`.
 *
 * Scoping is a tree fact, so this half is PARSED rather than scanned: a rule
 * is found by walking declarations up to their parent, never by counting
 * braces or slicing between the selector and the next `}`.
 */
function familyLiterals(root: Root): string[] {
  const out: string[] = [];
  const forms = /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsl\(/;
  root.walkDecls((node) => {
    if (node.prop.startsWith("--")) return;
    const parent = node.parent;
    if (!parent || parent.type !== "rule") return;
    // RESOLVED, not the raw parent selector. Tailwind keeps CSS nesting in
    // its dev output and index.css writes `&`-nested blocks of its own, so a
    // literal inside `.settings-x { &:hover { ... } }` has `&:hover` as its
    // immediate selector and slips past a substring test on the family name.
    // Measured: flat and media-wrapped literals were reported, an
    // ampersand-nested one was not.
    const selectors = resolvedSelectors(parent as Rule);
    if (!selectors.some((s) => TOKENISED_FAMILIES.some((f) => s.includes(f)))) return;
    if (!forms.test(node.value)) return;
    out.push(`${selectors.join(", ")} { ${node.prop}: ${node.value} }`);
  });
  return out;
}

/** Warm/green literals that are DELIBERATE, each with the reason it survives. */
const ALLOWED: Record<string, string> = {
  // The single surviving warm accent: destructive + cannot-run states.
  "195,106,114": "--color-es-danger #C36A72, as rgba (voice blockers)",
  "206,146,154": "danger text on dark (vault warning, voice blockers)",
  "214,120,130": "error toast accent",
  "216,124,134": "destructive menu item",
  "206,112,122": "destructive message action (delete), on the light canvas",
};

/** Hex literals that are deliberate. */
const ALLOWED_HEX = new Set([
  "#c36a72", // --color-es-danger, the one warm token
  "#8d3e49", // deepened danger for the persona destructive action, and for
             // danger words on the right panel with the mist on
  "#7c3740", // that danger a step darker, for the Security tab's bare danger
             // actions on the ghost hover fill, where #8d3e49 read 4.24:1
  "#af4650", // --color-es-danger, redefined inside .glass-right so danger
             // text clears 4.5:1 on the app's one light surface
]);

function parseColors(css: string): { line: number; raw: string; rgb: number[] }[] {
  const out: { line: number; raw: string; rgb: number[] }[] = [];
  css.split("\n").forEach((text, i) => {
    // Skip comment-only lines: prose about colours is not paint.
    if (text.trim().startsWith("*") || text.trim().startsWith("/*")) return;

    for (const m of text.matchAll(/rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)/g)) {
      out.push({
        line: i + 1,
        raw: m[0],
        rgb: [Number(m[1]), Number(m[2]), Number(m[3])],
      });
    }
    for (const m of text.matchAll(/#([0-9a-fA-F]{6})\b/g)) {
      const hex = m[1];
      out.push({
        line: i + 1,
        raw: `#${hex}`,
        rgb: [
          parseInt(hex.slice(0, 2), 16),
          parseInt(hex.slice(2, 4), 16),
          parseInt(hex.slice(4, 6), 16),
        ],
      });
    }
  });
  return out;
}

/** Red clearly dominant: reds, oranges, browns. */
function isWarm([r, g, b]: number[]): boolean {
  return r > g + 24 && r > b + 24;
}

/** Blue clearly weakest with red+green high: golds, ambers, yellows. */
function isAmber([r, g, b]: number[]): boolean {
  return r > b + 40 && g > b + 30 && r > 120 && g > 100;
}

/** Green clearly dominant. */
function isGreen([r, g, b]: number[]): boolean {
  return g > r + 24 && g > b + 24;
}

/**
 * Every .tsx under src/components, so the INLINE layer is covered too.
 *
 * The guard read index.css and nothing else (audit KÖK 17), and this app
 * paints heavily through `style={{ ... }}`: a green or amber literal added
 * in a component passed the whole suite. There were no violations there
 * when the audit looked - which is exactly when a guard is worth adding,
 * rather than after somebody has to be told to remove one.
 */
/** Prose ABOUT a colour is not paint: InkPicker's own docstring cites a
 *  hex the user picked in order to explain a bug. Same rule the
 *  stylesheet parser already applies to its comment lines. */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
}

function componentFiles(): string[] {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), "..", "components");
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith(".tsx")) out.push(full);
    }
  };
  walk(root);
  return out;
}

describe("Azure palette guard", () => {
  const colors = parseColors(readFileSync(CSS, "utf-8"));

  // KEPT in KADEME 20b, against section 4's list. Its reason was that this
  // apparatus disappears once the rules move to stylelint. Measured: the
  // repo contains no stylelint config, no stylelint dependency, no mention
  // of it anywhere. The condition is unmet, and until it is met this is
  // the floor that stops the amber/green and warm-colour tests below from
  // filtering an empty array and passing having checked nothing.
  it("the stylesheet is actually being read", () => {
    // Guards the guard: a path typo would make every assertion below vacuous.
    expect(colors.length).toBeGreaterThan(50);
  });

  it("carries no amber, gold or green - those hues were retired", () => {
    const offenders = colors
      .filter((c) => isAmber(c.rgb) || isGreen(c.rgb))
      .filter((c) => !ALLOWED_HEX.has(c.raw.toLowerCase()))
      .filter((c) => !(c.rgb.join(",") in ALLOWED))
      .map((c) => `index.css:${c.line} ${c.raw}`);
    expect(offenders, "retired hues are back").toEqual([]);
  });

  it("uses no warm colour outside the one sanctioned danger family", () => {
    const offenders = colors
      .filter((c) => isWarm(c.rgb))
      .filter((c) => !ALLOWED_HEX.has(c.raw.toLowerCase()))
      .filter((c) => !(c.rgb.join(",") in ALLOWED))
      .map((c) => `index.css:${c.line} ${c.raw}`);
    expect(
      offenders,
      "a new warm colour appeared - if it is deliberate, add it to ALLOWED with its reason",
    ).toEqual([]);
  });

  it("components paint from the same palette as the stylesheet", () => {
    const offenders: string[] = [];
    for (const file of componentFiles()) {
      // Comments stripped first: prose ABOUT a colour is not paint, and
      // InkPicker's own docstring cites a hex the user picked to explain a
      // bug. Same rule the stylesheet parser already applies to /* */ lines.
      const body = stripComments(readFileSync(file, "utf-8"));
      for (const c of parseColors(body)) {
        if (!isWarm(c.rgb) && !isAmber(c.rgb) && !isGreen(c.rgb)) continue;
        if (ALLOWED_HEX.has(c.raw.toLowerCase())) continue;
        if (c.rgb.join(",") in ALLOWED) continue;
        offenders.push(`${file.split(/[\\/]/).pop()}:${c.line} ${c.raw}`);
      }
    }
    expect(
      offenders,
      "a retired hue was painted inline - the stylesheet is not the only surface",
    ).toEqual([]);
  });

  // KEPT in KADEME 20b for the same reason as the stylesheet floor above:
  // the stylelint replacement does not exist yet. Point `root` at an empty
  // directory and the component-paint test below iterates nothing.
  it("the component walk actually finds files", () => {
    // Guards the guard, same as the stylesheet check above.
    expect(componentFiles().length).toBeGreaterThan(20);
  });

  it("radii come from the scale, not from a number somebody picked", () => {
    // --radius-input/card/panel are declared once, under the comment "One
    // place to tune the whole app's sharpness". The V9 voice work went round
    // it with flat 6px, 7px, 8px and a 0.75rem, so surfaces of the same rank
    // stopped sharing a shape (KÖK 17, breaking D4 and D6).
    const css = readFileSync(CSS, "utf-8");
    // ON the scale: the three declared steps, whether spelled as the token
    // or written out. Writing 4px where --radius-card would do is a style
    // preference; writing 7px is a different SHAPE, and that is the whole
    // finding. Plus the values that are not sizes at all - 0 means no
    // corner, a pill is a shape, inherit defers to the parent.
    const ON_SCALE = /^(3px|4px|5px|0|50%|9999px|999px|100%|inherit)$/;
    // Two that predate the scale and are deliberately their own shape.
    const GRANDFATHERED = new Set([".sidebar-brand", ".error-fallback-button"]);
    const offenders: string[] = [];
    let selector = "";
    css.split("\n").forEach((line, i) => {
      const rule = /^\s*(\.[\w.-]+)[^{]*\{/.exec(line);
      if (rule) selector = rule[1];
      const radius = /border-radius:\s*([^;]+);/.exec(line);
      if (!radius) return;
      const value = radius[1].trim();
      if (value.includes("var(--radius")) return;
      if (value.split(/\s+/).every((v) => ON_SCALE.test(v))) return;
      if (GRANDFATHERED.has(selector)) return;
      offenders.push(`index.css:${i + 1} ${selector} -> ${value}`);
    });
    expect(
      offenders,
      "a radius was written out by hand instead of taken from the scale",
    ).toEqual([]);
  });

  it("gives the voice warning a left border, not colour alone", () => {
    // Colour-blind readers, and anyone glancing: the warning line is
    // distinguished by a rule and quieter type, not by being orange.
    const css = readFileSync(CSS, "utf-8");
    const block = css.slice(
      css.indexOf(".settings-voice-warning"),
      css.indexOf(".settings-voice-warning") + 260,
    );
    expect(block).toMatch(/border-left/);
  });
});

/**
 * T-52 and T-83, the colour debt, over the sheet the browser is handed.
 *
 * These five read the COMPILED stylesheet and parse it, where the rest of
 * this file still greps index.css for hues and radii. The reason is not
 * taste. The count used to come out of a helper that read index.css with
 * readFileSync, and the behavioural register in testsAreBehavioural could not
 * see that read at all - its detector short-circuits on a file with no
 * `expect()` in it, so putting a read in a helper is a way round a live guard
 * whether or not anybody meant it as one. The measurement never needed the
 * source file: lib/theme/paletteBudget.ts records both counts taken with one
 * counter, and the direct half is the same 268 either way.
 */
describe("the colour debt (T-52, T-83)", () => {
  let sheet: Root;

  beforeAll(async () => {
    sheet = parseSheet(await loadGlassRightCss());
  }, 60_000);

  it("T-52: the direct colour literals stay under the ceiling", () => {
    const { direct, tokenised } = countColourLiterals(sheet);
    // GROUND: the counter is reading a stylesheet and not an empty tree.
    // Without it a broken load reports zero literals and a perfect score.
    expect(direct + tokenised, "the counter read no colours at all")
      .toBeGreaterThan(100);
    expect(
      direct,
      "a colour was written out instead of named - put it in a token or lower the ceiling",
    ).toBeLessThanOrEqual(LITERAL_CEILING);
    // The other half of the ratchet. The ceiling alone cannot tell a colour
    // that moved into a token from a colour that was deleted, and both lower
    // it; this says the tokens are still carrying what they took on.
    expect(tokenised, "literals left the token definitions rather than the rules")
      .toBeGreaterThanOrEqual(TOKENISED_FLOOR);
  });

  it("emits no utility for the two dead card tokens", () => {
    /* The colour side of the guard fontScale.test.ts has for type.
     *
     * Tailwind scans every file under src as raw text, so a utility name
     * written in a COMMENT is a rule in the stylesheet users download. On
     * the type side that is caught: a retired step spelled anywhere turns
     * the scale assertions red. On the colour side nothing looked, because a
     * rule written `background-color: var(--card)` carries no literal and
     * the census counts literals. Measured while this was being written:
     * putting one of these two names back into a comment in
     * lib/theme/paletteBudget.ts added the rule to the compiled sheet and
     * every colour and token test stayed green.
     *
     * The two names are the ones paletteBudget.ts records as dead - their
     * only consumer was the card component this work deletes - and they are
     * BUILT here rather than written, or this test would emit the very rules
     * it asks about.
     */
    const css = sheet.toString();
    for (const name of ["bg-" + "card", "text-" + "card-foreground"]) {
      expect(css, `the dead .${name} utility is back in the sheet`)
        .not.toMatch(new RegExp(`\\.${name}\\s*\\{`));
    }
    // POSITIVE CONTROL: the same query finds a utility the sheet really
    // carries, so "absent" above is a reading and not a regex that matches
    // nothing at all.
    const live = "bg-" + "popover";
    expect(css, `the query found no .${live} either, so it is reading nothing`)
      .toMatch(new RegExp(`\\.${live}\\s*\\{`));
  });

  it("T-52 POSITIVE CONTROL: the floor is a floor, not a number nothing reads", () => {
    // The ceiling is pinned in both directions - `direct <= CEILING` above
    // and `direct + 1 > CEILING` below - so it cannot be raised out of the
    // way. The floor had only one of the two: set it to 0 and every
    // assertion stayed green, which disarms the half of the ratchet that
    // tells tokenising apart from deletion.
    //
    // So the same sheet is counted with its token definitions thrown away,
    // and that count must fall UNDER the floor. A floor low enough to
    // survive that is not a floor.
    const stripped = parseSheet(sheet.toString());
    stripped.walkDecls((decl) => {
      if (decl.prop.startsWith("--")) decl.remove();
    });
    const gutted = countColourLiterals(stripped);
    expect(gutted.tokenised, "the strip did not remove the token definitions")
      .toBe(0);
    expect(TOKENISED_FLOOR, "the floor would survive every token being deleted")
      .toBeGreaterThan(gutted.tokenised);
    // And the live count clears it with no more than a family's worth of
    // headroom, so the floor is measured rather than decorative.
    expect(countColourLiterals(sheet).tokenised - TOKENISED_FLOOR)
      .toBeLessThan(50);
  });

  it("GROUND: the sheet being counted is the COMPILED one", () => {
    // The substrate claim, in one place. index.css writes no `@layer`
    // at-rule of its own; the compiled sheet carries Tailwind's. If this ever
    // read the source file again, `generatedByTailwind` would be excluding
    // nothing and the ceiling would quietly be measuring something else.
    const layers = new Set<string>();
    sheet.walkAtRules("layer", (at) => {
      layers.add(at.params.trim());
    });
    expect([...layers], "this is not Tailwind's output").toContain("utilities");
    expect([...layers]).toContain("theme");
    // And the location rule really excludes something, so "skips Tailwind's
    // own" is a reading rather than a no-op.
    let generated = 0;
    sheet.walkDecls((decl) => {
      if (generatedByTailwind(decl)) generated += 1;
    });
    expect(generated, "the location rule excluded nothing at all")
      .toBeGreaterThan(50);
  });

  it("T-52 POSITIVE CONTROL: one more rgba() would break the ceiling", () => {
    // The ceiling is the measured count, so there is no headroom: the count
    // of a sheet with one colour added must exceed it. Parsed into a second
    // tree, so the one under test is untouched.
    const css = sheet.toString();
    const probe = countColourLiterals(
      parseSheet(`${css}\n.probe { color: rgba(1, 2, 3, 0.5); }`));
    expect(probe.direct).toBeGreaterThan(LITERAL_CEILING);
    // ... and `transparent` does NOT, which is the method's own rule.
    const nothing = countColourLiterals(
      parseSheet(`${css}\n.probe { color: transparent; }`));
    expect(nothing.direct).toBeLessThanOrEqual(LITERAL_CEILING);
    // ... and neither does a colour written in PROSE, which is the rule the
    // deleted blanking step stood for, asserted rather than performed.
    const prose = countColourLiterals(
      parseSheet(`${css}\n/* #ff0000 and rgba(1, 2, 3, 0.5) are not paint */`));
    expect(prose.direct, "a comment was counted as paint")
      .toBeLessThanOrEqual(LITERAL_CEILING);
    // ... and a literal inside a MEDIA block IS counted, because the location
    // rule names Tailwind's own layers rather than "the top level". Without
    // this, a reduced-transparency block would be a place to hide one.
    const hidden = countColourLiterals(parseSheet(
      `${css}\n@media (prefers-reduced-transparency: reduce) {`
      + `\n.probe { color: rgba(1, 2, 3, 0.5); } }`));
    expect(hidden.direct, "a media block is a place to hide a literal")
      .toBeGreaterThan(LITERAL_CEILING);
  });

  it("T-83: the three families that moved carry no direct literal at all", () => {
    const offenders = familyLiterals(sheet);
    expect(
      offenders,
      "a colour was written back into a family that had been emptied",
    ).toEqual([]);
  });

  it("T-83 GROUND: the family walk finds rules and would report one", () => {
    // Two halves. The walk really reaches those families (their rules exist
    // and carry declarations), and the predicate really fires on a literal.
    let reached = 0;
    sheet.walkRules((rule) => {
      if (TOKENISED_FAMILIES.some((f) => rule.selector.includes(f))) reached += 1;
    });
    expect(reached, "no rule in the three families was reached").toBeGreaterThan(50);
    // The LIST is the claim, written out. Every check below iterates the
    // constant, so a family cut out of it is simply never looked at: dropping
    // `.glass-right` left 134 rules reached, an empty offender list and every
    // probe still reporting, and a whole family walked out of the guard with
    // nothing red. These three are the families IP-05 FIX 2 emptied.
    expect([...TOKENISED_FAMILIES], "a family left the list it is named by")
      .toEqual([".glass-right", "persona-", "settings-"]);
    // EVERY family gets its own probe. A single hardcoded `.persona-probe`
    // left the list itself unguarded: cut `.glass-right` out of
    // TOKENISED_FAMILIES and the walk still reached 134 rules, the offender
    // list was still empty and this control still returned 1, so a whole
    // family could leave the guard with nothing saying so.
    for (const family of TOKENISED_FAMILIES) {
      const selector = family.startsWith(".") ? family : `.${family}probe`;
      expect(
        familyLiterals(parseSheet(`${selector} { color: rgba(1, 2, 3, 0.5); }`)),
        `${family} is in the list but the walk does not report it`,
      ).toHaveLength(1);
    }
    // And each family is really present in the sheet on its own, so the
    // aggregate floor above cannot be met by two of the three.
    for (const family of TOKENISED_FAMILIES) {
      let own = 0;
      sheet.walkRules((rule) => {
        if (resolvedSelectors(rule).some((s) => s.includes(family))) own += 1;
      });
      expect(own, `${family} has no rule in the sheet at all`).toBeGreaterThan(5);
    }
  });
});

describe("motion guard", () => {
  const css = readFileSync(CSS, "utf-8");

  /**
   * The text of ONE brace-balanced block starting at `from`.
   *
   * Both tests below used to reach for the rest of the file instead - one
   * with `.split(...).slice(1).join(...)`, one with a bare `css.slice(idx)`.
   *
   * For the install bar that was a live hole, measured:
   * `.settings-voice-progress::after` appears twice (once outside any media
   * block, once inside the reduced-motion one), and `animation: none` appears
   * in two places, so `css.slice(idx)` reached past the override it named and
   * matched a `[data-voice-loading]` rule 280 lines further down. Deleting the
   * reduced-motion override entirely left that test GREEN.
   *
   * For the spinner it was not. Re-measured after a first draft of this
   * comment said otherwise: `.animate-spin` occurs exactly ONCE in the whole
   * stylesheet and it is already inside a reduced-motion block, so deleting
   * that block turned the old test red too. The rewrite there is hardening
   * against a shape that could hide a hole, not a fix for one that did.
   *
   * Written with a depth counter rather than `indexOf("}")` because a media
   * query nests, and the first closing brace inside one belongs to a rule,
   * not to the block.
   */
  function blockAt(from: number): string {
    const open = css.indexOf("{", from);
    if (open === -1) return "";
    let depth = 0;
    for (let i = open; i < css.length; i += 1) {
      if (css[i] === "{") depth += 1;
      else if (css[i] === "}") {
        depth -= 1;
        if (depth === 0) return css.slice(from, i + 1);
      }
    }
    return css.slice(from);
  }

  it("honours prefers-reduced-motion for spinners", () => {
    // Every busy state in the app is a rotating Loader2. Eight reduced-motion
    // blocks existed and none of them covered it - so the one animation a
    // vestibular-sensitive reader meets most often was the one that ignored
    // their setting.
    const marker = "@media (prefers-reduced-motion: reduce)";
    const blocks: string[] = [];
    for (let i = css.indexOf(marker); i !== -1; i = css.indexOf(marker, i + 1)) {
      blocks.push(blockAt(i));
    }
    // GROUND: the file really does carry reduced-motion blocks, so a failure
    // below means "the spinner is not in one" and not "there are none".
    expect(blocks.length).toBeGreaterThan(0);
    expect(blocks.join("\n")).toMatch(/\.animate-spin/);
  });

  it("slows the spinner rather than freezing it", () => {
    // A frozen spinner reads as a hung app; the goal is less motion, not a
    // broken-looking screen.
    const idx = css.indexOf(".animate-spin");
    const rule = css.slice(idx, idx + 200);
    expect(rule).toMatch(/animation-duration/);
    expect(rule).not.toMatch(/animation:\s*none/);
  });

  it("the indeterminate install bar stops moving under reduced motion", () => {
    // The override that matters is the one INSIDE a reduced-motion block, so
    // that is what is looked for. Reading forward from the first mention of
    // the selector found `animation: none` in a `[data-voice-loading]` rule
    // far below and reported success while the override itself was gone.
    const marker = "@media (prefers-reduced-motion: reduce)";
    const overrides: string[] = [];
    for (let i = css.indexOf(marker); i !== -1; i = css.indexOf(marker, i + 1)) {
      const block = blockAt(i);
      if (block.includes(".settings-voice-progress::after")) overrides.push(block);
    }
    expect(
      overrides,
      "no reduced-motion block mentions the install bar's sweep",
    ).toHaveLength(1);
    expect(overrides[0]).toMatch(/animation:\s*none/);
  });

  /* The settings surface shipped for three versions with hover lifts, press
     scales, a sliding switch thumb and two rotating chevrons, and not one
     .settings-* class named in any reduced-motion block. The house style
     promises reduced-motion coverage for every animation, and the promise is
     checked against the COMPILED sheet: the set of settings rules that move
     is derived from the sheet itself rather than typed here, so a control
     added tomorrow is held to the same answer. This used to be a hand-typed
     list of eleven classes checked against the text of the reduced-motion
     blocks, which is the method that let the gap exist in the first place.
     test/lib/motionTokens.test.ts holds the whole-sheet claim; this is its
     settings slice. */
  it("answers prefers-reduced-motion for the settings surface", async () => {
    const { loadGlassRightCss } = await import("./helpers/glassSurfaceCss");
    const {
      allNamedExceptions, isNeutralised, isReopened, movingRules,
      normalizeSelector, parseSheet, reducedMotionModel, rootTokens,
    } = await import("./helpers/compiledSheet");
    const root = parseSheet(await loadGlassRightCss());
    const table = rootTokens(root);
    const settings = movingRules(root, table).filter((e) =>
      /(^|[\s>+~])\.settings-/.test(e.selector));

    // GROUND: derived, and not empty. The switch thumb and the disclosure
    // chevron (animated by a rule on the svg, which naming the button never
    // reached) are among what the sheet moves.
    expect(settings.length, "no settings rule moves at all").toBeGreaterThan(5);
    expect(settings.some((e) => e.selector === ".settings-switch-thumb")).toBe(true);
    expect(settings.some((e) => e.selector === ".settings-voice-disclosure svg")).toBe(true);

    const model = reducedMotionModel(root, table);
    const named = allNamedExceptions();
    const stillMoving = settings
      .filter((e) =>
        !isNeutralised(e, model) ||
        (isReopened(e, model) && !named.has(normalizeSelector(e.selector))))
      .map((e) => `${e.kind}: ${e.selector}`);
    expect(stillMoving, "keeps moving under reduced motion").toEqual([]);
  }, 60_000);

  it("GROUND: a settings rule the reset did not reach would be caught", async () => {
    // Without this the case above passes on a checker that answers "covered"
    // for everything. A synthetic sheet with a toothless reset is reported;
    // the same sheet with !important is not.
    const { isNeutralised, movingRules, parseSheet, reducedMotionModel, rootTokens } =
      await import("./helpers/compiledSheet");
    const check = (reset: string) => {
      const root = parseSheet(
        `.settings-probe { transition: transform 180ms; }
         @media (prefers-reduced-motion: reduce) { *, *::before, *::after { ${reset} } }`,
      );
      const table = rootTokens(root);
      const probe = movingRules(root, table).find((e) => e.selector === ".settings-probe");
      expect(probe, "the probe did not register as moving").toBeDefined();
      return isNeutralised(probe!, reducedMotionModel(root, table));
    };
    expect(check("transition-duration: 1ms;")).toBe(false);
    expect(check("transition-duration: 1ms !important;")).toBe(true);
  });
});
