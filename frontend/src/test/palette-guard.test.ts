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

import { describe, expect, it } from "vitest";

const CSS = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "index.css",
);

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
  "#96424e", // deepened danger for the persona destructive action
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
     .settings-* class named in any reduced-motion block. docs/V1_1_PLAN.md
     requires the opposite of every new animation, so the promise is checked
     here rather than remembered. */
  it("answers prefers-reduced-motion for the settings surface", () => {
    const css = readFileSync(CSS, "utf8");
    const blocks = [
      ...css.matchAll(/@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{/g),
    ].map((m) => css.slice(m.index!, m.index! + 2200));
    expect(blocks.length, "no reduced-motion blocks found at all").toBeGreaterThan(0);
    const covered = blocks.join(" | ");

    // The interactive classes, not the decorative ones. Each of these carries
    // a transform in a hover or an active state somewhere in this file.
    for (const cls of [
      ".settings-category-row",
      ".settings-toggle-row",
      ".settings-back-button",
      ".settings-segment-option",
      ".settings-segment-button",
      ".settings-tint-chip",
      ".settings-inline-reset",
      ".settings-switch-thumb",
      ".settings-voice-pick",
      ".settings-voice-disclosure",
      ".settings-voice-button",
    ]) {
      expect(covered, `${cls} keeps moving under reduced motion`).toContain(cls);
    }

    // transition does not inherit, so naming the BUTTON never reaches the
    // chevron inside it. Both disclosure arrows need their own selector.
    expect(
      covered,
      "the disclosure chevrons rotate from a rule on the svg, not the button",
    ).toContain(".settings-voice-disclosure svg");
    expect(covered).toContain(".settings-param-group-toggle svg");
  });

  it("GROUND: a class nothing reduces would be caught", () => {
    // Without this the assertions above pass on any string long enough.
    const css = readFileSync(CSS, "utf8");
    const blocks = [
      ...css.matchAll(/@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{/g),
    ].map((m) => css.slice(m.index!, m.index! + 2200));
    expect(blocks.join(" | ")).not.toContain(".settings-no-such-class");
  });
});
