/**
 * cssReaders.test.ts - the red-green three helper readers shipped without.
 *
 * compiledSheet.ts and glassSurfaceCss.ts are read BY most of the CSS suite,
 * and that is exactly why they were the least guarded code in it: every test
 * that leans on them asserts about the stylesheet, so a reader that answers
 * confidently and wrongly makes those tests greener, not redder. Three of them
 * were measured being broken on purpose with the whole suite staying green:
 *
 *   * `parseRgbColour` pulled three numbers out of ANY `f(...)`, so
 *     `oklch(1 0 0)` - white - came back as rgb(1, 0, 0), near black.
 *   * `specificity` counting `:where()` like `:is()`, which inverts the
 *     answer to "which rule wins" for every Tailwind reset selector.
 *   * `candidatesFor` reading `decl.parent` instead of walking up through
 *     at-rules, which silently dropped every declaration written in the
 *     NESTED `@media` form the compiler keeps verbatim.
 *
 * So this file calls the exported functions and nothing else. It reads no
 * source text, it compiles no stylesheet: every sheet below is a string
 * literal written here, handed to the real postcss parser, and matched against
 * real elements by jsdom's real selector engine. Every case carries a ground
 * control, because a reader that answers nothing passes an assertion about
 * what it must not answer.
 */
import { afterEach, describe, expect, it } from "vitest";
import type { Declaration, Root } from "postcss";

import { AA_NORMAL, contrastRatio } from "@/lib/appearance/contrast";
import { SCROLLBAR_THIN_PX } from "@/lib/theme/tokens";
import { MD_FROM_PX } from "./compiledSheet";

/** Named once, because half the cases below measure a white thumb. */
const WHITE = { r: 255, g: 255, b: 255 };
import {
  backgroundLayers,
  candidatesFor,
  cascadeCompare,
  clipsAndMasksOn,
  compareSpecificity,
  conditional,
  customPropertyAt,
  fontSizePx,
  inkOf,
  insetRightPx,
  layerOrder,
  lengthPx,
  ownOpacity,
  ownPaints,
  paintedStops,
  paintLayersBehind,
  paintsAnotherBox,
  parseOpacity,
  parseSheet,
  placeholderInkOf,
  rootTokens,
  specificity,
  stackOf,
  substituteAt,
  winningDeclaration,
  type Candidate,
  type Specificity,
} from "./compiledSheet";
import {
  colourAtParam,
  composite,
  gradientStops,
  layerParamAt,
  layerParamRange,
  layerPaintOver,
  paintAlpha,
  paintChannels,
  parseLayer,
  parseRgbColour,
  screenOver,
  wordsOverStack,
  worstRatioOverStack,
  type Box,
  type Region,
} from "./glassSurfaceCss";

/* ------------------------------------------------------------------ *
 * 1. parseRgbColour refuses a colour function it cannot read.
 * ------------------------------------------------------------------ */

describe("parseRgbColour reads hex and rgb()/rgba(), and refuses the rest", () => {
  // GROUND CONTROL. Every refusal below is worthless if the guard simply
  // broke the reader: these four are the syntaxes it is supposed to speak,
  // and they have to come back with the right channels.
  it.each<[label: string, raw: string, expected: { r: number; g: number; b: number }]>([
    ["a three-number rgb()", "rgb(1, 0, 0)", { r: 1, g: 0, b: 0 }],
    ["an rgba() with a fractional alpha", "rgba(1, 0, 0, .5)", { r: 1, g: 0, b: 0 }],
    ["short hex", "#fff", { r: 255, g: 255, b: 255 }],
    ["long hex", "#0E141B", { r: 14, g: 20, b: 27 }],
  ])("still parses %s", (_label, raw, expected) => {
    expect(parseRgbColour(raw)).toEqual(expected);
  });

  // Every literal here is chosen so that the OLD lenient reader would have
  // succeeded on it: strip the function-name check and its number scraper
  // finds three numbers inside each one and hands back a colour. A literal
  // the scraper also chokes on would throw either way, and the assertion
  // would pass on a reader with no guard in it at all - measured, because the
  // first two rows were written that way and did exactly that.
  it.each<[label: string, raw: string, named: RegExp]>([
    ["oklch()", "oklch(1 0 0)", /oklch/],
    ["color-mix()", "color-mix(in srgb, rgb(255, 255, 255) 50%, black)", /color-mix/],
    ["hsl()", "hsl(210 35 20)", /hsl/],
  ])("refuses %s and says which function it was", (_label, raw, named) => {
    expect(() => parseRgbColour(raw)).toThrow(named);
  });

  it("names the harm numerically: white must never read as near black", () => {
    // The old lenient reader took the first three numbers out of any `f(...)`,
    // so `oklch(1 0 0)` - plain white - came back as rgb(1, 0, 0). This is
    // what that wrong reading was worth as a contrast measurement against
    // white: around 21:1, which is the top of the scale, reported for a pair
    // of colours whose real ratio is 1:1.
    const white = parseRgbColour("#fff");
    const oldWrongReading = { r: 1, g: 0, b: 0 };
    expect(contrastRatio(oldWrongReading, white)).toBeGreaterThan(AA_NORMAL * 4);

    // POSITIVE CONTROL for that number: white measured against white really
    // is 1, so the 21 above is the mis-parse and not the arithmetic.
    expect(contrastRatio(white, white)).toBeCloseTo(1, 10);

    // The clean answer is therefore no answer at all. The call throws, so no
    // ratio is produced from an oklch literal by any route.
    expect(() => parseRgbColour("oklch(1 0 0)")).toThrow(/oklch/);

    // The other two are the same failure with different numbers. The lenient
    // reader read `hsl(210 35 20)` - a dark slate blue, about rgb(33, 53, 77)
    // - as rgb(210, 35, 20), a strong red, and `color-mix(in srgb, rgb(255,
    // 255, 255) 50%, black)` - a mid grey - as the white it was mixing from.
    expect(() => parseRgbColour("hsl(210 35 20)")).toThrow(/hsl/);
    expect(() =>
      parseRgbColour("color-mix(in srgb, rgb(255, 255, 255) 50%, black)"),
    ).toThrow(/color-mix/);
  });

  it("still refuses a syntax it cannot read even when it is unnamed", () => {
    // The second half of the ground: a string that is not a colour at all is
    // refused too, so the function-name guard is not the only thing standing
    // between a caller and a fabricated number.
    expect(() => parseRgbColour("not a colour")).toThrow(/could not read/);
  });
});

/* ------------------------------------------------------------------ *
 * 2. specificity(), Selectors Level 4.
 * ------------------------------------------------------------------ */

/** The escaped Tailwind class, assembled rather than written whole. The
 *  brackets are part of the CLASS NAME here, and a reader that treats them as
 *  an attribute selector counts this compound twice. */
const ESCAPED_TAILWIND_CLASS = ".max-h-" + "\\[85vh\\]";

const SPECIFICITY_CASES: Array<[selector: string, expected: Specificity]> = [
  // `:where()` adds nothing at all: only `.a` counts. This is the row the
  // "discount removed" mutation turns into [0, 2, 0].
  [".a:where(.b)", [0, 1, 0]],
  // `:is()` contributes its MOST SPECIFIC argument, so the id inside it wins
  // over the class beside it.
  [":is(#x, .b)", [1, 0, 0]],
  // and it adds that to whatever is written outside it.
  [".a:is(#x, .b)", [1, 1, 0]],
  // `:not()` is scored the same way.
  [".a:not(.b, #c)", [1, 1, 0]],
  ["#id .c div", [1, 1, 1]],
  ["div::before", [0, 0, 2]],
  ['[data-slot="x"]', [0, 1, 0]],
  [ESCAPED_TAILWIND_CLASS, [0, 1, 0]],
  ["*", [0, 0, 0]],
];

describe("specificity reads a selector the way the cascade does", () => {
  it.each(SPECIFICITY_CASES)("scores %s", (selector, expected) => {
    expect(specificity(selector)).toEqual(expected);
  });

  it("answers a selector it has never been shown, and counts it", () => {
    // GROUND CONTROL, and deliberately not one the rows above already fix:
    // every value in the table is asserted there one by one, so a case built
    // out of the same rows cannot fail if they pass. These selectors are not
    // in the table. A reader hard-wired to the cases, or one returning a
    // constant, has nothing to say about them.
    const fresh: Array<[string, Specificity]> = [
      ["em", [0, 0, 1]],
      ["em strong", [0, 0, 2]],
      [".p em", [0, 1, 1]],
      [".p .q em", [0, 2, 1]],
      ["#p .q em", [1, 1, 1]],
    ];
    for (const [selector, expected] of fresh) {
      expect(specificity(selector), selector).toEqual(expected);
    }
    // And they really are a ladder, so a reader that counted SOMETHING but
    // counted it in the wrong column is caught too.
    for (let i = 1; i < fresh.length; i += 1) {
      expect(
        compareSpecificity(fresh[i][1], fresh[i - 1][1]),
        `${fresh[i][0]} should outrank ${fresh[i - 1][0]}`,
      ).toBeGreaterThan(0);
    }
  });

  it("keeps the escaped brackets inside the class name", () => {
    // Stated on its own because it is the one row where being wrong is
    // invisible in the totals: read as `.max-h-` plus `[85vh]`, this compound
    // scores [0, 2, 0], and every Tailwind arbitrary-value utility in the
    // sheet would then outrank the hand-written rule it is supposed to lose
    // to. The unescaped form really is two things, and is scored as two.
    expect(specificity(ESCAPED_TAILWIND_CLASS)).toEqual([0, 1, 0]);
    expect(specificity(".max-h-[85vh]")).toEqual([0, 2, 0]);
  });

  it("orders those selectors the way the cascade ranks them", () => {
    const ladder = [
      "*",
      "div::before",
      '[data-slot="x"]',
      ":is(#x, .b)",
      ".a:is(#x, .b)",
      "#id .c div",
    ];
    for (let i = 1; i < ladder.length; i += 1) {
      const lower = specificity(ladder[i - 1]);
      const higher = specificity(ladder[i]);
      expect(
        compareSpecificity(higher, lower),
        `${ladder[i]} should outrank ${ladder[i - 1]}`,
      ).toBeGreaterThan(0);
      expect(compareSpecificity(lower, higher)).toBeLessThan(0);
    }
  });

  it("calls a genuine tie a tie", () => {
    // GROUND for the ladder: a comparator that answered "greater" to
    // everything would pass every step above.
    expect(
      compareSpecificity(specificity(".a:where(.b)"), specificity('[data-slot="x"]')),
    ).toBe(0);
    expect(
      compareSpecificity(specificity(ESCAPED_TAILWIND_CLASS), specificity(".a:where(.b)")),
    ).toBe(0);
  });
});

/* ------------------------------------------------------------------ *
 * 3. candidatesFor: conditions, and the walk up through at-rules.
 * ------------------------------------------------------------------ */

const COLOUR = /^color$/;

/** Always present, always matching, never conditional. Every sheet below
 *  carries it so an empty answer can never be mistaken for a correct one. */
const GROUND = ".ground { color: #010203; }";

const CONDITIONAL_COLOUR = "#aa0000";

const TOP_LEVEL_MEDIA = `@media (min-width: 1px) { .x { color: ${CONDITIONAL_COLOUR}; } }`;
const NESTED_MEDIA = `.x { @media (min-width: 1px) { color: ${CONDITIONAL_COLOUR}; } }`;
const TOP_LEVEL_SUPPORTS = `@supports (color: red) { .x { color: ${CONDITIONAL_COLOUR}; } }`;
const NESTED_SUPPORTS = `.x { @supports (color: red) { color: ${CONDITIONAL_COLOUR}; } }`;
/** ASSEMBLED, and the only at-rule name here that has to be. Tailwind scans
 *  this file as raw text and the container at-rule is spelled exactly like
 *  the utility that sets `container-type`, so writing it whole - in a string,
 *  in a test name, in a comment - emits a rule for it into the SHIPPED
 *  stylesheet. `@media` and `@supports` are not utilities and stay literal. */
const AT_CONTAINER = "@" + "container";

const TOP_LEVEL_CONTAINER = `${AT_CONTAINER} (min-width: 1px) { .x { color: ${CONDITIONAL_COLOUR}; } }`;
const NESTED_CONTAINER = `.x { ${AT_CONTAINER} (min-width: 1px) { color: ${CONDITIONAL_COLOUR}; } }`;
const LAYERED_TOP_LEVEL_MEDIA =
  `@layer base { @media (min-width: 1px) { .x { color: ${CONDITIONAL_COLOUR}; } } }`;
const LAYERED_NESTED_MEDIA =
  `@layer base { .x { @media (min-width: 1px) { color: ${CONDITIONAL_COLOUR}; } } }`;

function sheet(body: string): string {
  return `${GROUND}\n${body}\n`;
}

const attached: Element[] = [];

/** A real element in a real document, so `Element.matches` is doing the
 *  matching rather than a string comparison hidden in the reader. */
function elementWith(className: string): HTMLDivElement {
  const el = document.createElement("div");
  el.className = className;
  document.body.appendChild(el);
  attached.push(el);
  return el;
}

afterEach(() => {
  while (attached.length > 0) attached.pop()?.remove();
});

/** Every field of every candidate, flattened, so two readings can be compared
 *  whole instead of one property at a time. */
function fingerprint(
  body: string,
  el: Element,
  options: { includeConditional?: boolean } = {},
): string[] {
  return candidatesFor(parseSheet(sheet(body)), el, COLOUR, options).map((c) =>
    [
      c.selector,
      c.prop,
      c.value,
      String(c.important),
      String(c.layer),
      c.specificity.join(","),
      String(c.order),
    ].join(" | "),
  );
}

function colourDeclarations(css: string): Declaration[] {
  const out: Declaration[] = [];
  parseSheet(css).walkDecls(COLOUR, (decl) => {
    out.push(decl);
  });
  return out;
}

describe("candidatesFor finds a plain rule in either mode", () => {
  it("returns the unconditional declaration with the flag off and on", () => {
    // GROUND CONTROL for this whole section. Everything below asserts about
    // what is NOT returned, and a reader that returns nothing at all passes
    // all of it. This is the row that says the reader works.
    const el = elementWith("x ground");
    const root = parseSheet(sheet(".x { color: #aa0000; }"));

    const plain = candidatesFor(root, el, COLOUR);
    expect(plain.map((c) => c.selector)).toEqual([".ground", ".x"]);
    expect(plain.map((c) => c.value)).toEqual(["#010203", "#aa0000"]);

    const withConditional = candidatesFor(parseSheet(sheet(".x { color: #aa0000; }")), el, COLOUR, {
      includeConditional: true,
    });
    expect(withConditional.map((c) => c.selector)).toEqual([".ground", ".x"]);
  });

  it("returns nothing for an element no rule matches", () => {
    // The other half of the ground: the reader discriminates, it does not
    // hand back the whole sheet.
    const el = elementWith("nothing-matches-this");
    const root = parseSheet(sheet(".x { color: #aa0000; }"));
    expect(candidatesFor(root, el, COLOUR, { includeConditional: true })).toEqual([]);
  });
});

const CONDITIONAL_SHAPES: Array<[label: string, body: string, layer: string | null]> = [
  ["a top-level @media", TOP_LEVEL_MEDIA, null],
  ["a nested @media", NESTED_MEDIA, null],
  ["a top-level @supports", TOP_LEVEL_SUPPORTS, null],
  ["a nested @supports", NESTED_SUPPORTS, null],
  [`a top-level ${AT_CONTAINER}`, TOP_LEVEL_CONTAINER, null],
  [`a nested ${AT_CONTAINER}`, NESTED_CONTAINER, null],
  ["a top-level @media inside @layer base", LAYERED_TOP_LEVEL_MEDIA, "base"],
  ["a nested @media inside @layer base", LAYERED_NESTED_MEDIA, "base"],
];

describe("candidatesFor hides a conditional declaration unless asked", () => {
  it.each(CONDITIONAL_SHAPES)(
    "%s is invisible by default and visible with includeConditional",
    (_label, body, layer) => {
      const el = elementWith("x ground");
      const root = parseSheet(sheet(body));

      // Invisible: only the unconditional ground rule comes back, which is
      // also the proof the sheet parsed and the element matches.
      expect(candidatesFor(root, el, COLOUR).map((c) => c.selector)).toEqual([".ground"]);

      const opened = candidatesFor(parseSheet(sheet(body)), el, COLOUR, {
        includeConditional: true,
      });
      expect(opened.map((c) => c.selector)).toEqual([".ground", ".x"]);
      expect(opened[1].value).toBe(CONDITIONAL_COLOUR);
      expect(opened[1].layer).toBe(layer);
      expect(opened[1].specificity).toEqual([0, 1, 0]);
    },
  );

  const EQUIVALENT_FORMS: Array<[label: string, topLevel: string, nested: string]> = [
    ["@media", TOP_LEVEL_MEDIA, NESTED_MEDIA],
    ["@supports", TOP_LEVEL_SUPPORTS, NESTED_SUPPORTS],
    [AT_CONTAINER, TOP_LEVEL_CONTAINER, NESTED_CONTAINER],
    ["@media inside @layer base", LAYERED_TOP_LEVEL_MEDIA, LAYERED_NESTED_MEDIA],
  ];

  it.each(EQUIVALENT_FORMS)(
    "reads the nested %s form exactly as it reads the top-level one",
    (_label, topLevel, nested) => {
      // This is the pair the "nested walk removed" mutation breaks. The
      // compiler keeps `.x { @media (...) { ... } }` verbatim, so there the
      // declaration's PARENT is the at-rule and its rule is a grandparent.
      // Reading `decl.parent` alone drops the nested form and keeps the
      // top-level one, which made the flag above a half-truth.
      const el = elementWith("x ground");
      expect(fingerprint(nested, el, { includeConditional: true })).toEqual(
        fingerprint(topLevel, el, { includeConditional: true }),
      );
      expect(fingerprint(nested, el)).toEqual(fingerprint(topLevel, el));
      // GROUND: the comparison above is not two empty lists agreeing.
      expect(fingerprint(nested, el, { includeConditional: true })).toHaveLength(2);
      expect(fingerprint(nested, el)).toHaveLength(1);
    },
  );

  it("classifies both forms as conditional and the plain rule as not", () => {
    const [plain] = colourDeclarations(".x { color: #aa0000; }");
    const [topLevel] = colourDeclarations(TOP_LEVEL_MEDIA);
    const [nested] = colourDeclarations(NESTED_MEDIA);
    expect(conditional(plain)).toBe(false);
    expect(conditional(topLevel)).toBe(true);
    expect(conditional(nested)).toBe(true);
  });

  it("does not treat @layer on its own as a condition", () => {
    // A layered rule always applies; only media, supports and container are
    // questions jsdom cannot answer.
    const [layered] = colourDeclarations("@layer base { .x { color: #aa0000; } }");
    expect(conditional(layered)).toBe(false);

    const el = elementWith("x ground");
    const root = parseSheet(sheet("@layer base { .x { color: #aa0000; } }"));
    const found = candidatesFor(root, el, COLOUR);
    expect(found.map((c) => c.selector)).toEqual([".ground", ".x"]);
    expect(found[1].layer).toBe("base");
  });
});

describe("fontSizePx reads a breakpoint variant at a window width", () => {
  // Every size below is a different number, named once and written into the
  // sheet from the name, so a reader that answered one size for every
  // width fails all but one of them.
  const SIZE = { small: 12, wide: 14, hand: 15, narrow: 11, never: 30 } as const;
  const sheet = parseSheet(`
    .probe-small { font-size: ${SIZE.small}px; }
    .probe-wide { @media (width >= 48rem) { font-size: ${SIZE.wide}px; } }
    .probe-hand { @media (min-width: 900px) { font-size: ${SIZE.hand}px; } }
    .probe-narrow { @media (max-width: 500px) { font-size: ${SIZE.narrow}px; } }
    .probe-motion { @media (prefers-reduced-motion: reduce) { font-size: ${SIZE.never}px; } }
    .probe-support { @supports (display: flex) { font-size: ${SIZE.never}px; } }
  `);
  const table = rootTokens(sheet);
  const at = (el: Element, widthPx?: number) =>
    fontSizePx(sheet, table, 16, el, widthPx === undefined ? {} : { viewportPx: widthPx });

  it("pins the breakpoint the rows read either side of: Tailwind's md, 48rem", () => {
    expect(MD_FROM_PX).toBe(768);
  });

  it("takes a width variant from its breakpoint on, and never below it", () => {
    const el = elementWith("probe-small probe-wide");
    expect(at(el, MD_FROM_PX - 1)).toBe(SIZE.small);
    expect(at(el, MD_FROM_PX)).toBe(SIZE.wide);
    expect(at(el, 1440)).toBe(SIZE.wide);
  });

  it("leaves every conditional rule out when no width is asked about", () => {
    expect(at(elementWith("probe-small probe-wide"))).toBe(SIZE.small);
  });

  it("reads the hand-written min- and max- forms", () => {
    expect(at(elementWith("probe-small probe-hand"), 899)).toBe(SIZE.small);
    expect(at(elementWith("probe-small probe-hand"), 900)).toBe(SIZE.hand);
    expect(at(elementWith("probe-small probe-narrow"), 500)).toBe(SIZE.narrow);
    expect(at(elementWith("probe-small probe-narrow"), 501)).toBe(SIZE.small);
  });

  it("never lets a condition that is not a width decide a width question", () => {
    expect(at(elementWith("probe-small probe-motion"), 1440)).toBe(SIZE.small);
    expect(at(elementWith("probe-small probe-support"), 1440)).toBe(SIZE.small);
  });

  it("hands a child the size a window that wide gives its parent", () => {
    const child = document.createElement("span");
    elementWith("probe-small probe-wide").appendChild(child);
    expect(at(child, MD_FROM_PX)).toBe(SIZE.wide);
    expect(at(child, MD_FROM_PX - 1)).toBe(SIZE.small);
  });
});

describe("candidatesFor never returns a keyframe step", () => {
  // Percentage steps rather than from/to on purpose. `from` is an element
  // selector a div never matches, so dropping the keyframes guard would
  // change nothing observable; `0%` is not a selector jsdom's engine can
  // parse at all, so a reader that walks into a keyframes block either
  // returns a step as a candidate or throws trying. Both are red here.
  const KEYFRAMES =
    "@keyframes es-probe { 0% { color: #aa0000; } 100% { color: #00aa00; } }";

  it.each<[label: string, options: { includeConditional?: boolean }]>([
    ["with the flag off", {}],
    ["with includeConditional on", { includeConditional: true }],
  ])("ignores the block %s", (_label, options) => {
    const el = elementWith("x ground");
    const root = parseSheet(sheet(KEYFRAMES));
    const found = candidatesFor(root, el, COLOUR, options);
    expect(found.map((c) => c.selector)).toEqual([".ground"]);
    expect(found.map((c) => c.value)).not.toContain("#aa0000");
  });

  it("really did put two declarations inside that block", () => {
    // GROUND: the assertions above are only worth something if the keyframes
    // block actually carries the property the reader was asked for.
    const decls = colourDeclarations(KEYFRAMES);
    expect(decls.map((d) => d.value)).toEqual(["#aa0000", "#00aa00"]);
  });
});

describe("candidatesFor leaves a box the element does not paint", () => {
  it("skips a pseudo-element rule and keeps the element's own", () => {
    const el = elementWith("x ground");
    const root = parseSheet(sheet(".x::before { color: #aa0000; }"));
    expect(candidatesFor(root, el, COLOUR).map((c) => c.selector)).toEqual([".ground"]);
  });

  it("knows which selectors paint another box", () => {
    // GROUND for the row above: the predicate says no to an ordinary class,
    // so the skip is about the pseudo-element and not about everything.
    expect(paintsAnotherBox(".x")).toBe(false);
    expect(paintsAnotherBox(".x::before")).toBe(true);
    expect(paintsAnotherBox(".x::-webkit-scrollbar-thumb")).toBe(true);
    expect(paintsAnotherBox(".x:-moz-focusring")).toBe(true);
  });
});

/* ------------------------------------------------------------------ *
 * 4. cascadeCompare, one case per branch.
 * ------------------------------------------------------------------ */

const LAYERS = ["base", "theme"];

function candidate(over: Partial<Candidate> = {}): Candidate {
  return {
    selector: ".x",
    prop: "color",
    value: "#010203",
    important: false,
    layer: null,
    specificity: [0, 1, 0],
    order: 1,
    ...over,
  };
}

describe("cascadeCompare ranks two candidates the way CSS Cascade 5 does", () => {
  /** Positive when the first wins, and the reverse has to be negative: a
   *  comparator that answered "greater" both ways would pass half of these. */
  function winner(a: Candidate, b: Candidate): void {
    expect(cascadeCompare(a, b, LAYERS)).toBeGreaterThan(0);
    expect(cascadeCompare(b, a, LAYERS)).toBeLessThan(0);
  }

  it("puts important above normal", () => {
    winner(candidate({ important: true }), candidate({ important: false }));
  });

  it("prefers unlayered to layered for a normal declaration", () => {
    winner(candidate({ layer: null }), candidate({ layer: "theme" }));
  });

  it("prefers layered to unlayered for an important one", () => {
    winner(
      candidate({ layer: "base", important: true }),
      candidate({ layer: null, important: true }),
    );
  });

  it("prefers a later layer to an earlier one", () => {
    winner(candidate({ layer: "theme" }), candidate({ layer: "base" }));
  });

  it("falls to specificity before source order", () => {
    winner(
      candidate({ specificity: [0, 2, 0], order: 1 }),
      candidate({ specificity: [0, 1, 0], order: 9 }),
    );
  });

  it("falls to source order when everything else is equal", () => {
    winner(candidate({ order: 2 }), candidate({ order: 1 }));
  });

  it("calls two identical candidates a tie", () => {
    // GROUND: the comparator can return zero, so none of the rows above pass
    // because it never does.
    expect(cascadeCompare(candidate(), candidate(), LAYERS)).toBe(0);
  });
});

describe("the cascade model run end to end over a real sheet", () => {
  const LAYERED_SHEET = [
    "@layer base, theme;",
    "@layer base { .x { color: #b1b1b1; } }",
    "@layer theme { .x { color: #c2c2c2; } }",
    ".x { color: #d3d3d3; }",
  ].join("\n");

  it("reads the layer order off the sheet", () => {
    expect(layerOrder(parseSheet(LAYERED_SHEET))).toEqual(["base", "theme"]);
    // GROUND: a sheet with no layers has no order, so the reader is not
    // inventing names.
    expect(layerOrder(parseSheet(".x { color: #d3d3d3; }"))).toEqual([]);
  });

  it("lets the unlayered normal declaration win", () => {
    const el = elementWith("x");
    expect(winningDeclaration(parseSheet(LAYERED_SHEET), el, COLOUR)?.value).toBe("#d3d3d3");
  });

  it("lets the layered important declaration win", () => {
    const el = elementWith("x");
    const css = [
      "@layer base, theme;",
      "@layer base { .x { color: #e4e4e4 !important; } }",
      ".x { color: #f5f5f5 !important; }",
    ].join("\n");
    expect(winningDeclaration(parseSheet(css), el, COLOUR)?.value).toBe("#e4e4e4");
  });

  it("has nothing to hand back when no rule matches", () => {
    const el = elementWith("nothing-matches-this");
    expect(winningDeclaration(parseSheet(LAYERED_SHEET), el, COLOUR)).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * 5. paintedStops reads the BOTTOM layer only.
 * ------------------------------------------------------------------ */

const TOKEN_SHEET = [
  ":root, :host {",
  "  --es-panel: rgba(14, 20, 27, 0.9);",
  "  --es-veil: rgba(255, 255, 255, 0);",
  "  --es-frame: #0E141B;",
  "}",
].join("\n");

describe("paintedStops reads the bottom background layer", () => {
  const table = rootTokens(parseSheet(TOKEN_SHEET));

  it("built a token table to resolve against", () => {
    // GROUND before any var() assertion: the table is real and has the three
    // tokens in it, so a var() that resolves to nothing cannot be mistaken
    // for a colour the reader dropped on purpose.
    expect(table.get("--es-panel")).toBe("rgba(14, 20, 27, 0.9)");
    expect(table.get("--es-veil")).toBe("rgba(255, 255, 255, 0)");
    expect(table.get("--es-frame")).toBe("#0E141B");
  });

  it("returns the one stop of a single-layer background", () => {
    // GROUND CONTROL for the reader itself. Everything below is about what
    // must NOT come back, and a reader that returns [] satisfies all of it.
    expect(paintedStops("#0E141B", table)).toEqual(["#0E141B"]);
    expect(paintedStops("rgba(14, 20, 27, 0.9)", table)).toEqual(["rgba(14, 20, 27, 0.9)"]);
  });

  it("drops every layer above the bottom one", () => {
    const stops = paintedStops(
      "linear-gradient(180deg, rgba(200, 30, 30, 0.6), rgba(30, 200, 30, 0.4)), #0E141B",
      table,
    );
    expect(stops).toEqual(["#0E141B"]);
    // Said the other way round, because "equals one thing" and "does not
    // contain the other two" fail differently when the reader changes.
    expect(stops).not.toContain("rgba(200, 30, 30, 0.6)");
    expect(stops).not.toContain("rgba(30, 200, 30, 0.4)");
  });

  it("returns every stop when the bottom layer is itself a gradient", () => {
    // POSITIVE CONTROL for the row above: the reader can return more than one
    // stop, so returning one there was a choice about layers and not a cap.
    expect(
      paintedStops("#123456, linear-gradient(180deg, rgba(14, 20, 27, 0.9), #0E141B)", table),
    ).toEqual(["rgba(14, 20, 27, 0.9)", "#0E141B"]);
  });

  it("drops transparent and a zero alpha, and keeps the rest", () => {
    expect(
      paintedStops("linear-gradient(180deg, rgba(14, 20, 27, 0.9), transparent)", table),
    ).toEqual(["rgba(14, 20, 27, 0.9)"]);
    expect(
      paintedStops("linear-gradient(180deg, rgba(14, 20, 27, 0.9), rgba(255, 255, 255, 0))", table),
    ).toEqual(["rgba(14, 20, 27, 0.9)"]);
    // POSITIVE CONTROL: the same shape with a colour that DOES paint returns
    // both stops, so the two rows above dropped something rather than
    // stopping at the first stop they found.
    expect(
      paintedStops("linear-gradient(180deg, rgba(14, 20, 27, 0.9), #123456)", table),
    ).toEqual(["rgba(14, 20, 27, 0.9)", "#123456"]);
    // A non-zero alpha of the same colour is paint and stays.
    expect(
      paintedStops(
        "linear-gradient(180deg, rgba(14, 20, 27, 0.9), rgba(255, 255, 255, 0.01))",
        table,
      ),
    ).toEqual(["rgba(14, 20, 27, 0.9)", "rgba(255, 255, 255, 0.01)"]);
  });

  it("resolves var() against the table it was given", () => {
    expect(paintedStops("var(--es-frame)", table)).toEqual(["#0E141B"]);
    expect(
      paintedStops("linear-gradient(180deg, var(--es-panel), var(--es-veil)), var(--es-frame)", table),
    ).toEqual(["#0E141B"]);
    expect(
      paintedStops("#123456, linear-gradient(180deg, var(--es-panel), var(--es-veil))", table),
    ).toEqual(["rgba(14, 20, 27, 0.9)"]);
    // GROUND: the same value against an EMPTY table resolves to nothing, so
    // the three rows above went through the table rather than through some
    // literal already present in the string.
    expect(paintedStops("var(--es-frame)", new Map())).toEqual([]);
  });

  it("prefers a var() fallback when the token is unknown", () => {
    expect(paintedStops("var(--not-a-token, #0E141B)", new Map())).toEqual(["#0E141B"]);
  });
});

/* ------------------------------------------------------------------ *
 * 8. screenOver blends, and never darkens.
 *
 * The sidebar's right edge - where its scrollbar is - carries a 46px sheen
 * in `mix-blend-mode: screen`, and the decision to paint that panel's thumb
 * opaque white rests on how much that sheen lifts the ground under the bar.
 * A `screenOver` that quietly behaved like an ordinary composite would make
 * the corner read darker than it ships, and every contrast assertion in the
 * suite would go on passing.
 * ------------------------------------------------------------------ */

const MID = { r: 100, g: 120, b: 140 };

describe("screenOver lightens the way a screen blend does", () => {
  // GROUND CONTROL: the reader answers at all, and differently for
  // different inputs, so the identities below are not a function that
  // returns its backdrop.
  it("answers differently for a light layer and a dark one", () => {
    expect(screenOver("rgb(200, 216, 236)", MID)).not.toEqual(
      screenOver("rgb(20, 20, 20)", MID),
    );
  });

  it("leaves the backdrop alone under a fully black layer", () => {
    // 255 - (255-0)(255-b)/255 = b. Screen with black is the identity.
    expect(screenOver("rgb(0, 0, 0)", MID)).toEqual(MID);
  });

  it("takes an opaque white layer to white", () => {
    expect(screenOver("rgb(255, 255, 255)", MID)).toEqual({ r: 255, g: 255, b: 255 });
  });

  it("mixes the blended result back at the layer's own alpha", () => {
    // Blend first: 255 - (255-255)(255-b)/255 = 255 on every channel. Then
    // mix at 0.5 towards the backdrop: (255+100)/2, (255+120)/2, (255+140)/2.
    expect(screenOver("rgba(255, 255, 255, 0.5)", MID)).toEqual({
      r: 178,
      g: 188,
      b: 198,
    });
  });

  it("never darkens, whatever the layer is", () => {
    for (const layer of [
      "rgb(0, 0, 0)",
      "rgba(0, 0, 0, 0.5)",
      "rgb(200, 216, 236)",
      "rgba(200, 216, 236, 0.15)",
      "rgb(255, 255, 255)",
    ]) {
      const lit = screenOver(layer, MID);
      expect(lit.r, `${layer} darkened the red channel`).toBeGreaterThanOrEqual(MID.r);
      expect(lit.g, `${layer} darkened the green channel`).toBeGreaterThanOrEqual(MID.g);
      expect(lit.b, `${layer} darkened the blue channel`).toBeGreaterThanOrEqual(MID.b);
    }
  });

  it("names the harm numerically: it is not the same answer as an ordinary composite", () => {
    // The sheen as the sheet writes it, over the sidebar's lit corner as the
    // panel paints it. Screen and normal differ here by more than a rounding
    // step, and the difference is what the thumb decision was made on.
    const corner = { r: 107, g: 136, b: 169 };
    const sheen = "rgba(200, 216, 236, 0.15)";
    const blended = screenOver(sheen, corner);
    const plain = composite(sheen, corner);
    expect(contrastRatio(WHITE, blended)).toBeLessThan(contrastRatio(WHITE, plain));
    expect(blended.r).toBeGreaterThan(plain.r);
  });
});

/* ------------------------------------------------------------------ *
 * 9. The gradient geometry: where a layer paints, not what it contains.
 * ------------------------------------------------------------------ */

const SQUARE: Box = { w: 100, h: 100 };
const TALL: Box = { w: 100, h: 400 };
const WIDE: Box = { w: 400, h: 100 };

describe("parseLayer reads the shapes the sheet writes, and refuses the rest", () => {
  it("reads a bare colour as a layer that fills its box", () => {
    expect(parseLayer("rgba(14, 20, 27, 0.9)")).toEqual({
      shape: { kind: "solid" },
      stops: [{ colour: "rgba(14, 20, 27, 0.9)", pos: 0 }],
    });
  });

  it("reads the side and corner keywords as angles", () => {
    expect(parseLayer("linear-gradient(to top, #000 0%, #fff 100%)").shape).toEqual({
      kind: "linear",
      angleDeg: 0,
    });
    expect(parseLayer("linear-gradient(90deg, #000, #fff)").shape).toEqual({
      kind: "linear",
      angleDeg: 90,
    });
    expect(parseLayer("linear-gradient(to top right, #000, #fff)").shape).toEqual({
      kind: "linear",
      corner: "to top right",
    });
  });

  it("reads an ellipse with its centre and radii", () => {
    expect(parseLayer("radial-gradient(ellipse 60% 34% at 82% 6%, #abc 0%, transparent 64%)")).toEqual(
      {
        shape: { kind: "radial", rx: 0.6, ry: 0.34, cx: 0.82, cy: 0.06 },
        stops: [
          { colour: "#abc", pos: 0 },
          { colour: "transparent", pos: 0.64 },
        ],
      },
    );
  });

  it("refuses a gradient whose direction it cannot place", () => {
    // A missing direction means `to bottom`, and a reader that guessed would
    // place half the layers in this sheet upside down.
    expect(() => parseLayer("linear-gradient(#000, #fff)")).toThrow(/does not speak/);
    expect(() => parseLayer("radial-gradient(circle at 50% 50%, #000, #fff)")).toThrow(
      /does not speak/,
    );
    expect(() => parseLayer("conic-gradient(from 0deg, #000, #fff)")).toThrow(
      /not a background layer/,
    );
  });
});

describe("gradientStops fills the positions the sheet leaves out", () => {
  it("gives the ends 0 and 1 when neither is written", () => {
    expect(gradientStops(["#000", "#fff"])).toEqual([
      { colour: "#000", pos: 0 },
      { colour: "#fff", pos: 1 },
    ]);
  });

  it("spreads a run of missing positions evenly between the known ones", () => {
    expect(gradientStops(["#000 0%", "#111", "#222", "#333 100%"])).toEqual([
      { colour: "#000", pos: 0 },
      { colour: "#111", pos: 1 / 3 },
      { colour: "#222", pos: 2 / 3 },
      { colour: "#333", pos: 1 },
    ]);
  });

  it("never lets a position fall below the one before it", () => {
    expect(gradientStops(["#000 60%", "#fff 20%"]).map((s) => s.pos)).toEqual([0.6, 0.6]);
  });

  it("refuses a stop written with a position range", () => {
    expect(() => gradientStops(["#000 10% 40%", "#fff"])).toThrow(/position RANGE/);
  });
});

describe("layerParamAt puts a point on the gradient line", () => {
  it("reads 0 and 1 at the two ends of a side gradient", () => {
    const shape = parseLayer("linear-gradient(to top, #000, #fff)").shape;
    expect(layerParamAt(shape, SQUARE, 0.5, 1)).toBeCloseTo(0, 6);
    expect(layerParamAt(shape, SQUARE, 0.5, 0)).toBeCloseTo(1, 6);
  });

  /* The corner keywords are the one place a gradient's angle is not written
   * in the sheet, so the reader has to derive it, and a derivation is worth
   * exactly as much as the rule it is taken from. CSS Images 3 states it as:
   * "the gradient line must be angled such that it points into the same
   * quadrant as the specified corner, and is perpendicular to a line
   * intersecting the two neighboring corners of the gradient box."
   *
   * For `to top right` on a w by h box the neighbouring corners are the
   * top-left and the bottom-right. The line joining them runs (w, h), so the
   * gradient line runs along (h, -w) and its angle off `to top` is
   * atan2(h, w). Substituting that into the projection gives a result worth
   * writing down, because it is what the cases below assert:
   *
   *   sin = h/d, cos = w/d with d = hypot(w, h), so the gradient line's
   *   length |w sin| + |h cos| is 2wh/d, and the parameter at (x, y) is
   *   0.5 + (wh(x - 0.5) - wh(y - 0.5)) / (2wh) = 0.5 + (x - y)/2.
   *
   * The aspect cancels. A corner gradient reads the SAME parameter at the
   * same fractional point of any box, the two neighbouring corners both read
   * exactly 0.5, and a reader that makes them disagree has the angle wrong.
   * This is the calibration a square cannot give: on a square atan2(h, w)
   * and atan2(w, h) are both 45 degrees, so the whole question vanishes. */
  const CORNER = parseLayer("linear-gradient(to top right, #000, #fff)").shape;

  it("reads 0 and 1 at the two corners a corner gradient runs between", () => {
    for (const box of [SQUARE, TALL, WIDE]) {
      expect(layerParamAt(CORNER, box, 0, 1)).toBeCloseTo(0, 6);
      expect(layerParamAt(CORNER, box, 1, 0)).toBeCloseTo(1, 6);
    }
  });

  it("puts a corner gradient's two NEIGHBOURING corners at the same parameter", () => {
    // The rule the angle comes from, asserted directly. On a non-square box
    // this is the whole of the magic-corner rule; getting the angle wrong by
    // its complement splits these two apart, into 0.5 + (h*h - w*w) / (2 *
    // (w*w + h*h)) and its mirror, which on 100x400 is 0.941176 and
    // 0.058824, while leaving every square-box reading untouched.
    for (const box of [TALL, WIDE]) {
      const topLeft = layerParamAt(CORNER, box, 0, 0);
      const bottomRight = layerParamAt(CORNER, box, 1, 1);
      expect(topLeft).toBeCloseTo(bottomRight, 10);
      // And the value is not free: the joining line runs through the box's
      // centre, which is the middle of the gradient line.
      expect(topLeft).toBeCloseTo(0.5, 10);
    }
    // GROUND: the reader is not answering 0.5 to everything. The corners the
    // gradient actually runs between are still 0 and 1 on the same box.
    expect(layerParamAt(CORNER, TALL, 0, 1)).toBeCloseTo(0, 10);
    expect(layerParamAt(CORNER, TALL, 1, 0)).toBeCloseTo(1, 10);
  });

  it("reads a corner gradient at a named point the same way at every aspect", () => {
    // 0.5 + (x - y)/2 at (1, 0.5) is 0.75, on any box. Arithmetic, not an
    // inequality: a reader whose corner angle is the complement gives 0.75
    // on a square and 0.5 + w*w / (2 * (w*w + h*h)) elsewhere, which on
    // 100x400 is 0.529412.
    for (const box of [SQUARE, TALL, WIDE]) {
      expect(layerParamAt(CORNER, box, 1, 0.5)).toBeCloseTo(0.75, 10);
    }
  });

  it("does use the box, where the box is what the answer depends on", () => {
    // The aspect cancels for a corner keyword and does NOT cancel for a
    // written angle off the axes, which is the shape `.warm-canvas::before`
    // ends with. 176deg: sin = 0.0697565, cos = -0.9975641, and the gradient
    // line is |w sin| + |h cos| long, so at (1, 0.5) the parameter is
    // 0.5 + (w/2) sin / len. On 100x100 that is 0.5 + 3.487825/106.73206 =
    // 0.5326780; on 100x400 it is 0.5 + 3.487825/406.00127 = 0.5085907.
    const angled = parseLayer("linear-gradient(176deg, #000, #fff)").shape;
    expect(layerParamAt(angled, SQUARE, 1, 0.5)).toBeCloseTo(0.5326780, 6);
    expect(layerParamAt(angled, TALL, 1, 0.5)).toBeCloseTo(0.5085907, 6);
  });

  it("measures a radial in radius units from its centre", () => {
    const shape = parseLayer(
      "radial-gradient(ellipse 50% 25% at 50% 50%, #000, transparent 100%)",
    ).shape;
    expect(layerParamAt(shape, SQUARE, 0.5, 0.5)).toBeCloseTo(0, 6);
    expect(layerParamAt(shape, SQUARE, 1, 0.5)).toBeCloseTo(1, 6);
    expect(layerParamAt(shape, SQUARE, 0.5, 0.75)).toBeCloseTo(1, 6);
  });
});

describe("layerParamRange bounds a region without sampling it", () => {
  const RADIAL = parseLayer(
    "radial-gradient(ellipse 50% 50% at 50% 50%, #000, transparent 100%)",
  ).shape;

  it("takes the nearest and farthest points of a region off to one side", () => {
    // x from 0.8 to 1.0 is 0.6 to 1.0 radii away; y straddles the centre, so
    // the nearest point is level with it and the farthest is at the corner.
    const [lo, hi] = layerParamRange(RADIAL, SQUARE, { x0: 0.8, x1: 1, y0: 0.4, y1: 0.7 });
    expect(lo).toBeCloseTo(0.6, 6);
    expect(hi).toBeCloseTo(Math.hypot(1, 0.4), 6);
  });

  it("reads zero when the region contains the centre", () => {
    const [lo] = layerParamRange(RADIAL, SQUARE, { x0: 0.4, x1: 0.6, y0: 0.4, y1: 0.6 });
    expect(lo).toBeCloseTo(0, 6);
  });

  it("takes a linear gradient's extremes at the region's corners", () => {
    const shape = parseLayer("linear-gradient(90deg, #000, #fff)").shape;
    const [lo, hi] = layerParamRange(shape, SQUARE, { x0: 0.25, x1: 0.75, y0: 0, y1: 1 });
    expect(lo).toBeCloseTo(0.25, 6);
    expect(hi).toBeCloseTo(0.75, 6);
  });
});

describe("colourAtParam interpolates the way the engine does", () => {
  const STOPS = gradientStops(["rgba(160, 195, 235, 0.22) 0%", "transparent 64%"]);

  it("clamps to the end stops outside the ramp", () => {
    expect(colourAtParam(STOPS, -1)).toBe("rgba(160, 195, 235, 0.22)");
    expect(colourAtParam(STOPS, 2)).toBe("transparent");
  });

  it("keeps the hue as the alpha falls, because the mix is premultiplied", () => {
    // Half way to `transparent`: the alpha halves and the colour does NOT
    // slide towards black, which is what an un-premultiplied mix would do.
    expect(colourAtParam(STOPS, 0.32)).toBe("rgba(160, 195, 235, 0.11)");
  });

  it("mixes two opaque-ish stops by hand-checkable arithmetic", () => {
    // rgba(54,78,104,.92) -> rgba(84,112,146,.84) at f = 0.5.
    // Premultiplied: (49.68,71.76,95.68) and (70.56,94.08,122.64); the mean
    // is (60.12,82.92,109.16) at alpha 0.88, so the colour is that over 0.88.
    const stops = gradientStops(["rgba(54, 78, 104, 0.92) 0%", "rgba(84, 112, 146, 0.84) 100%"]);
    expect(colourAtParam(stops, 0.5)).toBe("rgba(68, 94, 124, 0.88)");
  });
});

describe("layerPaintOver says what a layer paints at a place, and where it is absent", () => {
  // The sidebar's own brightest highlight, and the bar's own band on a
  // 320px panel: from the drawn bar's inboard edge to the panel's edge. The
  // bar width is the measured one, imported, never retyped.
  const HIGHLIGHT = "radial-gradient(ellipse 60% 34% at 82% 6%, rgba(160, 195, 235, 0.22) 0%, transparent 64%)";
  const BAR: Region = { x0: (320 - SCROLLBAR_THIN_PX) / 320, x1: 1, y0: 0, y1: 0.02 };
  const PANEL: Box = { w: 320, h: 900 };

  it("GROUND: the same layer paints at its own centre", () => {
    const { stops, canBeAbsent } = layerPaintOver(HIGHLIGHT, PANEL, {
      x0: 0.81,
      x1: 0.83,
      y0: 0.05,
      y1: 0.07,
    });
    expect(stops).toContain("rgba(160, 195, 235, 0.22)");
    expect(canBeAbsent).toBe(false);
  });

  it("paints at the bar, but far weaker than its own peak", () => {
    const { stops, canBeAbsent } = layerPaintOver(HIGHLIGHT, PANEL, BAR);
    expect(stops).not.toEqual([]);
    expect(canBeAbsent).toBe(false);
    for (const stop of stops) {
      expect(paintAlpha(stop), `${stop} is at or above the peak at the bar`).toBeLessThan(0.22);
    }
  });

  it("does not reach the bar at all when its ramp dies first", () => {
    // The `::before`'s other two radials, at 12% and at 70% of the width.
    // Both die before the bar's band, and a reader that stacked them there
    // would report a corner lighter than the panel can paint.
    for (const layer of [
      "radial-gradient(ellipse 34% 22% at 12% 24%, rgba(130, 165, 208, 0.16) 0%, transparent 60%)",
      "radial-gradient(ellipse 40% 26% at 70% 52%, rgba(96, 132, 174, 0.12) 0%, transparent 62%)",
    ]) {
      const { stops, canBeAbsent } = layerPaintOver(layer, PANEL, {
        x0: BAR.x0,
        x1: 1,
        y0: 0,
        y1: 1,
      });
      expect(stops, `${layer} paints at the bar`).toEqual([]);
      expect(canBeAbsent).toBe(true);
    }
  });

  it("calls a layer absent where its ramp has run out, and present where it has not", () => {
    // The `::before`'s bottom shadow: opaque at the foot, gone above 46%.
    const shadow = "linear-gradient(to top, rgba(28, 44, 64, 0.28) 0%, transparent 46%)";
    const foot = layerPaintOver(shadow, PANEL, { ...BAR, y0: 0.95, y1: 1 });
    expect(foot.stops).not.toEqual([]);
    expect(foot.canBeAbsent, "the shadow is being offered as absent at the foot").toBe(false);
    const top = layerPaintOver(shadow, PANEL, { ...BAR, y0: 0, y1: 0.1 });
    expect(top.stops).toEqual([]);
    expect(top.canBeAbsent, "the shadow is being painted at the top of the panel").toBe(true);
    // And a band that straddles the ramp's end is both.
    const across = layerPaintOver(shadow, PANEL, { ...BAR, y0: 0.4, y1: 0.7 });
    expect(across.stops).not.toEqual([]);
    expect(across.canBeAbsent).toBe(true);
  });

  it("never calls an opaque gradient absent", () => {
    // Every stop paints, so this layer covers its whole box. Offering it as
    // absent would put whatever is under the surface into the measurement
    // through a surface that never lets it through.
    const base =
      "linear-gradient(to top right, rgba(40, 58, 80, 0.95) 0%, rgba(54, 78, 104, 0.92) 48%, rgba(84, 112, 146, 0.84) 100%)";
    const { stops, canBeAbsent } = layerPaintOver(base, PANEL, BAR);
    expect(stops).not.toEqual([]);
    expect(canBeAbsent).toBe(false);
  });
});

/* ------------------------------------------------------------------ *
 * 10. paintLayersBehind: the whole stack behind a bar, veil included.
 *
 * The layer that is hardest to see is the one rendered INSIDE a surface
 * rather than declared on it: the side panels put a mist canvas and a veil
 * between their own background and their pseudo-elements. Nothing else in
 * the suite exercises that branch, because the component that renders it
 * needs a WebGL context jsdom does not have - so it is exercised here, on a
 * document written for the purpose.
 * ------------------------------------------------------------------ */

describe("paintLayersBehind walks the whole stack behind a scroller", () => {
  const STACK = [
    ".surface { position: relative; background: rgb(40, 58, 80); }",
    ".veil { position: absolute; inset: 0; background: rgba(84, 112, 146, 0.72); }",
    ".not-a-veil { position: absolute; inset: 4px; background: rgb(255, 0, 0); }",
    ".sibling { position: static; background: rgb(0, 255, 0); }",
    ".surface::before { content: ''; position: absolute; inset: 0; background: rgba(160, 195, 235, 0.22); opacity: 0.9; }",
    ".surface::after { content: ''; position: absolute; inset: 0; left: auto; width: 46px; background: rgba(200, 216, 236, 0.15); mix-blend-mode: screen; }",
  ].join("\n");

  function stack(): { root: Root; scroller: Element } {
    const surface = elementWith("surface");
    for (const cls of ["veil", "not-a-veil", "sibling"]) {
      const child = document.createElement("div");
      child.className = cls;
      surface.appendChild(child);
    }
    const scroller = document.createElement("div");
    scroller.className = "scroller";
    surface.appendChild(scroller);
    return { root: parseSheet(STACK), scroller };
  }

  it("finds the surface, its veil and both pseudo-elements, in paint order", () => {
    const { root, scroller } = stack();
    const painted = paintLayersBehind(root, scroller, new Map());
    expect(painted, "nothing paints behind the scroller").not.toBeNull();
    expect(painted?.surfaceRule).toBe(".surface");
    expect(painted?.layers.map((l) => l.source)).toEqual([
      ".surface",
      ".veil",
      ".surface::before",
      ".surface::after",
    ]);
  });

  it("GROUND: a box that is not a full-bleed absolute child is not a veil", () => {
    // `.not-a-veil` is absolute but inset by 4px, `.sibling` paints but is
    // static. A reader that took any painted descendant would find both, and
    // the red layer would then be in every measurement.
    const { root, scroller } = stack();
    const sources = paintLayersBehind(root, scroller, new Map())?.layers.map((l) => l.source);
    expect(sources).not.toContain(".not-a-veil");
    expect(sources).not.toContain(".sibling");
  });

  it("carries each layer's own opacity, blend mode and value", () => {
    const { root, scroller } = stack();
    const layers = paintLayersBehind(root, scroller, new Map())?.layers ?? [];
    const before = layers.find((l) => l.source === ".surface::before");
    const after = layers.find((l) => l.source === ".surface::after");
    expect(before?.opacity).toBe(0.9);
    expect(before?.screen).toBe(false);
    expect(before?.value).toBe("rgba(160, 195, 235, 0.22)");
    expect(after?.opacity).toBe(1);
    expect(after?.screen).toBe(true);
  });

  it("places a one-sided box as a band and a full-bleed one as the whole surface", () => {
    // `left: auto; width: 46px` is a band down the RIGHT edge - which is
    // where a vertical scrollbar is, so reading it as full-bleed would lift
    // the ground across the whole panel instead of only under the bar.
    const { root, scroller } = stack();
    const layers = paintLayersBehind(root, scroller, new Map())?.layers ?? [];
    expect(layers.find((l) => l.source === ".surface::after")?.anchor).toEqual({
      edge: "right",
      bandPx: 46,
    });
    expect(layers.find((l) => l.source === ".surface::before")?.anchor).toEqual({ edge: "full" });
    expect(layers.find((l) => l.source === ".veil")?.anchor).toEqual({ edge: "full" });
  });

  it("refuses a box whose placement it cannot read", () => {
    const surface = elementWith("odd");
    const scroller = document.createElement("div");
    surface.appendChild(scroller);
    const root = parseSheet(
      ".odd { background: rgb(1, 2, 3); }\n" +
        ".odd::before { content: ''; position: absolute; left: 20px; background: rgb(4, 5, 6); }",
    );
    expect(() => paintLayersBehind(root, scroller, new Map())).toThrow(/does not speak/);
  });

  it("returns nothing when no ancestor paints", () => {
    const lonely = elementWith("lonely");
    expect(paintLayersBehind(parseSheet(STACK), lonely, new Map())).toBeNull();
  });
});

describe("backgroundLayers turns a value into the layers the engine paints", () => {
  const table = rootTokens(parseSheet(":root { --a: rgb(1, 2, 3); }"));

  it("hands them back BOTTOM first, which is the reverse of how they are written", () => {
    expect(backgroundLayers("rgb(9, 9, 9), rgb(5, 5, 5), rgb(1, 1, 1)", table)).toEqual([
      "rgb(1, 1, 1)",
      "rgb(5, 5, 5)",
      "rgb(9, 9, 9)",
    ]);
  });

  it("resolves tokens and does not split inside a function", () => {
    expect(
      backgroundLayers("linear-gradient(90deg, var(--a), rgba(0, 0, 0, 0.5)), var(--a)", table),
    ).toEqual([
      "rgb(1, 2, 3)",
      "linear-gradient(90deg, rgb(1, 2, 3), rgba(0, 0, 0, 0.5))",
    ]);
  });
});

/* ------------------------------------------------------------------ *
 * 9. Where a scrollbar's column comes from.
 * ------------------------------------------------------------------ */

describe("lengthPx turns the lengths the sheet writes into pixels", () => {
  it("reads px, rem and a bare zero", () => {
    expect(lengthPx("12px", 16)).toBe(12);
    expect(lengthPx("0.55rem", 16)).toBeCloseTo(8.8, 10);
    expect(lengthPx("0", 16)).toBe(0);
    // The rem is the sheet's, not a constant: a smaller root font makes the
    // same declaration a smaller length.
    expect(lengthPx("0.55rem", 14)).toBeCloseTo(7.7, 10);
  });

  it("reads the calc() Tailwind's spacing scale compiles to, in either order", () => {
    expect(lengthPx("calc(0.25rem * 3)", 16)).toBeCloseTo(12, 10);
    expect(lengthPx("calc(3 * 0.25rem)", 16)).toBeCloseTo(12, 10);
    expect(lengthPx("calc(0.25rem * 6)", 16)).toBeCloseTo(24, 10);
  });

  it("refuses what it cannot turn into pixels instead of answering zero", () => {
    // GROUND for every measurement built on it: a reader that quietly
    // returned 0 for an unresolved token would move a measurement band and
    // report nothing, which is the whole failure mode here.
    expect(() => lengthPx("var(--spacing)", 16)).toThrow(/cannot turn into pixels/);
    expect(() => lengthPx("2em", 16)).toThrow(/cannot turn into pixels/);
    expect(() => lengthPx("calc(100% - 2px)", 16)).toThrow(/cannot turn into pixels/);
    expect(() => lengthPx("auto", 16)).toThrow(/cannot turn into pixels/);
  });
});

describe("clipsAndMasksOn finds anything taking a bite out of a box", () => {
  it("finds a clip and a mask, in both spellings, and names them", () => {
    const root = parseSheet(
      ".a::before,\n.a::after { clip-path: inset(0 8px 0 0); }\n" +
        ".a::after { -webkit-mask-image: linear-gradient(90deg, #000, transparent); }\n" +
        ".b { mask: url(#m); }\n" +
        ".c { -webkit-clip-path: circle(40%); }\n",
    );
    expect(clipsAndMasksOn(root, ".a::before")).toEqual(["clip-path: inset(0 8px 0 0)"]);
    expect(clipsAndMasksOn(root, ".a::after")).toEqual([
      "clip-path: inset(0 8px 0 0)",
      "-webkit-mask-image: linear-gradient(90deg, #000, transparent)",
    ]);
    expect(clipsAndMasksOn(root, ".b")).toEqual(["mask: url(#m)"]);
    expect(clipsAndMasksOn(root, ".c")).toEqual(["-webkit-clip-path: circle(40%)"]);
  });

  it("does not call the initial value a cut, and answers a selector nothing cuts", () => {
    // GROUND: the two ways an honest empty answer arises, so "nothing cuts
    // this box" is not the same sentence as "this reader found nothing".
    const root = parseSheet(".a { clip-path: none; }\n.b { background: #fff; }\n");
    expect(clipsAndMasksOn(root, ".a")).toEqual([]);
    expect(clipsAndMasksOn(root, ".b")).toEqual([]);
    expect(clipsAndMasksOn(root, ".nothing-here")).toEqual([]);
  });
});

describe("insetRightPx walks a scroller's column out of the rendered tree", () => {
  /* The probe sheet. Two of these class names are deliberately not
   * Tailwind's. Tailwind scans this file as raw text and emits a rule for
   * every utility name it reads, so the doubled-padding and right-margin
   * probes, written as Tailwind names, put two rules nothing renders into
   * the stylesheet users download. `px-3` stays because the app carries it;
   * the two that were dead are spelled `probe-*`, which Tailwind generates
   * nothing for, and this sheet declares them itself, so the reader is asked
   * exactly the same question. Their Tailwind spellings are not written
   * anywhere in this file, or the rules would come straight back. */
  const SHEET =
    ":root { --spacing: 0.25rem; }\n" +
    ".px-3 { padding-inline: calc(var(--spacing) * 3); }\n" +
    ".probe-pad-6 { padding-inline: calc(var(--spacing) * 6); }\n" +
    ".pad-box { padding: 1px 7px 1px 2px; }\n" +
    ".probe-mr-5 { margin-right: 5px; }\n" +
    ".edge { border-right: 2px solid #000; }\n" +
    ".hairline-no-style { border-right-width: 9px; }\n";

  /** surface > wrapper > scroller, with the classes each carries. */
  function tree(surface: string, wrapper: string, scroller: string) {
    const host = elementWith(surface);
    const mid = document.createElement("div");
    mid.className = wrapper;
    const leaf = document.createElement("div");
    leaf.className = scroller;
    mid.appendChild(leaf);
    host.appendChild(mid);
    return { host, leaf };
  }

  const root = () => parseSheet(SHEET);
  const table = () => rootTokens(parseSheet(SHEET));

  it("adds up the padding between the scroller and the surface", () => {
    const { host, leaf } = tree("surface", "px-3", "scroller");
    expect(insetRightPx(root(), leaf, host, 16, table())).toBeCloseTo(12, 10);
  });

  it("follows the padding rather than reporting a number of its own", () => {
    // The sensitivity that makes this a measurement: the same tree with a
    // wider wrapper answers wider, by exactly the difference.
    const { host, leaf } = tree("surface", "probe-pad-6", "scroller");
    expect(insetRightPx(root(), leaf, host, 16, table())).toBeCloseTo(24, 10);
  });

  it("reports a flush scroller as flush", () => {
    // GROUND: it is not a reader that always finds something. With no
    // padding, margin or border anywhere the two right edges coincide.
    const { host, leaf } = tree("surface", "wrapper", "scroller");
    expect(insetRightPx(root(), leaf, host, 16, table())).toBe(0);
  });

  it("adds margins and borders, and takes the right side of a box shorthand", () => {
    const { host, leaf } = tree("surface pad-box", "px-3 edge", "scroller probe-mr-5");
    // 5 (the scroller's own margin) + 12 (px-3) + 2 (the wrapper's border)
    // + 7 (the surface's padding, right side of a four-part shorthand).
    expect(insetRightPx(root(), leaf, host, 16, table())).toBeCloseTo(26, 10);
  });

  it("does not count the surface's OWN border", () => {
    // The decoration whose ground is being measured is drawn in the
    // surface's padding box, so the band is measured from that edge; adding
    // the border would move it a pixel further from the panel's lit edge,
    // which is the kinder direction.
    const { host, leaf } = tree("surface edge", "wrapper", "scroller");
    expect(insetRightPx(root(), leaf, host, 16, table())).toBe(0);
  });

  it("counts the scroller's OWN border, because the bar is drawn inside it", () => {
    // A scrollbar sits in its scroller's padding box, so a border on the
    // scroller itself pushes the bar inboard exactly like a wrapper's
    // padding does. GROUND: the same tree without that class reads 0.
    const { host, leaf } = tree("surface", "wrapper", "scroller edge");
    expect(insetRightPx(root(), leaf, host, 16, table())).toBe(2);
    leaf.className = "scroller";
    expect(insetRightPx(root(), leaf, host, 16, table())).toBe(0);
  });

  it("gives a border with no style no width, which is what the engine draws", () => {
    const { host, leaf } = tree("surface", "hairline-no-style", "scroller");
    expect(insetRightPx(root(), leaf, host, 16, table())).toBe(0);
  });

  it("lets an inline style outrank the sheet, which is where the app writes its border", () => {
    const { host, leaf } = tree("surface", "wrapper", "scroller");
    const wrapper = host.firstElementChild as HTMLElement;
    wrapper.setAttribute("style", "border-right: 1px solid var(--whatever);");
    // GROUND: the same tree without that attribute reads 0, so the pixel is
    // the attribute's and not the reader's.
    expect(insetRightPx(root(), leaf, host, 16, table())).toBe(1);
    wrapper.removeAttribute("style");
    expect(insetRightPx(root(), leaf, host, 16, table())).toBe(0);
  });

  it("refuses two elements that are not on one path", () => {
    const a = elementWith("surface");
    const b = elementWith("other");
    expect(() => insetRightPx(root(), b, a, 16, table())).toThrow(/not on one path/);
  });
});

/* ------------------------------------------------------------------ *
 * fontSizePx reads a size through the cascade and through inheritance.
 * ------------------------------------------------------------------ */

describe("fontSizePx reads the size the words render at", () => {
  // Every case below expects a different number, so a reader that answered
  // one value for everything fails all but one of them.
  const sheet = parseSheet(`
    :root { --chrome: 13px; }
    .chrome { font-size: var(--chrome); }
    .label { font-size: 12px; }
    .bigger { font-size: 1.5em; }
    .half { font-size: 50%; }
    .passes { font-size: inherit; }
    button { font: inherit; }
    .shorthand { font: 700 14px serif; }
    .loud { font-size: 15px !important; }
  `);
  const table = rootTokens(sheet);
  const size = (el: Element) => fontSizePx(sheet, table, 16, el);

  /** `child` inside a fresh box carrying `parentClass`. */
  function inside(parentClass: string, child: Element): Element {
    elementWith(parentClass).appendChild(child);
    return child;
  }

  it("reads the element's own declaration, var() resolved", () => {
    expect(size(elementWith("chrome"))).toBe(13);
    expect(size(elementWith("label"))).toBe(12);
  });

  it("inherits from the nearest ancestor that declares a size", () => {
    expect(size(inside("label", document.createElement("span")))).toBe(12);
  });

  it("passes font: inherit and font-size: inherit up to the parent", () => {
    // The preflight's shorthand is how a button gets its parent's size.
    expect(size(inside("chrome", document.createElement("button")))).toBe(13);
    const passing = document.createElement("span");
    passing.className = "passes";
    expect(size(inside("label", passing))).toBe(12);
  });

  it("takes em and % against the parent's size", () => {
    const bigger = document.createElement("span");
    bigger.className = "bigger";
    expect(size(inside("label", bigger))).toBe(18);
    const half = document.createElement("span");
    half.className = "half";
    expect(size(inside("label", half))).toBe(6);
  });

  it("is at the root size when nothing up the tree declares one", () => {
    expect(size(elementWith("undeclared"))).toBe(16);
  });

  it("refuses a font shorthand it cannot take apart", () => {
    expect(() => size(elementWith("shorthand"))).toThrow(/does not take apart/);
  });

  it("reads an inline font-size over a sheet rule, unless the rule is important", () => {
    // A label wrapped in a span with its own inline size is sized there,
    // whatever class the span also wears, and its children inherit it.
    const sized = elementWith("chrome");
    sized.setAttribute("style", "font-size: 11px");
    expect(size(sized)).toBe(11);
    const child = document.createElement("span");
    sized.appendChild(child);
    expect(size(child)).toBe(11);
    // An important sheet rule outranks the inline style, as in the engine.
    const loud = elementWith("loud");
    loud.setAttribute("style", "font-size: 11px");
    expect(size(loud)).toBe(15);
    // And an inline shorthand is refused like the sheet's.
    const shorthand = elementWith("chrome");
    shorthand.setAttribute("style", "font: 700 15px serif");
    expect(() => size(shorthand)).toThrow(/does not take apart/);
  });
});

/* ------------------------------------------------------------------ *
 * inkOf reads a colour through the cascade, through inheritance, and
 * resolves each var() where the engine does.
 * ------------------------------------------------------------------ */

describe("inkOf reads the colour words are painted in", () => {
  // Every case expects a different colour, so a reader that answered one
  // value for everything fails all but one of them. Each colour is named
  // once, here, and the sheet below is written from the names.
  const INK = {
    root: "#eaeef3",
    panel: "#3a4a5c",
    own: "#111111",
    inline: "#222222",
    loud: "#333333",
    fallback: "#444444",
    initial: "#555555",
    setter: "#666666",
  } as const;
  const sheet = parseSheet(`
    :root { --probe-fg: ${INK.root}; }
    .probe-host { color: var(--probe-fg); }
    .probe-panel { --probe-fg: ${INK.panel}; }
    .probe-own { color: var(--probe-fg); }
    .probe-ink { color: ${INK.own}; }
    .probe-loud { color: ${INK.loud} !important; }
    .probe-passes { color: inherit; }
    .probe-unset { color: unset; }
    .probe-current { color: currentColor; }
    .probe-fallback { color: var(--probe-nothing, ${INK.fallback}); }
    .probe-broken { color: var(--probe-nothing); }
    .probe-chain { --probe-deep: var(--probe-fg); color: var(--probe-deep); }
    .probe-outer { --probe-deep: var(--probe-fg); }
    .probe-deep-reader { color: var(--probe-deep); }
    @property --probe-local { syntax: "<color>"; inherits: false; initial-value: ${INK.initial}; }
    .probe-setter { --probe-local: ${INK.setter}; }
    .probe-reader { color: var(--probe-local); }
    .probe-reverts { color: revert; }
  `);

  /** A chain of boxes, outermost first, each carrying one class; returns the
   *  innermost. */
  function nested(...classes: string[]): Element {
    let parent: Element = elementWith(classes[0]);
    for (const cls of classes.slice(1)) {
      const child = document.createElement("div");
      child.className = cls;
      parent.appendChild(child);
      parent = child;
    }
    return parent;
  }

  it("reads the element's own colour, says it decided it there, and keeps what it wrote", () => {
    const own = elementWith("probe-ink");
    expect(inkOf(sheet, own)).toEqual({ colour: INK.own, from: own, written: INK.own });
  });

  it("reads an ink dimmed by an opacity modifier as the mix, not as its fallback", () => {
    // What Tailwind compiles `text-<token>/40` to: the token at full
    // strength, then the mix inside an `@supports` block the app's engine
    // satisfies. The mix is what the screen shows.
    const dimmed = parseSheet(`
      :root { --probe-fg: ${INK.root}; }
      .probe-dim {
        color: var(--probe-fg);
        @supports (color: color-mix(in lab, red, red)) {
          color: color-mix(in oklab, var(--probe-fg) 40%, transparent);
        }
      }
      .probe-plain { color: var(--probe-fg); }
      @media (min-width: 1px) { .probe-plain { color: ${INK.own}; } }
    `);
    const line = elementWith("probe-dim");
    expect(inkOf(dimmed, line)).toEqual({
      colour: `color-mix(in oklab, ${INK.root} 40%, transparent)`,
      from: line,
      written: "color-mix(in oklab, var(--probe-fg) 40%, transparent)",
    });
    // GROUND: a colour under a media query is still not taken, so the
    // supports blocks are the only condition read as holding.
    expect(inkOf(dimmed, elementWith("probe-plain")).colour).toBe(INK.root);
  });

  it("inherits from the nearest ancestor that declares one", () => {
    const line = nested("probe-ink", "");
    expect(inkOf(sheet, line))
      .toEqual({ colour: INK.own, from: line.parentElement, written: INK.own });
  });

  it("resolves a var() where the colour is declared, so a redefinition below it "
     + "never reaches an inherited colour", () => {
    // The right panel's trap, in four boxes: the host resolves the token
    // against the root, the panel redefines the token, and a line inside the
    // panel with no colour of its own keeps the host's result...
    const inherited = nested("probe-host", "probe-panel", "");
    expect(inkOf(sheet, inherited).colour).toBe(INK.root);
    expect(inkOf(sheet, inherited).from?.className).toBe("probe-host");
    // ...while a line that names the token itself resolves it inside the
    // panel and gets the panel's value, the reference kept as written.
    const named = nested("probe-host", "probe-panel", "probe-own");
    expect(inkOf(sheet, named))
      .toEqual({ colour: INK.panel, from: named, written: "var(--probe-fg)" });
    // And the same line outside the panel gets the root's.
    expect(inkOf(sheet, nested("probe-own")).colour).toBe(INK.root);
  });

  it("resolves a custom property that refers to another at the element that declares it", () => {
    const chained = nested("probe-panel", "probe-chain");
    expect(inkOf(sheet, chained).colour).toBe(INK.panel);
    expect(customPropertyAt(sheet, chained, "--probe-deep")).toBe(INK.panel);
    // Declared OUTSIDE the panel, the reference was already resolved against
    // the root when it got there, so the panel's redefinition in between
    // does not change what the line reads.
    const outer = nested("probe-outer", "probe-panel", "probe-deep-reader");
    expect(customPropertyAt(sheet, outer, "--probe-deep")).toBe(INK.root);
    expect(inkOf(sheet, outer).colour).toBe(INK.root);
    expect(inkOf(sheet, outer).from).toBe(outer);
  });

  it("reads an inline colour over a sheet rule, unless the rule is important", () => {
    const inline = elementWith("probe-ink");
    inline.setAttribute("style", `color: ${INK.inline}`);
    expect(inkOf(sheet, inline).colour).toBe(INK.inline);
    const loud = elementWith("probe-loud");
    loud.setAttribute("style", `color: ${INK.inline}`);
    expect(inkOf(sheet, loud).colour).toBe(INK.loud);
    // An inline var() is resolved like a sheet one.
    const token = nested("probe-panel", "");
    token.setAttribute("style", "color: var(--probe-fg)");
    expect(inkOf(sheet, token))
      .toEqual({ colour: INK.panel, from: token, written: "var(--probe-fg)" });
  });

  it("passes inherit, unset and currentcolor up to the parent", () => {
    for (const cls of ["probe-passes", "probe-unset", "probe-current"]) {
      const line = nested("probe-ink", cls);
      expect(inkOf(sheet, line), cls)
        .toEqual({ colour: INK.own, from: line.parentElement, written: INK.own });
    }
  });

  it("takes a var()'s fallback, and inherits when a var() with none resolves to nothing", () => {
    expect(inkOf(sheet, elementWith("probe-fallback")).colour).toBe(INK.fallback);
    const broken = nested("probe-ink", "probe-broken");
    expect(inkOf(sheet, broken))
      .toEqual({ colour: INK.own, from: broken.parentElement, written: INK.own });
    expect(substituteAt(sheet, broken, "var(--probe-nothing)")).toBeNull();
    expect(customPropertyAt(sheet, broken, "--probe-nothing")).toBeNull();
  });

  it("stops a registered property that does not inherit at its own element", () => {
    const below = nested("probe-setter", "probe-reader");
    expect(inkOf(sheet, below).colour).toBe(INK.initial);
    expect(inkOf(sheet, elementWith("probe-setter probe-reader")).colour).toBe(INK.setter);
  });

  it("is the initial value when nothing up the tree declares a colour", () => {
    expect(inkOf(sheet, elementWith("probe-none")))
      .toEqual({ colour: "canvastext", from: null, written: null });
  });

  it("refuses a keyword it does not resolve", () => {
    expect(() => inkOf(sheet, elementWith("probe-reverts"))).toThrow(/does not resolve/);
  });

  it("reads each element once across a sweep that shares a memo", () => {
    const memo = new Map();
    const line = nested("probe-host", "probe-panel", "");
    const first = inkOf(sheet, line, memo);
    // Every box on the way up was read and kept.
    expect(memo.get(line)).toBe(first);
    expect(memo.has(line.parentElement!)).toBe(true);
    expect(inkOf(sheet, line, memo)).toBe(first);
  });
});

/* ------------------------------------------------------------------ *
 * The readers the right panel's contrast rows stand on: a placeholder's
 * ink, an element's own opacity and paint, the stack between a surface and
 * a line, and the arithmetic that lays the line over it.
 * ------------------------------------------------------------------ */

describe("placeholderInkOf reads a placeholder the way the engine paints it", () => {
  const INK = { field: "#101010", own: "#202020", faded: "#303030" } as const;
  const sheet = parseSheet(`
    .probe-field { color: ${INK.field}; }
    ::placeholder { opacity: 1; }
    @supports (color: red) {
      ::placeholder { color: currentColor; }
      @supports (color: color-mix(in lab, red, red)) {
        ::placeholder { color: color-mix(in oklab, currentcolor 50%, transparent); }
      }
    }
    .probe-owns::placeholder { color: ${INK.own}; }
    .probe-faded::placeholder { color: ${INK.faded}; opacity: .6; }
    @media (min-width: 1px) {
      .probe-windowed::placeholder { color: #404040; }
    }
  `);
  const field = (cls: string): Element => {
    const el = elementWith(cls);
    const input = document.createElement("input");
    input.className = cls;
    el.appendChild(input);
    return input;
  };

  it("takes the preflight's half-strength copy of the field's own ink, inside @supports", () => {
    const input = field("probe-field");
    expect(placeholderInkOf(sheet, input).colour)
      .toBe(`color-mix(in oklab, ${INK.field} 50%, transparent)`);
    expect(paintChannels(placeholderInkOf(sheet, input).colour))
      .toEqual({ rgb: parseRgbColour(INK.field), a: 0.5 });
  });

  it("lets a rule of the field's own win over the preflight's", () => {
    expect(placeholderInkOf(sheet, field("probe-field probe-owns")))
      .toEqual(expect.objectContaining({ colour: INK.own, written: INK.own }));
  });

  it("lays a placeholder's own opacity into its ink", () => {
    const ink = placeholderInkOf(sheet, field("probe-field probe-faded")).colour;
    expect(paintChannels(ink).a).toBeCloseTo(0.6, 10);
    expect(paintChannels(ink).rgb).toEqual(parseRgbColour(INK.faded));
  });

  it("does not take a rule under a condition about the window", () => {
    expect(placeholderInkOf(sheet, field("probe-field probe-windowed")).colour)
      .toBe(`color-mix(in oklab, ${INK.field} 50%, transparent)`);
  });
});

describe("parseOpacity and ownOpacity read how much of an element is painted", () => {
  const sheet = parseSheet(`
    .probe-seventy { opacity: 70%; }
    .probe-half { opacity: .5; }
    .probe-token { --probe-o: 0.25; opacity: var(--probe-o); }
    .probe-loud { opacity: 0.4 !important; }
  `);

  it("reads a number and a percentage, and clamps the way the engine clamps", () => {
    expect(parseOpacity("0.7")).toBe(0.7);
    expect(parseOpacity("35%")).toBe(0.35);
    expect(parseOpacity("1.5")).toBe(1);
    expect(parseOpacity("-1")).toBe(0);
    expect(() => parseOpacity("half")).toThrow(/not an opacity/);
  });

  it("reads the sheet, a var() in it, and the inline style over both", () => {
    expect(ownOpacity(sheet, elementWith("probe-seventy"))).toBe(0.7);
    expect(ownOpacity(sheet, elementWith("probe-token"))).toBe(0.25);
    expect(ownOpacity(sheet, elementWith("probe-none"))).toBe(1);
    const inline = elementWith("probe-half");
    inline.setAttribute("style", "opacity: 0.9");
    expect(ownOpacity(sheet, inline)).toBe(0.9);
    const loud = elementWith("probe-loud");
    loud.setAttribute("style", "opacity: 0.9");
    expect(ownOpacity(sheet, loud)).toBe(0.4);
  });
});

describe("ownPaints and stackOf read what lies between a surface and a line", () => {
  const sheet = parseSheet(`
    .probe-card { background: rgba(255, 255, 255, 0.2); }
    .probe-tint { background-color: #c36a72; }
    .probe-ramp { background: linear-gradient(180deg, #111111, #eeeeee); }
    .probe-none { background: none; }
    .probe-picture { background: url(x.png); }
    .probe-dim { opacity: 0.5; }
    .probe-mixed { background-color: color-mix(in oklab, #3e72b0 16%, transparent); }
  `);

  it("reads a flat fill, a gradient's stops, a mix, nothing, and refuses a picture", () => {
    expect(ownPaints(sheet, elementWith("probe-card"))).toEqual(["rgba(255, 255, 255, 0.2)"]);
    expect(ownPaints(sheet, elementWith("probe-tint"))).toEqual(["#c36a72"]);
    expect(ownPaints(sheet, elementWith("probe-ramp"))).toEqual(["#111111", "#eeeeee"]);
    expect(ownPaints(sheet, elementWith("probe-mixed")))
      .toEqual(["color-mix(in oklab, #3e72b0 16%, transparent)"]);
    expect(ownPaints(sheet, elementWith("probe-none"))).toEqual([]);
    expect(ownPaints(sheet, elementWith("probe-nothing-here"))).toEqual([]);
    expect(() => ownPaints(sheet, elementWith("probe-picture"))).toThrow(/image/);
    const inline = elementWith("probe-card");
    inline.setAttribute("style", "background-color: #123456");
    expect(ownPaints(sheet, inline)).toEqual(["#123456"]);
  });

  it("paints currentcolor in the element's own ink, and refuses a colour name it does not know", () => {
    // GROUND: the words a layer carries besides its colour are not colours,
    // and are not refused.
    const placed = elementWith("");
    placed.setAttribute("style", "background: #fff no-repeat center / cover");
    expect(ownPaints(sheet, placed)).toEqual(["#fff"]);
    // `currentcolor` is the text colour of the element that paints it.
    const current = elementWith("");
    current.setAttribute("style", "color: #abcdef; background: currentColor");
    expect(ownPaints(sheet, current)).toEqual(["#abcdef"]);
    // A named colour this does not know, flat or in a gradient, is a ground
    // it cannot put a value to: it throws rather than leaving it out.
    for (const background of ["red", "canvas", "linear-gradient(180deg, red, #eeeeee)"]) {
      const named = elementWith("");
      named.setAttribute("style", `background: ${background}`);
      expect(() => ownPaints(sheet, named), background).toThrow(/colour name/);
    }
  });

  it("lists the stack outermost first, the surface left out, each with its own opacity", () => {
    const surface = elementWith("");
    const card = document.createElement("div");
    card.className = "probe-card probe-dim";
    const line = document.createElement("p");
    line.className = "probe-tint";
    card.appendChild(line);
    surface.appendChild(card);
    const steps = stackOf(sheet, surface, line);
    expect(steps.map((s) => s.element)).toEqual([card, line]);
    expect(steps.map((s) => s.opacity)).toEqual([0.5, 1]);
    expect(steps.map((s) => s.paints)).toEqual([["rgba(255, 255, 255, 0.2)"], ["#c36a72"]]);
  });

  it("refuses a surface that is itself translucent, and an element outside it", () => {
    const surface = elementWith("probe-dim");
    const line = document.createElement("p");
    surface.appendChild(line);
    expect(() => stackOf(sheet, surface, line)).toThrow(/two-stop ground/);
    expect(() => stackOf(sheet, elementWith(""), line)).toThrow(/outside its surface/);
  });
});

describe("paintChannels, wordsOverStack and worstRatioOverStack lay words over a stack", () => {
  const BLACK = { r: 0, g: 0, b: 0 };

  it("reads a colour mixed with transparent as that colour at the share, either way round", () => {
    expect(paintChannels("color-mix(in oklab, #204060 85%, transparent)"))
      .toEqual({ rgb: { r: 32, g: 64, b: 96 }, a: 0.85 });
    expect(paintChannels("color-mix(in srgb, transparent 25%, rgba(32, 64, 96, 0.8))").a)
      .toBeCloseTo(0.6, 10);
    expect(paintChannels("transparent")).toEqual({ rgb: BLACK, a: 0 });
    expect(() => paintChannels("color-mix(in oklab, #204060, #ffffff 5%)")).toThrow(/transparent/);
  });

  it("applies an opacity to what the element paints, the words included", () => {
    // Black words at half opacity over white: the words read mid-grey, the
    // ground stays white.
    const half = wordsOverStack("#000000", [{ opacity: 0.5, paint: null }], WHITE);
    expect(half).toEqual({ words: { r: 128, g: 128, b: 128 }, ground: WHITE });
    // A white card at half opacity with black words, over black: the
    // element's paint and its words go down as one group, so where the words
    // are nothing of the card shows, and around them the card is half there.
    const group = wordsOverStack("#000000", [{ opacity: 0.5, paint: "#ffffff" }], BLACK);
    expect(group).toEqual({ words: BLACK, ground: { r: 128, g: 128, b: 128 } });
    // Nested: a quarter of the words' ink is left after two halves.
    const nestedHalf = wordsOverStack("#000000",
      [{ opacity: 0.5, paint: null }, { opacity: 0.5, paint: null }], WHITE);
    expect(nestedHalf.words).toEqual({ r: 191, g: 191, b: 191 });
  });

  it("takes the worst of every paint a step can lay down, over every base", () => {
    const worst = worstRatioOverStack("#777777",
      [{ opacity: 1, paints: ["#ffffff", "#888888"] }], [WHITE, BLACK]);
    expect(worst.ground).toEqual({ r: 136, g: 136, b: 136 });
    expect(worst.ratio).toBeCloseTo(contrastRatio({ r: 119, g: 119, b: 119 }, worst.ground), 10);
    // The same ink on the lighter paint alone reads well above that.
    expect(worstRatioOverStack("#777777", [{ opacity: 1, paints: ["#ffffff"] }], [WHITE]).ratio)
      .toBeGreaterThan(worst.ratio + 3);
  });
});
