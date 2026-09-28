/**
 * motionTokens.test.ts - the motion guard, over the COMPILED sheet.
 *
 * Three rules from the design reference are held here and nowhere else:
 * KURAL 5.3 (a popup opens at --duration-fast and closes at --duration-exit,
 * and exit is the shorter), the FIX 2 literal rule (180ms and 120ms are
 * written once each, where the tokens are defined) and KURAL 5.6 (under
 * prefers-reduced-motion nothing moves except two named families). The
 * fourth block holds FIX 8: the three hand-written popups share one visual
 * family and answer both media queries the same way.
 *
 * Everything is read out of index.css as Vite and Tailwind emit it, parsed
 * with postcss, and every number is READ from the sheet's own token
 * definitions rather than typed here; a duration retyped in a test is a
 * second copy that lets the sheet and the test drift in lockstep.
 *
 * KURAL 5.6 in particular is not a list of class names. Fifteen hand-written
 * reduced-motion blocks used to name what their authors remembered, and 25
 * of the 78 moving rules were in none of them. The guard is a set
 * difference: A is every rule with a positive transition or animation
 * duration, B is what the reduced-motion media neutralises minus what it
 * re-opens, and A minus B minus the NAMED exceptions has to be empty. A
 * count would let a number be typed and defended; a difference cannot.
 *
 * History, kept because it explains the shape: this file held four tests
 * for five spring presets (deleted 2026-08-09, nothing imported them), then
 * two assertions that read source text moved out on 2026-09-03 (the
 * reduced-motion prop to components/ReducedMotionRoot.test.tsx, the
 * AnimatedList clamp to components/AnimatedListStagger.test.tsx). The last
 * raw read, of index.css for the reduced-transparency switch, went with this
 * rewrite; that claim is made against the parsed compiled sheet below.
 */
import { beforeAll, describe, expect, it } from "vitest";
import type { Root } from "postcss";

import { staggerStep } from "@/lib/motion/stagger";
import { loadGlassRightCss } from "../helpers/glassSurfaceCss";
import {
  NAMED_REDUCED_MOTION_EXCEPTIONS,
  POPUP_SLOTS,
  REDUCED_TRANSPARENCY,
  TW_DURATION,
  UNIVERSAL_RESET,
  UNIVERSAL_RESET_SELECTORS,
  declarationsFor,
  durationUtilities,
  type DurationUtility,
  inMedia,
  infiniteAnimations,
  isNeutralised,
  isReopened,
  movingRules,
  normalizeSelector,
  parseSheet,
  resolveVars,
  resolvedSelectors,
  rootTokens,
  splitTopLevel,
  stillMoving,
  switchOffReaches,
  timeMs,
  timingFunctionOf,
  tokensOf,
  universalResetDeclarations,
} from "../helpers/compiledSheet";

let css = "";
let root: Root;
let table: Map<string, string>;

beforeAll(async () => {
  css = await loadGlassRightCss();
  root = parseSheet(css);
  table = rootTokens(root);
}, 60_000);

/** A `--duration-*` token's value in ms, read from the sheet. */
function tokenMs(name: string): number {
  const raw = table.get(name);
  if (raw === undefined) throw new Error(`the compiled sheet does not declare ${name}`);
  const ms = timeMs(resolveVars(raw, table));
  if (ms === null) throw new Error(`${name} is not a time: ${raw}`);
  return ms;
}

describe("stagger budget", () => {
  it("never lets a long list take longer just for being long", () => {
    const total = (n: number) => staggerStep(n) * n;
    expect(total(40)).toBeLessThanOrEqual(0.4);
    expect(total(200)).toBeLessThanOrEqual(0.4);
  });

  it("keeps a short list step above 0.02s, and at the 0.05s cap", () => {
    // One-sided until KADEME 19a. `staggerStep` is `min(cap, total/count)`
    // and at five items the CAP is what wins, so the lower bound alone let
    // the cap regress freely: at cap 1.0 this returns 0.07 and stays green.
    // The upper side is the cap itself, so pin it here where it binds.
    expect(staggerStep(5)).toBeGreaterThan(0.02);
    expect(staggerStep(5), "the per-item cap moved").toBeCloseTo(0.05, 5);
  });

  it("does not stagger a single item", () => {
    expect(staggerStep(1)).toBe(0);
  });
});

describe("the compiled sheet", () => {
  it("compiled something worth reading", () => {
    // The watchman's watchman: every claim below is read off this text.
    expect(css.length).toBeGreaterThan(1000);
    expect(table.size, "no root tokens were found").toBeGreaterThan(10);
  });
});

/**
 * The effective `animation-duration` for one popup slot in one state, the
 * way the cascade decides it between the slot's own rule and the
 * state-qualified nested rule: !important first, then the more specific
 * selector, then source order.
 */
function effectiveSlotDuration(slot: string, state: "open" | "closed") {
  const candidates = [
    ...declarationsFor(root, table, `[data-slot="${slot}"]`, "animation-duration")
      .map((d) => ({ ...d, blanket: true })),
    ...declarationsFor(root, table, `[data-slot="${slot}"][data-${state}]`, "animation-duration")
      .map((d) => ({ ...d, blanket: false })),
  ];
  const important = candidates.filter((c) => c.important);
  const pool = important.length > 0 ? important : candidates;
  const specific = pool.filter((c) => !c.blanket);
  const chosen = (specific.length > 0 ? specific : pool).at(-1);
  if (!chosen) return null;
  const ms = timeMs(chosen.value);
  if (ms === null) throw new Error(`${slot} ${state}: not a time: ${chosen.value}`);
  return { ms, important: chosen.important, blanket: chosen.blanket };
}

describe("a popup opens at --duration-fast and closes at --duration-exit (KURAL 5.3, T-16)", () => {
  it.each(POPUP_SLOTS)("%s", (slot) => {
    const open = effectiveSlotDuration(slot, "open");
    const closed = effectiveSlotDuration(slot, "closed");
    expect(open, `${slot} has no entry duration`).not.toBeNull();
    expect(closed, `${slot} has no exit duration`).not.toBeNull();

    // Both halves READ from the sheet: the pair is the token pair.
    expect(open!.ms).toBe(tokenMs("--duration-fast"));
    expect(closed!.ms).toBe(tokenMs("--duration-exit"));
    // The one exception to "one gesture, one duration": exit is shorter.
    expect(closed!.ms).toBeLessThan(open!.ms);

    // No blanket cap and no !important: a component may still give itself
    // its own duration, and the reduced-motion answer is universal rather
    // than fought per slot.
    for (const half of [open!, closed!]) {
      expect(half.blanket, `${slot} is capped by a blanket rule`).toBe(false);
      expect(half.important, `${slot} carries !important`).toBe(false);
    }
  });

  it("GROUND: an unknown slot has no duration at all", () => {
    // The reader answers null for a slot the sheet never prices, so the
    // six answers above are found, not manufactured.
    expect(effectiveSlotDuration("no-such-slot", "open")).toBeNull();
  });
});

describe("180ms and 120ms are written once each, where the tokens are defined (FIX 2)", () => {
  it("no declaration outside the two token definitions carries either literal", () => {
    const offenders: string[] = [];
    let uses = 0;
    for (const name of ["--duration-fast", "--duration-exit"]) {
      const literal = table.get(name);
      expect(literal, `${name} is not defined`).toBeDefined();
      expect(timeMs(literal!), `${name} is not a plain time`).not.toBeNull();
      root.walkDecls((decl) => {
        if (decl.prop === name) return;
        if (decl.value.includes(`var(${name})`)) uses += 1;
        if (tokensOf(decl.value).some((t) => t === literal)) {
          const rule = decl.parent;
          const where = rule && rule.type === "rule"
            ? resolvedSelectors(rule).join(", ")
            : String(rule?.type);
          offenders.push(`${where} { ${decl.prop}: ${decl.value} }`);
        }
      });
    }
    // POSITIVE CONTROL: the tokens are in use, so an empty offender list is
    // the sheet reading tokens rather than the walker seeing nothing.
    expect(uses, "nothing references the two tokens").toBeGreaterThan(10);
    expect(offenders, "a duration was written as a number instead of the token").toEqual([]);
  });
});

describe("no component writes its own duration either (FIX 1, the .tsx half)", () => {
  /**
   * The case above holds the STYLESHEET to the token set. This one holds the
   * component tree to it, and the two together are the whole of FIX 1's
   * duration scan.
   *
   * It reads no source text. Tailwind emits `.duration-<x>` into the sheet
   * only when a file in the component tree actually uses that class, so the
   * compiled output IS the inventory of hand-written durations in `.tsx`,
   * obtained through the real pipeline. A component that ships an arbitrary
   * bracketed duration puts the utility here for this case to find. The
   * same mechanism is why no file in this suite spells a numeric duration
   * class out: the scanner would emit it and the guard would be watching
   * its own text.
   *
   * Two more spellings emit a hand-written duration, and both are read: an
   * arbitrary property (the `transition-duration` longhand, or the
   * `transition` shorthand with a time in it, in square brackets as one
   * class) and any of them behind a variant (`hover:`, `data-[state=open]:`).
   * A shorthand reads a token when the token is its only time. The same
   * mechanism as above is why none is spelled out whole in this file.
   */
  /** Whether a scanned utility's duration is a `--duration-*` token. */
  const readsToken = (u: DurationUtility): boolean => u.prop.endsWith("-duration")
    ? /^var\(--duration-[a-z-]+\)$/.test(u.value)
    : /var\(--duration-[a-z-]+\)/.test(u.value)
      && tokensOf(u.value).every((t) => timeMs(t) === null);

  it("every duration-* utility in the sheet reads a --duration-* token", () => {
    const utilities = durationUtilities(root);
    // GROUND: the reader found the utilities at all, and found the SHAPE the
    // claim is about - a token-valued one - so an empty inventory below
    // reads as "nobody writes a number" rather than as "the scan is blind".
    // One is the floor now: the second utility that used to be here was the
    // Select popup's number, and it is gone.
    expect(utilities.length, "no duration utility at all; the scan is blind").toBeGreaterThan(0);
    const tokenForm = utilities.filter((u) => /^var\(--duration-[a-z-]+\)$/.test(u.value));
    expect(
      tokenForm.map((u) => u.className),
      "no utility reads a token; the reader is not seeing values",
    ).not.toEqual([]);
    for (const util of tokenForm) {
      const name = /^var\((--duration-[a-z-]+)\)$/.exec(util.value)![1];
      expect(table.get(name), `${util.className} reads ${name}, which is not declared`).toBeDefined();
    }

    // No exception roster: the sheet has to answer with nothing left over.
    // The Select popup was the one component that needed an entry, it reads
    // the token now, and an empty list is the only shape this assertion has
    // that a new hand-written duration cannot slip through.
    const offenders = utilities
      .filter((u) => !readsToken(u))
      .map((u) => `${u.className} -> ${u.value}`)
      .sort();
    expect(
      offenders,
      "a component writes its own duration instead of reading a token",
    ).toEqual([]);
  });

  it("POSITIVE CONTROL: each spelling of a hand-written duration is caught", () => {
    // Rules shaped the way the compiler emits them, appended to the shipped
    // sheet: an arbitrary property, the shorthand with a time, a variant
    // nesting its declaration under `&:hover` and a media query, and a
    // variant on an arbitrary property. Class names are assembled, so that
    // this file does not emit them into the sheet it reads.
    const escape = (cls: string) => cls.replace(/[[\]:()=!,.]/g, "\\$&");
    const arbitrary = "[transition-" + "duration:120ms]";
    const shorthand = "[transition:" + "opacity_120ms]";
    const hovered = "hover:" + "duration-" + "75";
    const openedArbitrary = "data-[state=open]:" + arbitrary;
    // A shorthand that names the token AND a time of its own: the token is
    // not its only time, and this is the entry the every-time-is-a-token
    // half of `readsToken` is the only thing to catch. Once in milliseconds
    // and once in seconds, which is the other unit a time is written in.
    const tokenAndTime = "[transition:" + "opacity_var(--duration-fast)_120ms]";
    const tokenAndSeconds = "[transition:" + "opacity_var(--duration-fast)_0.12s]";
    // Tailwind's duration variable, set by hand: every transition utility on
    // the element reads it.
    const variable = "[" + TW_DURATION + ":200ms]";
    const token = "[transition-" + "duration:var(--duration-fast)]";
    const variableToken = "[" + TW_DURATION + ":var(--duration-fast)]";
    const appended = [
      `.${escape(arbitrary)} { transition-duration: 120ms; }`,
      `.${escape(shorthand)} { transition: opacity 120ms; }`,
      `.${escape(hovered)} { &:hover { @media (hover: hover) { --tw-duration: 75ms; `
        + "transition-duration: 75ms; } } }",
      `.${escape(openedArbitrary)} { &[data-state="open"] { transition-duration: 120ms; } }`,
      `.${escape(tokenAndTime)} { transition: opacity var(--duration-fast) 120ms; }`,
      `.${escape(tokenAndSeconds)} { transition: opacity var(--duration-fast) 0.12s; }`,
      `.${escape(variable)} { ${TW_DURATION}: 200ms; }`,
      `.${escape(token)} { transition-duration: var(--duration-fast); }`,
      `.${escape(variableToken)} { ${TW_DURATION}: var(--duration-fast); }`,
    ].join("\n");
    const found = durationUtilities(parseSheet(`${css}\n${appended}`));
    const caught = found.filter((u) => !readsToken(u)).map((u) => u.className);
    for (const cls of [
      arbitrary, shorthand, hovered, openedArbitrary, tokenAndTime, tokenAndSeconds, variable,
    ]) {
      expect(caught, `the scan did not catch ${cls}`).toContain(cls);
    }
    // And a token passes in both arbitrary spellings too, so the ones above
    // are caught for their numbers and not for their shape.
    for (const cls of [token, variableToken]) {
      expect(found.map((u) => u.className), `the scan did not read ${cls}`).toContain(cls);
      expect(caught, `a token-valued arbitrary property was reported: ${cls}`).not.toContain(cls);
    }
  });
});

describe("under reduced motion nothing moves except the named exceptions (KURAL 5.6, T-17)", () => {
  it("GROUND: the sheet carries rules that move", () => {
    const moving = movingRules(root, table);
    expect(moving.length).toBeGreaterThan(20);
    const collapse = moving.find((e) => e.selector === ".es-collapse" && e.kind === "transition");
    expect(collapse, ".es-collapse no longer transitions").toBeDefined();
    expect(collapse!.durationMs).toBe(tokenMs("--duration-normal"));
  });

  it("T-17p POSITIVE CONTROL: with reduced motion off, every one of them moves for longer than the reset leaves it", () => {
    // Not "longer than zero": `movingRules` drops an entry whose duration is
    // zero or less, so that reading would be the filter agreeing with
    // itself. The claim with content is the one the difference below leans
    // on - every member of A moves for longer than the 1ms the reset leaves
    // it, so the reset is a real change for each of them rather than a
    // no-op letting the difference pass on a rule that never moved. The
    // bound is READ from the reset, not typed here.
    const resetMs = timeMs(
      universalResetDeclarations(root, table).get("*")!.get("transition-duration")!.value,
    );
    expect(resetMs, "the reset's duration is not a plain time").not.toBeNull();
    const moving = movingRules(root, table);
    for (const entry of moving) {
      expect(entry.durationMs, `${entry.selector} ${entry.kind}`).toBeGreaterThan(resetMs!);
    }
    // And the family this unit unified moves at the fast token.
    const menu = moving.find((e) => e.selector === ".persona-menu" && e.kind === "animation");
    expect(menu, ".persona-menu no longer animates in").toBeDefined();
    expect(menu!.durationMs).toBe(tokenMs("--duration-fast"));
  });

  it("A minus B minus the named exceptions is empty", () => {
    const { leftover } = stillMoving(root);
    expect(leftover, "these rules still move under prefers-reduced-motion").toEqual([]);
  });

  it("every re-opened selector is named, and every name still re-opens something", () => {
    const { unnamedReopenings, staleNames } = stillMoving(root);
    expect(unnamedReopenings, "reduced motion re-opens motion nobody wrote down").toEqual([]);
    expect(staleNames, "a named exception no longer re-opens anything").toEqual([]);
  });

  it("the reset is universal and !important for both kinds, pseudo-elements included", () => {
    // A universal rule has zero specificity; without !important it loses to
    // every rule it exists to silence, and `*` does not reach ::before or
    // ::after on its own. This is the DURATION half of the reset, the half
    // the set difference consults; the case below holds the whole block.
    const { model } = stillMoving(root);
    for (const kind of ["transition", "animation"] as const) {
      for (const selector of UNIVERSAL_RESET_SELECTORS) {
        expect(model.universal[kind].has(selector), `${kind} reset misses ${selector}`).toBe(true);
      }
    }
  });

  it("the reset writes exactly its four declarations, on all three selectors, all !important", () => {
    // The case above reads the reset through `kindOf`, which only knows the
    // two properties that carry a duration - so `animation-iteration-count`
    // and `scroll-behavior` could be deleted with the whole suite green.
    // Here the block is read literally, property by property, and the claim
    // is exact in both directions: these four, no fifth, every one of them
    // !important, on all three selectors.
    const reset = universalResetDeclarations(root, table);
    expect(
      [...reset.keys()].sort(),
      "the universal reduced-motion reset is not in the sheet as written",
    ).toEqual([...UNIVERSAL_RESET_SELECTORS].sort());
    for (const selector of UNIVERSAL_RESET_SELECTORS) {
      const byProp = reset.get(selector)!;
      expect(
        [...byProp.keys()].sort(),
        `${selector} does not carry exactly the reset's declarations`,
      ).toEqual(Object.keys(UNIVERSAL_RESET).sort());
      for (const [prop, expected] of Object.entries(UNIVERSAL_RESET)) {
        const decl = byProp.get(prop)!;
        expect(decl.value, `${selector} { ${prop} }`).toBe(expected);
        expect(decl.important, `${selector} { ${prop} } is not !important`).toBe(true);
      }
    }
  });

  it("GROUND for the reset reader: a missing, an extra and a soft declaration are each reported", () => {
    // Known answers on synthetic sheets, so the exactness above is a
    // measurement rather than a reader that happens to agree with the file
    // it was written against. This is also the positive control for the
    // "no fifth declaration" half, which is an absence claim.
    const written = Object.entries(UNIVERSAL_RESET)
      .map(([prop, value]) => `${prop}: ${value} !important;`)
      .join(" ");
    const full = `@media (prefers-reduced-motion: reduce) { *, *::before, *::after { ${written} } }`;
    const read = (css_: string) =>
      universalResetDeclarations(parseSheet(css_), new Map<string, string>());

    const asWritten = read(full);
    expect([...asWritten.keys()].sort()).toEqual([...UNIVERSAL_RESET_SELECTORS].sort());
    expect([...asWritten.get("*")!.keys()].sort()).toEqual(Object.keys(UNIVERSAL_RESET).sort());

    const short = read(full.replace("scroll-behavior: auto !important;", ""));
    expect([...short.get("*")!.keys()], "a deleted declaration is not reported missing")
      .not.toContain("scroll-behavior");

    const extra = read(
      full.replace("scroll-behavior: auto", "will-change: auto !important; scroll-behavior: auto"),
    );
    expect([...extra.get("*")!.keys()], "a fifth declaration is not reported").toContain(
      "will-change",
    );

    const soft = read(full.replace("animation-iteration-count: 1 !important", "animation-iteration-count: 1"));
    expect(
      soft.get("*")!.get("animation-iteration-count")!.important,
      "a declaration that lost !important still reads as important",
    ).toBe(false);
  });

  it("no keyframe loops for ever under the reset's 1ms duration", () => {
    // What the iteration count is FOR, as a claim about the sheet rather
    // than about the text of one rule: a loop the reset shortens to 1ms and
    // does not also cap turns a one-second rotation into a thousand-per-
    // second strobe, which is the harm the preference exists to prevent.
    const looping = infiniteAnimations(root, table);
    expect(looping.length, "nothing in the sheet loops; the reader is blind").toBeGreaterThan(2);
    const reset = universalResetDeclarations(root, table);
    const slowed: readonly string[] = NAMED_REDUCED_MOTION_EXCEPTIONS.slowedIndicators;
    let capped = 0;
    for (const selector of looping) {
      // The two slowed indicators are handed their `infinite` back on
      // purpose, by name, and the exception-family case above holds that.
      if (slowed.includes(selector)) continue;
      const pseudo = /::[a-z-]+$/.exec(selector)?.[0] ?? "";
      expect(
        reset.get(`*${pseudo}`)?.get("animation-iteration-count")?.value,
        `${selector} loops for ever and the reset does not cap it`,
      ).toBe(UNIVERSAL_RESET["animation-iteration-count"]);
      capped += 1;
    }
    expect(capped, "every looping rule is a named exception; nothing is capped").toBeGreaterThan(0);
  });

  it("keeps the two exception families it names, and only those", () => {
    // Both halves of the roster are exercised: the spinner slows rather
    // than stops (its re-opened duration is longer than its normal one),
    // and every opacity fade re-opens at exactly --duration-exit.
    const { model } = stillMoving(root);
    for (const spinner of NAMED_REDUCED_MOTION_EXCEPTIONS.slowedIndicators) {
      const slowed = model.reopened.animation.get(spinner);
      expect(slowed, `${spinner} is not slowed`).toBeDefined();
      const normal = movingRules(root, table).find(
        (e) => normalizeSelector(e.selector) === spinner && e.kind === "animation",
      );
      expect(normal, `${spinner} does not spin at all without the preference`).toBeDefined();
      expect(slowed!.durationMs).toBeGreaterThan(normal!.durationMs);
      // A spinner buys its animation back and nothing else.
      expect(model.reopened.transition.has(spinner), `${spinner} also re-opens a transition`).toBe(false);
    }
    for (const fade of NAMED_REDUCED_MOTION_EXCEPTIONS.opacityFades) {
      const reopening = model.reopened.transition.get(fade);
      expect(reopening, `${fade} fade`).toBeDefined();
      expect(reopening!.durationMs, `${fade} fade duration`).toBe(tokenMs("--duration-exit"));
      // And it is a fade: the only property the family re-opens is opacity.
      // The roster grants a property, not a selector, so this is the thing
      // the name actually bought.
      expect(reopening!.properties, `${fade} re-opens more than opacity`).toEqual(["opacity"]);
    }
  });

  it("GROUND for the checker: a re-opened selector nobody named is reported", () => {
    // Synthetic sheets, so the checker is measured against a known answer
    // rather than trusted because the real sheet happens to pass it.
    const base = `
      :root { --duration-fast: 180ms; }
      .probe { transition: opacity var(--duration-fast); }
      .quiet { transition: opacity var(--duration-fast); }
      @media (prefers-reduced-motion: reduce) {
        *, *::before, *::after {
          animation-duration: 1ms !important;
          transition-duration: 1ms !important;
        }
        .probe { transition: opacity 120ms !important; }
      }`;
    const withReopening = stillMoving(parseSheet(base));
    expect(withReopening.leftover).toEqual(["transition: .probe"]);
    expect(withReopening.unnamedReopenings).toEqual([".probe"]);

    const silent = stillMoving(parseSheet(base.replace(".probe { transition: opacity 120ms !important; }", "")));
    expect(silent.leftover).toEqual([]);

    // And a universal reset that forgot !important covers nothing.
    const toothless = stillMoving(parseSheet(base.replace(/ !important/g, "")));
    expect(toothless.leftover).toEqual(["transition: .probe", "transition: .quiet"]);

    // An empty A is an error, never a pass.
    expect(() => stillMoving(parseSheet(".x { color: red; }"))).toThrow(/positive/);
  });

  it("GROUND for the checker: a named exception covers its own property and nothing else", () => {
    // `.jump-to-latest` is on the roster because it keeps a 120ms opacity
    // fade. Read as "this selector is exempt" the roster would also wave
    // through a 900ms transform on it, which is the opposite of what
    // reduced motion asks for. Synthetic sheets with known answers, so the
    // narrowing is measured rather than asserted about the real sheet.
    const reset = `
      *, *::before, *::after {
        animation-duration: 1ms !important;
        transition-duration: 1ms !important;
      }`;
    const fade = `
      :root { --duration-fast: 180ms; --duration-exit: 120ms; }
      .jump-to-latest { transition: opacity var(--duration-fast); }
      @media (prefers-reduced-motion: reduce) {
        ${reset}
        .jump-to-latest { transition: opacity var(--duration-exit) !important; }
      }`;
    expect(stillMoving(parseSheet(fade)).leftover).toEqual([]);

    // Same name, a different property, from outside the media query.
    const andTransform = fade.replace(
      ".jump-to-latest { transition: opacity var(--duration-fast); }",
      ".jump-to-latest { transition: opacity var(--duration-fast); }" +
        "\n" +
        ".jump-to-latest { transition: transform 900ms !important; }",
    );
    expect(stillMoving(parseSheet(andTransform)).leftover).toEqual([
      "transition: .jump-to-latest",
    ]);

    // Same name, a different property, re-opened INSIDE the media query.
    const transformInside = fade.replace(
      ".jump-to-latest { transition: opacity var(--duration-exit) !important; }",
      ".jump-to-latest { transition: transform var(--duration-exit) !important; }",
    );
    expect(stillMoving(parseSheet(transformInside)).leftover).toEqual([
      "transition: .jump-to-latest",
    ]);

    // And a spinner's amnesty is for its animation, not for a transition it
    // never had.
    const spinner = `
      .animate-spin { animation: spin 1s linear infinite; }
      .animate-spin { transition: transform 900ms !important; }
      @media (prefers-reduced-motion: reduce) {
        ${reset}
        .animate-spin { animation-duration: 2.4s !important; }
      }`;
    expect(stillMoving(parseSheet(spinner)).leftover).toEqual([
      "transition: .animate-spin",
    ]);
  });
});

describe("a closed collapse gives up its margin as well as its height (FIX 6)", () => {
  /** Every block-margin declaration the sheet writes for a .es-collapse. */
  function collapseMargins() {
    const out: Array<{ selector: string; prop: string; value: string }> = [];
    root.walkDecls(/^margin-block(-start|-end)?$/, (decl) => {
      const rule = decl.parent;
      if (!rule || rule.type !== "rule") return;
      for (const selector of resolvedSelectors(rule)) {
        if (!/^\.es-collapse\b/.test(selector)) continue;
        out.push({
          selector,
          prop: decl.prop,
          value: resolveVars(decl.value, table).trim(),
        });
      }
    });
    return out;
  }

  it("GROUND: the shared collapsible is in the sheet and this reader can see both states", () => {
    // So an empty answer below reads as "the rule is gone", never as "the
    // reader cannot find .es-collapse rules".
    expect(
      declarationsFor(root, table, ".es-collapse", "grid-template-rows"),
      "the reader finds no .es-collapse rule at all",
    ).not.toHaveLength(0);
    expect(
      declarationsFor(root, table, '.es-collapse[data-open="true"]', "grid-template-rows"),
      "the reader finds no open-state rule",
    ).not.toHaveLength(0);
  });

  it("the closed state zeroes its block margin and the open state does not", () => {
    // Collapse keeps its content mounted, and every block FIX 6 wraps sits
    // in a space-y-* stack, so a closed section kept the margin its parent
    // hands each child and left a gap where nothing was.
    const margins = collapseMargins();
    expect(margins, "no .es-collapse rule gives up its block margin").toHaveLength(1);
    expect(margins[0].value, "the closed collapse does not zero its margin").toBe("0");

    // WHICH state the rule reaches is decided by a selector engine, not by
    // reading the selector text: two real elements are asked.
    const open = document.createElement("div");
    open.className = "es-collapse";
    open.setAttribute("data-open", "true");
    const closed = document.createElement("div");
    closed.className = "es-collapse";
    closed.setAttribute("data-open", "false");
    expect(
      closed.matches(margins[0].selector),
      "the rule does not reach a closed collapse",
    ).toBe(true);
    expect(
      open.matches(margins[0].selector),
      "an OPEN collapse loses its margin too",
    ).toBe(false);
  });

  it("the margins close on the row's own transition, not after it", () => {
    const transitions = declarationsFor(root, table, ".es-collapse", "transition");
    expect(transitions, ".es-collapse declares no transition").not.toHaveLength(0);
    const priced = new Map<string, number | null>();
    for (const segment of splitTopLevel(transitions[transitions.length - 1].value, ",")) {
      const parts = tokensOf(segment);
      priced.set(parts[0], parts.map(timeMs).find((t) => t !== null) ?? null);
    }
    for (const prop of ["grid-template-rows", "margin-block-start", "margin-block-end"]) {
      expect(priced.has(prop), `${prop} is not on the collapse transition`).toBe(true);
      expect(priced.get(prop), `${prop} is not priced at --duration-normal`)
        .toBe(tokenMs("--duration-normal"));
    }
  });
});

/**
 * The three hand-written portal popups (ChatList's menu and confirm,
 * PersonaStrip's menu). KARAR-N keeps them hand-written this round and asks
 * that they be one visual family; these are the three the plan names.
 */
const PORTAL_POPUPS = [".persona-menu", ".chat-action-menu", ".chat-action-confirm"] as const;

describe("the three hand-written popups are one family (FIX 8, KARAR-N)", () => {
  function lastValue(selector: string, prop: string): string {
    const decls = declarationsFor(root, table, selector, prop);
    expect(decls, `${selector} declares no ${prop}`).not.toHaveLength(0);
    return decls[decls.length - 1].value;
  }

  it("share border-radius, box-shadow, entry duration and entry curve", () => {
    for (const prop of ["border-radius", "box-shadow"]) {
      const values = new Set(PORTAL_POPUPS.map((s) => lastValue(s, prop)));
      expect([...values], `${prop} differs across the three`).toHaveLength(1);
    }
    const durations = new Set<number | null>();
    const curves = new Set<string | null>();
    for (const selector of PORTAL_POPUPS) {
      const animation = lastValue(selector, "animation");
      const times = tokensOf(animation).map(timeMs).filter((t): t is number => t !== null);
      durations.add(times[0] ?? null);
      curves.add(timingFunctionOf(animation));
    }
    expect([...durations]).toEqual([tokenMs("--duration-fast")]);
    expect([...curves]).toHaveLength(1);
    expect([...curves][0], "no easing on the entrance").not.toBeNull();
  });

  it("the two portaled chat popups take their COORDINATES from ChatList, not the sheet (FIX 8a, 8b)", () => {
    // ChatList.tsx portals both to document.body and sets position, top or
    // bottom and right inline from the trigger's rect. The stylesheet kept
    // its own copy of that geometry, which never applied; and the width was
    // a min() clamp that measured against the BODY once portaled, so it
    // never engaged either.
    //
    // The z-index is NOT on that list any more, and the change is deliberate.
    // It used to be written inline beside the coordinates, where the layer
    // table could not see it: the 40 these two sat at was a number no reader
    // of the table could find. A layer is a decision about the whole app, a
    // coordinate is a fact about one trigger; the first belongs to the sheet
    // and the second does not. The order itself is asserted in
    // css-contract.test.ts, against the compiled sheet.
    for (const selector of [".chat-action-menu", ".chat-action-confirm"]) {
      for (const prop of ["position", "right", "top"]) {
        expect(
          declarationsFor(root, table, selector, prop),
          `${selector} still places itself with ${prop} in the stylesheet`,
        ).toEqual([]);
      }
      expect(
        declarationsFor(root, table, selector, "z-index").map((d) => d.value),
        `${selector} takes its layer from somewhere other than the sheet`,
      ).toEqual(["40"]);
      expect(
        declarationsFor(root, table, selector, "width").map((d) => d.value),
        `${selector} width`,
      ).toEqual(["13rem"]);
    }
  });

  it("GROUND: a popup the sheet DOES place still reads as placed", () => {
    // Positive control for the absence above: the same reader, the same four
    // properties, on the one popup of this family that is positioned in CSS
    // because it is not portaled.
    for (const prop of ["position", "right", "top", "z-index"]) {
      expect(
        declarationsFor(root, table, ".message-action-confirm", prop),
        `the reader found no ${prop} anywhere, so the absences above prove nothing`,
      ).not.toHaveLength(0);
    }
  });

  it("all three fall silent under reduced motion, .persona-menu included (FIX 8c)", () => {
    // POSITIVE CONTROL first: each one animates in at the fast token when
    // the preference is off, so "silent" below is a change of state.
    const { moving, model } = stillMoving(root);
    for (const selector of PORTAL_POPUPS) {
      const entries = moving.filter(
        (e) => normalizeSelector(e.selector) === selector && e.kind === "animation",
      );
      expect(entries, `${selector} has no entrance to silence`).not.toHaveLength(0);
      for (const entry of entries) {
        expect(entry.durationMs).toBe(tokenMs("--duration-fast"));
        expect(isNeutralised(entry, model), `${selector} keeps moving`).toBe(true);
        expect(isReopened(entry, model), `${selector} is re-opened`).toBe(false);
      }
    }
  });

  it("all three lose their blur with transparency reduced (FIX 8d)", () => {
    const switchedOff = new Set<string>();
    root.walkDecls(/^(-webkit-)?backdrop-filter$/, (decl) => {
      if (!/^none\b/.test(decl.value.trim()) || !inMedia(decl, REDUCED_TRANSPARENCY)) return;
      const rule = decl.parent;
      if (rule && rule.type === "rule") {
        for (const s of resolvedSelectors(rule)) switchedOff.add(s);
      }
    });
    expect(switchedOff.size, "nothing is switched off").toBeGreaterThan(0);
    for (const selector of PORTAL_POPUPS) {
      // GROUND: it is blurred to begin with, or there is nothing to switch off.
      // (The reduced-transparency `none` is also a declaration for this
      // selector, so the base rule is asked for rather than the last one.)
      expect(
        declarationsFor(root, table, selector, "backdrop-filter").some((d) => /blur\(/.test(d.value)),
        `${selector} is not blurred to begin with`,
      ).toBe(true);
      expect(
        [...switchedOff].some((entry) => switchOffReaches(entry, selector)),
        `${selector} keeps its blur with transparency reduced`,
      ).toBe(true);
    }
  });
});

describe("accessibility preferences the app must actually read", () => {
  it("honours reduced transparency across the glass surfaces", () => {
    // Windows exposes this switch (Personalization > Colors > Transparency
    // effects) and this is a Windows app, so somebody can already have it on.
    // It is a READABILITY preference, distinct from reduced motion.
    //
    // css-contract checks WHICH selectors the block lists and that they are
    // all real; that `backdrop-filter: none` is what the block does to them
    // is asserted here and nowhere else. Delete this and the block could
    // list every blurred surface correctly while turning off nothing at all.
    let switchesOff = 0;
    root.walkDecls("backdrop-filter", (decl) => {
      if (inMedia(decl, REDUCED_TRANSPARENCY) && /^none\b/.test(decl.value.trim()) && decl.important) {
        switchesOff += 1;
      }
    });
    expect(switchesOff, "no reduced-transparency rule switches blur off").toBeGreaterThan(0);
  });
});
