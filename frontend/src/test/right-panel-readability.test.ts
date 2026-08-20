/**
 * The right panel's ink, measured rather than eyeballed.
 *
 * The complaint that produced this file was that the panel's headings were
 * hard to look at and that its colours had stopped matching the rest of the
 * app. Measuring it found both halves to be true at once, which is why a
 * single "make it more readable" rule cannot express the fix:
 *
 *   1. Two things were genuinely UNREADABLE. "Secrets & Security" had no
 *      colour at all, so it inherited the value `body` computed once from
 *      :root - #EAEEF3, the DARK theme's text - and painted it on the app's
 *      one light panel at about 1.08:1. The selected auto-lock preset put
 *      --color-es-text-light (near-black inside .glass-right) on the deep
 *      navy primary fill at about 1.95:1.
 *   2. Everything ELSE was too loud. The panel's whole primary tier sat at
 *      12.23 to 14.15:1 as painted, louder than the panel's own body text
 *      and well above the 5.45 to 10.24:1 the sidebar's item text occupies.
 *
 * So the contract this file pins has a FLOOR and a CEILING. A floor alone
 * would have passed the state that prompted the complaint; a ceiling alone
 * would license the invisible heading. Both, or neither is a real test.
 *
 * Everything is read through the project's own Vite + Tailwind pipeline
 * (see helpers/glassSurfaceCss.ts), so a real edit to index.css is a real
 * red test rather than a fixture that quietly stopped matching.
 */
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { beforeAll, describe, expect, it } from "vitest";

import { contrastRatio, parseHex, type Rgb } from "@/lib/appearance/contrast";
import { loadGlassRightCss } from "@/test/helpers/glassSurfaceCss";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, "..");

/** WCAG AAA for normal text. The floor the panel must never drop below. */
const AAA = 7;

/** The ceiling. Not a WCAG number - there is no upper bound in WCAG, which
 *  is exactly why this had to be decided rather than looked up.
 *
 *  It is derived from the app's own sidebar, which is the surface the owner
 *  named as "the normal theme": item text there measures between 5.5 and
 *  10.3:1 depending on where the sidebar gradient is under it. 11 is the top
 *  of that band with a little slack, so the right panel is held to the range
 *  the rest of the app already lives in. The old #1C2632 reads 14.15:1 at
 *  the top stop and is the ground control below. */
const TOO_LOUD = 11;

/** The meter's own track, read out of the component that paints it rather
 *  than copied. This file's header promises everything is parsed, and a
 *  hand-copied constant is exactly how a test keeps passing after the thing
 *  it measures has moved. */
function meterTrack(): [Rgb, Rgb] {
  const src = readFileSync(
    join(SRC, "components", "models", "ModelPanel.tsx"),
    "utf8",
  );
  const m = /backgroundColor:\s*"rgba\(([^)]+)\)"/.exec(src);
  expect(m, "the meter track's inline style moved or changed shape").not.toBeNull();
  const fill = parseRgba(m![1]);
  const alpha = alphaOf(m![1]);
  return gradientStops().map((stop) => over(fill, stop, alpha)) as [Rgb, Rgb];
}

/** The meter's normal tier, from the token ModelPanel actually names. */
function meterNormal(): Rgb {
  const ink = parseHex(rootToken("--color-es-primary-sage"));
  expect(ink, "--color-es-primary-sage is no longer a plain hex").not.toBeNull();
  return ink!;
}

/** WCAG's non-text floor. A meter fill is a graphic, not a letter. */
const NON_TEXT = 3;

let css = "";

beforeAll(async () => {
  css = await loadGlassRightCss();
}, 20000);

function parseRgba(raw: string): Rgb {
  const m = /(\d+)[,\s]+(\d+)[,\s]+(\d+)/.exec(raw);
  if (!m) throw new Error(`unparseable colour: ${raw}`);
  return { r: Number(m[1]), g: Number(m[2]), b: Number(m[3]) };
}

/** src over dst at the given alpha. */
function over(src: Rgb, dst: Rgb, alpha: number): Rgb {
  const mix = (a: number, b: number) => Math.round(a * alpha + b * (1 - alpha));
  return { r: mix(src.r, dst.r), g: mix(src.g, dst.g), b: mix(src.b, dst.b) };
}

/** The fourth channel of an "r, g, b, a" list, defaulting to opaque. */
function alphaOf(raw: string): number {
  const parts = raw.split(",").map((x) => x.trim());
  return parts.length > 3 ? Number(parts[3]) : 1;
}

/** A token as declared at :root, read from the compiled text. */
function rootToken(name: string): string {
  const m = new RegExp(name + ":\\s*([^;]+);").exec(css);
  expect(m, "the stylesheet does not declare " + name).not.toBeNull();
  return m![1].trim();
}

/** The panel paints a literal gradient, so "the background" is two colours
 *  and a token has to clear the bar against BOTH.
 *
 *  Both stops are rgba, and the alpha is load-bearing: the panel sits on the
 *  app frame, which is an opaque --color-es-background. An earlier draft
 *  dropped the alpha and measured the stops as if they were opaque, which
 *  reported the panel roughly 0.7 MORE generous than it paints. That is the
 *  direction that lets a real regression through, and it also put this file
 *  at odds with the figures in index.css's own comment. Everything is parsed
 *  rather than copied, the frame colour included. */
function gradientStops(): [Rgb, Rgb] {
  const idx = css.indexOf(".glass-right {");
  expect(idx, "the .glass-right rule moved or was renamed").toBeGreaterThan(-1);
  const block = css.slice(idx, idx + 400);
  const m =
    /background:\s*linear-gradient\([^,]+,\s*rgba\(([^)]+)\),\s*rgba\(([^)]+)\)/.exec(
      block,
    );
  expect(m, "the panel gradient did not parse - was it reformatted?").not.toBeNull();
  const frame = parseHex(rootToken("--color-es-background"));
  expect(frame, "the frame background is no longer a plain hex").not.toBeNull();
  return [
    over(parseRgba(m![1]), frame!, alphaOf(m![1])),
    over(parseRgba(m![2]), frame!, alphaOf(m![2])),
  ];
}

/** A custom property's value as declared inside the .glass-right block.
 *  Read from the compiled text because :root-scoped tokens do not reach
 *  getPropertyValue in jsdom (helpers/glassSurfaceCss.ts explains why), and
 *  the point here is what THIS surface declares. */
function glassRightToken(name: string): string {
  const idx = css.indexOf(".glass-right {");
  expect(idx, "the .glass-right rule moved or was renamed").toBeGreaterThan(-1);
  const end = css.indexOf("}", idx);
  const block = css.slice(idx, end);
  const m = new RegExp(`${name}:\\s*([^;]+);`).exec(block);
  expect(m, `.glass-right does not declare ${name}`).not.toBeNull();
  return m![1].trim();
}

describe("the right panel's primary ink", () => {
  it("compiled at all", () => {
    // Without this every measurement below could be passing on an empty
    // string that parsed into nothing.
    expect(css.length).toBeGreaterThan(1000);
  });

  it("clears AAA against both stops of the panel's own gradient", () => {
    const ink = parseHex(glassRightToken("--color-es-text-light"));
    expect(ink, "the token is no longer a plain hex").not.toBeNull();
    for (const stop of gradientStops()) {
      expect(contrastRatio(ink!, stop)).toBeGreaterThanOrEqual(AAA);
    }
  });

  it("and stops short of the near-black the complaint was about", () => {
    const ink = parseHex(glassRightToken("--color-es-text-light"))!;
    for (const stop of gradientStops()) {
      expect(contrastRatio(ink, stop)).toBeLessThan(TOO_LOUD);
    }
  });

  it("GROUND: the value it replaced would fail that ceiling", () => {
    // The discriminating half. If #1C2632 passed here, the ceiling would be
    // decoration and this file would go green on the exact state the owner
    // complained about.
    const old = parseHex("#1C2632")!;
    const [top] = gradientStops();
    expect(contrastRatio(old, top)).toBeGreaterThan(TOO_LOUD);
  });

  it("GROUND: the dark theme's own text would be invisible here", () => {
    // What "Secrets & Security" was actually painting before it was given a
    // colour: :root's --foreground, inherited as a resolved rgb value that
    // .glass-right's override can never reach.
    const darkThemeInk = parseHex("#EAEEF3")!;
    const [top] = gradientStops();
    expect(contrastRatio(darkThemeInk, top)).toBeLessThan(2);
  });
});

describe("the context meter's warning tier", () => {
  it("is visible against the track it is actually drawn on", () => {
    const warn = parseHex(glassRightToken("--color-es-meter-warning"));
    expect(warn, "the token is no longer a plain hex").not.toBeNull();
    for (const track of meterTrack()) {
      expect(contrastRatio(warn!, track)).toBeGreaterThanOrEqual(NON_TEXT);
    }
  });

  it("GROUND: the pale azure it replaced was invisible on that track", () => {
    // #B9D4F0 was chosen against the NORMAL FILL and never checked against
    // the track, so the middle tier silently did not exist and the meter
    // went straight from normal to danger.
    for (const track of meterTrack()) {
      expect(contrastRatio(parseHex("#B9D4F0")!, track)).toBeLessThan(1.5);
    }
  });

  it("stays distinguishable from the normal tier beside it", () => {
    // Three states that a person cannot tell apart are one state. Amber is
    // dead in this theme, so the escalation is by depth; this is the check
    // that the depth is actually a step.
    const warn = parseHex(glassRightToken("--color-es-meter-warning"))!;
    expect(contrastRatio(warn, meterNormal())).toBeGreaterThan(1.4);
    // Both adjacent pairs, not one. The rationale above argues for the danger
    // end exactly as much as for the normal end.
    const danger = parseHex(glassRightToken("--color-es-danger"))!;
    expect(contrastRatio(warn, danger)).toBeGreaterThan(1.4);
  });
});

describe("tokens that do not exist", () => {
  /** Every .tsx under src/components, recursively. */
  function componentSources(): { file: string; text: string }[] {
    const out: { file: string; text: string }[] = [];
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) walk(full);
        else if (entry.name.endsWith(".tsx") || entry.name.endsWith(".ts"))
          out.push({ file: full, text: readFileSync(full, "utf8") });
      }
    };
    walk(join(SRC, "components"));
    walk(join(SRC, "lib"));
    return out;
  }

  it("finds components to read", () => {
    expect(componentSources().length).toBeGreaterThan(20);
  });

  it("paints nothing from a custom property the stylesheet never declares", () => {
    // The general form, on purpose. The first draft of this test grepped for
    // one token by name, --color-es-border-subtle, and would have sailed past
    // --color-es-ink-dim sitting two files away with the identical defect.
    //
    // Both tokens that motivated it, --color-es-border-subtle and
    // --color-es-ink-dim, are fixed; this sweep is what catches the next one.
    // An undefined custom property is the worst kind of styling bug because
    // nothing is wrong with the markup: `border: 1px solid var(--nope)` is
    // invalid at computed-value time, so the WHOLE shorthand unsets and the
    // control renders with no border at all, silently. `color: var(--nope)`
    // just inherits. Neither shows up in a diff, a linter or a type check.
    const declared = new Set(
      [...css.matchAll(/(--color-es-[\w-]+)\s*:/g)].map((m) => m[1]),
    );
    expect(
      declared.size,
      "no --color-es-* declarations were parsed out of the compiled sheet",
    ).toBeGreaterThan(20);

    const offenders: string[] = [];
    for (const { file, text } of componentSources()) {
      for (const m of text.matchAll(/var\(\s*(--color-es-[\w-]+)/g)) {
        if (!declared.has(m[1])) offenders.push(`${file}: ${m[1]}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("GROUND: the sweep would catch a token that is not declared", () => {
    // Without this, an empty offenders array and a broken regex look the same.
    const declared = new Set(
      [...css.matchAll(/(--color-es-[\w-]+)\s*:/g)].map((m) => m[1]),
    );
    expect(declared.has("--color-es-text-light")).toBe(true);
    expect(declared.has("--color-es-border-subtle")).toBe(false);
    expect(declared.has("--color-es-ink-dim")).toBe(false);
  });
});
