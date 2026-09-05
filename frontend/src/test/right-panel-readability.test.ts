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

import {
  AAA_NORMAL,
  AA_NON_TEXT,
  AA_NORMAL,
  contrastRatio,
  parseHex,
  type Rgb,
} from "@/lib/appearance/contrast";
import {
  composite,
  glassRightStops,
  loadGlassRightCss,
  parseRgbColour,
} from "@/test/helpers/glassSurfaceCss";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, "..");

/** The ceiling. Not a WCAG number - there is no upper bound in WCAG, which
 *  is exactly why this had to be decided rather than looked up.
 *
 *  It is derived from the app's own sidebar, which is the surface that sets
 *  what the normal theme looks like: item text there measures between 5.5 and
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
  const m = /backgroundColor:\s*"(rgba\([^)]+\))"/.exec(src);
  expect(m, "the meter track's inline style moved or changed shape").not.toBeNull();
  return gradientStops().map((stop) => composite(m![1], stop)) as [Rgb, Rgb];
}

/** The meter's normal tier, from the token ModelPanel actually names, read
 *  the way the panel resolves it rather than from :root alone. The two agree
 *  today because .glass-right does not repaint the primary. Reading only
 *  :root made that agreement load-bearing: adding an override for it inside
 *  .glass-right, three lines from the warning token this file exists to
 *  measure, moved the normal tier and changed nothing here. A comparison
 *  that cannot see one of its two sides move is a constant. */
function meterNormal(): Rgb {
  const ink = parseHex(surfaceValue("--color-es-primary-sage"));
  expect(ink, "--color-es-primary-sage is no longer a plain hex").not.toBeNull();
  return ink!;
}

let css = "";

beforeAll(async () => {
  css = await loadGlassRightCss();
}, 20000);

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
 *  reported the panel roughly 0.3 to 0.55 MORE generous than it paints. That
 *  is the direction that lets a real regression through, and it also put this
 *  file at odds with the figures in index.css's own comment. Everything is
 *  parsed rather than copied, the frame colour included.
 *
 *  The model moved into helpers/glassSurfaceCss.ts on 5 September 2026 so the
 *  other three files that measure this panel cannot answer differently. */
const gradientStops = (): [Rgb, Rgb] => glassRightStops(css);

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

/** What this surface actually paints for a token: .glass-right's own
 *  declaration where it has one, the :root declaration where it does not.
 *  The same order the cascade uses, for tokens the panel is allowed to
 *  inherit rather than required to override. */
function surfaceValue(name: string): string {
  const idx = css.indexOf(".glass-right {");
  expect(idx, "the .glass-right rule moved or was renamed").toBeGreaterThan(-1);
  const block = css.slice(idx, css.indexOf("}", idx));
  const scoped = new RegExp(`${name}:\\s*([^;]+);`).exec(block);
  return scoped ? scoped[1].trim() : rootToken(name);
}

describe("the right panel's primary ink", () => {
  it("compiled at all", () => {
    // Without this every measurement below could be passing on an empty
    // string that parsed into nothing.
    expect(css.length).toBeGreaterThan(1000);
  });

  it("the harness produced finite ratios, not NaN", () => {
    // A mis-parsed alpha yields NaN, and NaN compares false against every
    // floor and every ceiling in this file without failing any of them - a
    // file whose whole shape is "floor AND ceiling" is exactly the shape NaN
    // slips through. The `not.toBe(1)` half alone ACCEPTS NaN, so the
    // finiteness check is the load-bearing one. Both stops, because the
    // gradient is parsed as a pair and only one of the two need go wrong.
    const ink = parseHex(glassRightToken("--color-es-text-light"))!;
    for (const stop of gradientStops()) {
      const r = contrastRatio(ink, stop);
      expect(Number.isFinite(r), `the ratio came out ${r}`).toBe(true);
      expect(r).not.toBe(1);
    }
  });

  it("the SECONDARY ink clears AA against both stops as well", () => {
    // The gap this closes. Until 5 September 2026 nothing in the suite
    // measured `--color-es-text-muted` on the panel itself. index.css's own
    // rationale for it quotes 5.08:1 and 4.49:1, but those are its readings
    // on the ghost BUTTON FILL - a lighter surface - and the file that wrote
    // them fixed the problem on `.persona-ghost-action` rather than on the
    // token. Every bare use of the token on the panel was reading 5.03:1 and
    // 4.37:1, under AA at the bottom of the gradient, with no test looking.
    //
    // AA and not AAA: this is the deliberately quieter of the panel's two
    // tiers, and holding it to the primary's bar would delete the tier.
    const ink = parseHex(glassRightToken("--color-es-text-muted"));
    expect(ink, "the muted token is no longer a plain hex").not.toBeNull();
    const [top, bottom] = gradientStops();
    expect(contrastRatio(ink!, top), "top-stop").toBeGreaterThanOrEqual(AA_NORMAL);
    expect(contrastRatio(ink!, bottom), "bottom-stop")
      .toBeGreaterThanOrEqual(AA_NORMAL);
  });

  it("GROUND: and it stays the QUIETER tier while doing it", () => {
    // The discriminating half, and the reason the floor above is AA rather
    // than AAA. Darkening the muted token until it cleared the primary's bar
    // would pass the assertion above and quietly collapse the panel's two
    // ink tiers into one - the same failure index.css:875-881 names as the
    // real cost of softening an ink. Ordering, on the surface as painted.
    const muted = parseRgbColour(glassRightToken("--color-es-text-muted"));
    const primary = parseRgbColour(glassRightToken("--color-es-text-light"));
    for (const stop of gradientStops()) {
      expect(contrastRatio(muted, stop)).toBeLessThan(
        contrastRatio(primary, stop),
      );
    }
  });

  it("clears AAA against both stops of the panel's own gradient", () => {
    const ink = parseHex(glassRightToken("--color-es-text-light"));
    expect(ink, "the token is no longer a plain hex").not.toBeNull();
    for (const stop of gradientStops()) {
      expect(contrastRatio(ink!, stop)).toBeGreaterThanOrEqual(AAA_NORMAL);
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
    // decoration and this file would go green on the exact near-black state
    // it exists to reject.
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
      expect(contrastRatio(warn!, track)).toBeGreaterThanOrEqual(AA_NON_TEXT);
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
    //
    // The floor is AA_NON_TEXT, the same constant the track assertion above
    // measures against, because a meter fill is a graphic on both sides of
    // the comparison: the number that says a fill is visible against its
    // track is the number that says one fill is a different fill from the
    // one beside it. This pair was pinned at a bare 1.4 instead, which is
    // not a floor anybody can cite - it sat just under the value actually
    // shipping, so the gate ran green over a tier nobody could see. A
    // threshold chosen to clear the current value measures nothing.
    const warn = parseHex(glassRightToken("--color-es-meter-warning"))!;
    expect(contrastRatio(warn, meterNormal())).toBeGreaterThanOrEqual(AA_NON_TEXT);
    // Both adjacent pairs, not one. The rationale above argues for the danger
    // end exactly as much as for the normal end.
    const danger = parseHex(glassRightToken("--color-es-danger"))!;
    expect(contrastRatio(warn, danger)).toBeGreaterThanOrEqual(AA_NON_TEXT);
  });

  it("GROUND: the depth it replaced would fail that floor", () => {
    // The discriminating half. From the pass column a floor of 3 and a floor
    // of 1.4 look identical: both go green on whatever happens to ship.
    // #2B5384 is what shipped, it was chosen against the track alone, and it
    // clears NEITHER neighbour - so this is the proof the floor above can
    // still reject a colour rather than being decoration a second time.
    const shallow = parseHex("#2B5384")!;
    const danger = parseHex(glassRightToken("--color-es-danger"))!;
    expect(contrastRatio(shallow, meterNormal())).toBeLessThan(AA_NON_TEXT);
    expect(contrastRatio(shallow, danger)).toBeLessThan(AA_NON_TEXT);
  });

  it("GROUND: the normal tier is read off this surface, not off :root", () => {
    // The positive control for the lookup itself. Without it, surfaceValue
    // could be answering from :root for everything and the comparison above
    // would be measuring against a tier the panel does not paint.
    // --color-es-text-light is the token this panel is known to repaint, so
    // the two sources disagree on it and a preference is observable.
    expect(surfaceValue("--color-es-text-light")).toBe(
      glassRightToken("--color-es-text-light"),
    );
    expect(surfaceValue("--color-es-text-light")).not.toBe(
      rootToken("--color-es-text-light"),
    );
    // And the fall-through half: a token the panel does not repaint still
    // resolves to the root value, which is where the normal tier lives now.
    expect(surfaceValue("--color-es-primary-sage")).toBe(
      rootToken("--color-es-primary-sage"),
    );
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
