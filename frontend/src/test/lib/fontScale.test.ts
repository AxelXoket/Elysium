/**
 * fontScale.test.ts - the type scale is closed, and so is the weight set.
 *
 * T-50 and T-81. Two claims, one file, because they are the same claim about
 * two properties: a size and a weight are chosen from a list or not at all.
 *
 * The base this replaced, measured on 8 September 2026 and reproduced before
 * the work started: 52 font-size declarations in index.css carrying 15
 * different value texts - nine px values (9, 10, 10.5, 11, 11.5, 12, 12.5,
 * 13, 15), five rem literals (0.72, 0.74, 0.76, 0.85, 1.05) and one var()
 * fallback - plus four Tailwind steps in the components, three of them
 * arbitrary bracket sizes at 9, 10 and 11 pixels. Seven steps replace all
 * of it.
 *
 * Nothing in this file SPELLS a retired or an arbitrary class, comments
 * included. Tailwind scans test files as source and scans them as raw text
 * rather than as code, so a class named inside a comment here is a class
 * that exists in the compiled sheet - which is how the first draft of this
 * file failed its own assertion, with its own docstring as the offender.
 *
 * Both claims are read off the COMPILED sheet, not the source: the source
 * says what somebody wrote, the compiled sheet says what the browser gets,
 * and the difference is exactly where an arbitrary Tailwind class hides.
 * That is also why the compiled sheet closes the class side on its own -
 * Tailwind emits a rule for a bracket size the moment one appears in a
 * scanned file, so a size that is not on the scale arrives here as a
 * font-size declaration that is not on the scale.
 *
 * The render sweep at the bottom is a second net, and its reach is exactly
 * what `AppShell` paints on first render - no more. A RETIRED stock class
 * emits no rule at all (the `--text-*` namespace is cleared before the seven
 * steps are declared, and that reset is asserted below), so neither net can
 * see one on a surface the shell does not mount. The dialogs are the
 * surfaces that sit outside both, and they are swept where they are already
 * opened for real: css-contract.test.ts's dialog geometry block runs the
 * same predicate over each one's rendered tree.
 *
 * The right panel's Notes tab is the other surface the shell does not open.
 * The last block below opens it, with every panel in it showing its buttons,
 * and holds each button's words to the chrome step through the size the
 * compiled sheet gives them.
 *
 * Nothing is typed here: the allowed sets are derived from `typeScale` and
 * `fontWeights` in lib/theme/tokens.ts, which tokens-sync.test.ts holds
 * equal to index.css in both directions.
 */
import { AUTO_ACCEPT_OFF } from "@/components/notebook/noteProse";
import { createElement } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import { AppShell } from "@/components/layout/AppShell";
import { Providers } from "@/app/providers";
import {
  MSG_FONT_DEFAULT,
  MSG_FONT_MAX,
  MSG_FONT_MIN,
} from "@/lib/store/uiStore";
import {
  fontWeights,
  typeScale,
  TYPE_SCALE_DEVIATIONS,
  TYPE_SCALE_STEPS,
} from "@/lib/theme/tokens";
import {
  apiEscapes,
  guardAgainstApiEscapes,
  mockFetch,
  shellRoutes,
} from "@/test/mocks/api";
import { characterFixture } from "@/test/mocks/fixtures";
import { loadGlassRightCss } from "@/test/helpers/glassSurfaceCss";
import {
  NOTES_TAB_BLOCKS,
  NOTES_TAB_CONFIRMS,
  armNotesTabConfirms,
  closeNotesTab,
  mountNotesTab,
  notesTabBlockOf,
  settleNotesTab,
} from "@/test/helpers/notesTab";
import {
  fontSizePx,
  inAtRule,
  parseSheet,
  remPx,
  resolveVars,
  rootTokens,
} from "@/test/helpers/compiledSheet";

// The whole-file escape net, registered once: the render sweep below mounts
// the app shell, and a mount that lost its stub would report a missing
// element rather than the requests that left. See mocks/api.ts.
guardAgainstApiEscapes();

/** The scale's sizes, in computed pixels. */
const SCALE_PX = new Set<number>(Object.values(typeScale).map((s) => s.px));

/**
 * The relative sizes Tailwind's preflight declares, which are not choices.
 *
 * `inherit` on the headings, `1em` on the monospace elements and the two
 * percentages on `small` / `sub` / `sup` are the reset saying "whatever the
 * parent said", so they carry no size of their own and cannot be on or off
 * a scale. They are listed rather than filtered by shape: a FIFTH relative
 * value would be somebody inventing one, and the list is asserted whole.
 *
 * The exclusion is also a LOCATION rule: it applies only inside `@layer
 * base`, which is where the preflight lives. Excluded by value alone, the
 * four words would travel - a `font-size: inherit` written anywhere in
 * index.css would hide behind the list, and this is the one net that would
 * have seen it.
 *
 * The layer and not the preflight's own selectors, deliberately. Keyed to the
 * selectors, this would carry a copy of the reset's element list and go stale
 * on the next Tailwind upgrade. The layer is not free either, and the reason
 * is worth writing down rather than assuming: index.css DOES open an
 * `@layer base` of its own (the `*`, `body`, `html`, scrollbar and
 * focus-ring block), so "inside the reset's layer" and "written by the
 * reset" are not the same place, and a hand-written `font-size: inherit` in
 * that block would match the list.
 *
 * What closes it is the COUNT: the ground below pins the number of relative
 * sizes inside `@layer base` to the length of this list, so a fifth one is
 * reported wherever in the layer it was written, and it does that without
 * naming a single element the reset happens to style today.
 */
const PREFLIGHT_RELATIVE = ["inherit", "1em", "80%", "75%"];

/** The same, for weights: the reset's own keywords. */
const PREFLIGHT_RELATIVE_WEIGHTS = ["inherit", "bolder"];

/** The root font size every rem in this sheet is measured against. */
const ROOT_PX = 16;

/** A relative size that is the preflight speaking, rather than a choice. */
function isPreflightRelative(size: { raw: string; preflight: boolean }): boolean {
  return size.preflight && PREFLIGHT_RELATIVE.includes(size.raw);
}

let sheet = "";
let table = new Map<string, string>();
/** Every font-size the sheet declares, resolved: [selector, computed px]. */
const sizes: { selector: string; px: number; raw: string; preflight: boolean }[] = [];
/** Every font-weight the sheet declares, resolved. */
const weights: { selector: string; value: string }[] = [];

/** `13px` / `0.9375rem` -> 13 / 15. Anything that is not a length -> null. */
function computedPx(value: string): number | null {
  const m = /^(-?[\d.]+)(px|rem)$/.exec(value.trim());
  if (!m) return null;
  const n = Number(m[1]);
  return m[2] === "rem" ? n * ROOT_PX : n;
}

beforeAll(async () => {
  sheet = await loadGlassRightCss();
  const root = parseSheet(sheet);
  table = rootTokens(root);
  root.walkDecls((decl) => {
    const selector = decl.parent && "selector" in decl.parent
      ? String((decl.parent as { selector: string }).selector)
      : "?";
    if (decl.prop === "font-size") {
      const resolved = resolveVars(decl.value, table).trim();
      sizes.push({
        selector,
        px: computedPx(resolved) ?? NaN,
        raw: resolved,
        // The reset's own layer. A relative value is only the reset speaking
        // when it is written where the reset is written.
        preflight: inAtRule(decl, (at) =>
          at.name === "layer" && at.params.trim() === "base"),
      });
    }
    if (decl.prop === "font-weight") {
      weights.push({ selector, value: resolveVars(decl.value, table).trim() });
    }
  });
}, 60_000);

describe("the type scale is closed (T-50)", () => {
  it("GROUND: the sheet really declares sizes, and they really resolve", () => {
    // Without this every set comparison below is between two empty sets, and
    // a path typo or a parser that silently returned nothing would read as
    // a perfectly closed scale.
    expect(sizes.length, "no font-size in the compiled sheet").toBeGreaterThan(40);
    expect(
      sizes
        .filter((s) => Number.isNaN(s.px) && !isPreflightRelative(s))
        .map((s) => `${s.selector}: ${s.raw}`),
      "a font-size did not resolve to a length - an unresolved var() reads as no size at all",
    ).toEqual([]);
    // And the reset's own relative values are exactly the four known ones,
    // so "not a length" cannot become a place to hide a fifth.
    expect(
      [...new Set(sizes.filter((s) => Number.isNaN(s.px)).map((s) => s.raw))].sort(),
      "the preflight's relative sizes changed",
    ).toEqual([...PREFLIGHT_RELATIVE].sort());
    // GROUND for the location half: the four really are inside the reset's
    // own layer. Without this the `preflight` flag could be false for every
    // declaration and the exclusion would simply have stopped excluding.
    expect(
      sizes.filter((s) => Number.isNaN(s.px) && s.preflight).length,
      "no relative size sits inside @layer base - the location rule is reading nothing",
    ).toBe(PREFLIGHT_RELATIVE.length);
  });

  it("every size the browser is handed is a step of the scale", () => {
    const off = sizes
      .filter((s) => !isPreflightRelative(s))
      .filter((s) => !SCALE_PX.has(s.px))
      .map((s) => `${s.selector}: ${s.raw}`);
    expect(off, "a size that is not on the scale reached the sheet").toEqual([]);
  });

  it("POSITIVE CONTROL: a relative size OUTSIDE the reset is not excused", () => {
    // The location half, run over synthetic rows through the same predicate.
    // Excluded by value alone, a `font-size: inherit` written anywhere in
    // index.css would hide behind the preflight's list.
    const inside = { selector: "h1", px: NaN, raw: "inherit", preflight: true };
    const outside = { selector: ".probe", px: NaN, raw: "inherit", preflight: false };
    expect(isPreflightRelative(inside)).toBe(true);
    expect(isPreflightRelative(outside), "a relative size travelled out of the reset")
      .toBe(false);
  });

  it("keeps the --text-* namespace closed to everything but the seven", () => {
    // What `--text-*: initial` BUYS, asserted where it can be seen. The reset
    // itself never reaches the output - Tailwind consumes it while building
    // the theme - and with no call site left on a retired name the compiler
    // emits neither the retired token nor its utility, so deleting the reset
    // changes not one byte of today's sheet. Measured, and said plainly
    // rather than left as a test that cannot fail.
    //
    // What CAN be read is the namespace the build ended up with. The reset
    // and a retired call site are each other's only guard: put a retired
    // class back with the reset deleted and Tailwind emits that step's token
    // and its utility together, and this and the absence check above both go
    // red. The names are not written here for the reason in the file comment
    // above - a retired name spelled in a comment IS a call site as far as
    // Tailwind's scanner is concerned, which would make that proof circular.
    const steps = new Set<string>(TYPE_SCALE_STEPS.map((s) => `--text-${s}`));
    const strays = [...table.keys()]
      .filter((name) => name.startsWith("--text-"))
      .filter((name) => !steps.has(name.split("--").slice(0, 2).join("--")));
    expect(strays, "a --text-* token outside the seven steps is in the table")
      .toEqual([]);
    // GROUND: the seven really are in that table, so an empty stray list is
    // not an empty namespace.
    expect(
      [...steps].filter((name) => table.has(name)),
      "the scale's own steps are not in the compiled token table",
    ).toHaveLength(TYPE_SCALE_STEPS.length);
  });

  it("and the scale has no step nothing uses", () => {
    // The other direction, so the set comparison is an equality and not a
    // one-way filter: a step that no rule and no utility ever produces is a
    // number in a table pretending to be a decision.
    const used = new Set(sizes.map((s) => s.px));
    expect([...SCALE_PX].filter((px) => !used.has(px)), "a step nothing uses")
      .toEqual([]);
  });

  it("emits no utility for the steps that were retired", () => {
    // Tailwind's own stock steps are cleared out of the namespace
    // (`--text-*: initial`) before the seven are declared, so a call site
    // that reaches for a retired one compiles to nothing at all. The retired
    // names are BUILT here rather than written, for the reason in the file
    // comment above.
    const retired = ["x" + "s", "s" + "m", "b" + "ase", "l" + "g", "x" + "l",
                     "2x" + "l", "3x" + "l"];
    for (const name of retired) {
      expect(sheet, `the retired text-${name} utility is back in the sheet`)
        .not.toMatch(new RegExp(`\\.text-${name}\\s*\\{`));
    }
    // POSITIVE CONTROL for the same query: the steps that ARE on the scale
    // and are used as classes do emit rules, so "absent" above is a reading
    // and not a regex that matches nothing. Not every step emits a utility -
    // a step used only through var() in index.css has no class - so this
    // asks that several do, not that all seven do.
    const used = TYPE_SCALE_STEPS.filter((step) =>
      new RegExp(`\\.text-${step}\\s*\\{`).test(sheet));
    expect(used.length, "no scale utility in the sheet at all").toBeGreaterThan(3);
  });

  it("POSITIVE CONTROL: the sheet can be caught carrying an off-scale size", () => {
    // The filter above proves nothing unless it can report. A synthetic
    // declaration goes through the same resolve-and-compare path.
    const probe = [
      { selector: ".probe", px: computedPx("12.5px") ?? NaN, raw: "12.5px" },
      { selector: ".probe-rem", px: computedPx("0.85rem") ?? NaN, raw: "0.85rem" },
    ] as const;
    expect(probe.filter((s) => !SCALE_PX.has(s.px)).map((s) => s.raw))
      .toEqual(["12.5px", "0.85rem"]);
  });
});

describe("the weight set is closed (T-81)", () => {
  it("GROUND: the sheet really declares weights, 600 and 700 among them", () => {
    expect(weights.length, "no font-weight in the compiled sheet")
      .toBeGreaterThan(15);
    const values = new Set(weights.map((w) => w.value));
    // POSITIVE CONTROL: the two the sheet is known to use are found, so the
    // query reads real declarations rather than an empty walk.
    expect(values.has("600"), "no 600 anywhere - the query is not reading").toBe(true);
    expect(values.has("700"), "no 700 anywhere - the query is not reading").toBe(true);
  });

  it("uses no weight that is not on the scale, and at most three", () => {
    const allowed = new Set(fontWeights.map(String));
    const off = weights
      .filter((w) => !PREFLIGHT_RELATIVE_WEIGHTS.includes(w.value))
      .filter((w) => !allowed.has(w.value))
      .map((w) => `${w.selector}: ${w.value}`);
    expect(off, "a weight outside 400/600/700 reached the sheet").toEqual([]);
    expect(
      new Set(
        weights
          .filter((w) => !PREFLIGHT_RELATIVE_WEIGHTS.includes(w.value))
          .map((w) => w.value),
      ).size,
      "more than three weights",
    ).toBeLessThanOrEqual(3);
    expect(fontWeights.length, "the scale itself grew past three")
      .toBeLessThanOrEqual(3);
  });

  it("writes the label step's weight down instead of letting it inherit", () => {
    // A quiet label inside a semibold block comes out semibold unless the
    // step says otherwise, which is the one thing a quiet label must not be.
    // The value is read from the scale, and the sheet is asked for the
    // token the scale implies - so this fails if either side moves alone.
    expect(typeScale.label.weight, "the label step stopped carrying a weight")
      .not.toBeNull();
    expect(fontWeights, "the label's weight left the allowed set")
      .toContain(typeScale.label.weight);
    expect(
      table.get("--text-label--font-weight"),
      "the sheet does not declare the label step's weight",
    ).toBe(String(typeScale.label.weight));
  });

  it("carries none of the four values that fall differently per family", () => {
    // 450 and 650 are indistinguishable from their neighbours; 500 and 550
    // draw Semibold in Segoe UI and Regular in Arial, so what a reader sees
    // depends on a Windows setting nobody here can see (KURAL 3.4).
    const banned = new Set(["450", "500", "550", "650"]);
    expect(
      weights.filter((w) => banned.has(w.value)).map((w) => `${w.selector}: ${w.value}`),
    ).toEqual([]);
  });
});

describe("the deviation inventory describes the sheet it claims to", () => {
  it("every selector it names really carries the step it names", () => {
    // The plan lets the role rule beat the transition table and charges one
    // price for it: "fark envanterde adiyla yazilir". An inventory nobody
    // checks is prose, and prose goes stale the first time somebody moves one
    // of these declarations back - so each row that names a SELECTOR is
    // resolved against the compiled sheet and held to the step it claims.
    //
    // The call-site rows are not checked here. Their subject is a class on a
    // component rather than a rule in the sheet, and they are covered where
    // the class closure is: nothing off the scale renders, and the seven
    // steps are the only sizes the namespace has.
    const rows = TYPE_SCALE_DEVIATIONS.filter((d) =>
      d.where.startsWith("index.css ."));
    expect(rows.length, "the inventory names no stylesheet selector at all")
      .toBeGreaterThan(5);
    for (const row of rows) {
      const selector = row.where.slice("index.css ".length);
      const declared = sizes.filter((s) => s.selector.split(",")
        .map((part) => part.trim()).includes(selector));
      expect(declared.length, `${selector} declares no font-size in the sheet`)
        .toBeGreaterThan(0);
      for (const size of declared) {
        expect(size.px, `${selector} is not on the step the inventory claims`)
          .toBe(typeScale[row.step].px);
      }
    }
  });

  it("and every row is a real deviation, not a row somebody forgot to delete", () => {
    // The other direction. A row whose `fromPx` already equals the step it
    // moved to is either a mistake or a leftover; the one row where they are
    // equal says so in its own reason, and it is named here rather than
    // filtered out silently.
    const unchanged = TYPE_SCALE_DEVIATIONS
      .filter((d) => d.fromPx === typeScale[d.step].px)
      .map((d) => d.where);
    // The string is WRITTEN OUT rather than read back off the constant. It
    // exists as TYPE_SCALE_DEVIATIONS[1].where, and asserting a value
    // against itself would accept any row the list happened to hold: the
    // claim is that this one selector, and no other, moved to a step without
    // moving a pixel, so the selector is the claim.
    expect(unchanged, "a row in the inventory records no move at all")
      .toEqual(["index.css .char-avatar"]);
    // The row count is pinned for the same reason. The sheet-side check
    // above only walks the rows that are there, so a deviation disarms
    // itself the moment somebody deletes its row.
    expect(TYPE_SCALE_DEVIATIONS, "a row left the inventory, or arrived in it")
      .toHaveLength(14);
    // And every reason is an argument rather than a rubber stamp. The length
    // is the same floor testsAreBehavioural.ts holds its own exemptions to,
    // for the same reason: a one-word reason is a stamp.
    for (const row of TYPE_SCALE_DEVIATIONS) {
      expect(row.why.length, `${row.where} carries no argument`)
        .toBeGreaterThan(40);
    }
  });
});

describe("the message body default is the scale's body step", () => {
  it("MSG_FONT_DEFAULT is the body step, and sits inside its own range", () => {
    // The single number IP-05 FIX 1 exists to move, 14 to 15, and nothing
    // held it: setting it back to 14 left all 2506 tests green. The assertion
    // that used to pin it - `expect(relaunch().msgFontPx).toBe(14)` - was
    // rewritten in this same work to `toBe(MSG_FONT_DEFAULT)`, which is the
    // constant compared with itself.
    //
    // Bound to the SCALE rather than retyped, which is the point of the fix:
    // the message body is the body step, so the store and the scale move
    // together or this goes red. The number itself is asserted beside it,
    // because "equal to whatever the scale says" would still be green on a
    // scale whose body step had drifted.
    expect(MSG_FONT_DEFAULT, "the body default left the type scale")
      .toBe(typeScale.body.px);
    expect(MSG_FONT_DEFAULT, "the body step is no longer 15").toBe(15);
    expect(MSG_FONT_DEFAULT, "the default is below the reader's own minimum")
      .toBeGreaterThanOrEqual(MSG_FONT_MIN);
    expect(MSG_FONT_DEFAULT, "the default is above the reader's own maximum")
      .toBeLessThanOrEqual(MSG_FONT_MAX);
    // GROUND: the range is a real range and not two copies of the default,
    // which is the shape that would make the two assertions above vacuous.
    expect(MSG_FONT_MIN, "the reader's range collapsed").toBeLessThan(MSG_FONT_MAX);
    expect(MSG_FONT_MIN).not.toBe(MSG_FONT_DEFAULT);
    expect(MSG_FONT_MAX).not.toBe(MSG_FONT_DEFAULT);
  });

  it("and the reader's own range is the range the plan leaves alone", () => {
    // IP-05 DO NOT TOUCH: "Kullanicinin punto ayarinin araligi
    // (`MSG_FONT_MIN` 13, `MSG_FONT_MAX` 19)". The assertions above only ask
    // that the default sits BETWEEN the two, so narrowing the range around it
    // is invisible: min 13 -> 12 left the whole suite green, and the setting
    // a person already uses would have silently lost a step at each end.
    // These two numbers are written down because nothing else pins them.
    expect([MSG_FONT_MIN, MSG_FONT_MAX], "the reader's range moved")
      .toEqual([13, 19]);
  });
});

/**
 * A class with its variant prefixes taken off: everything before the last
 * colon goes.
 *
 * Both sweeps below anchored at the START of the class string, so a retired
 * step behind a breakpoint or a state prefix was invisible to them. That is
 * the single most likely form, and it is the exact one KURAL 3.6 was about:
 * the primitive base strings this work cleaned up each carried a breakpoint
 * variant of a retired step beside the base one.
 *
 * No class is spelled in this file, prefixed or bare, and not in a comment
 * either. Both halves of that are load-bearing and each was measured:
 *
 *  - An ARBITRARY bracket size emits a real rule the moment it is written,
 *    whatever the namespace does. A sentence in this very docstring put one
 *    into the compiled sheet and turned the assertion below red.
 *  - A retired STOCK name emits nothing today, but only because index.css
 *    clears the `--text-*` namespace. One survived in a comment further down
 *    this file, and with the reset deleted the compiled sheet gained that
 *    token and its utility back out of the comment alone - so the reset was
 *    carrying a test file's prose. It is assembled from fragments now like
 *    every other name here, and deleting the reset costs the sheet nothing.
 */
function bareClass(cls: string): string {
  return cls.split(":").pop() ?? cls;
}

/** A size class that is not one of the seven steps. Retired stock names are
 *  BUILT, never spelled, for the reason in the file comment above. */
function isOffScaleSize(cls: string): boolean {
  const bare = bareClass(cls);
  const allowed = new Set(TYPE_SCALE_STEPS.map((s) => `text-${s}`));
  if (allowed.has(bare)) return false;
  const retired = ["x" + "s", "s" + "m", "b" + "ase", "l" + "g", "x" + "l",
                   "2x" + "l", "3x" + "l"];
  if (!bare.startsWith("text-")) return false;
  const step = bare.slice(5);
  return retired.includes(step) || /^(\d|\[)/.test(step);
}

/** A weight class that is not one of the three on the scale. Allow-listed
 *  rather than banned by name: Tailwind ships nine weight words, and a list
 *  of the four somebody thought of lets the other five straight past. The
 *  names are not written here, for the reason in bareClass above. */
function isOffScaleWeight(cls: string): boolean {
  const bare = bareClass(cls);
  if (!bare.startsWith("font-")) return false;
  const rest = bare.slice(5);
  if (["normal", "semi" + "bold", "bold"].includes(rest)) return false;
  return /^[a-z]+$/.test(rest) || /^\[\d+\]$/.test(rest);
}

describe("what actually renders stays on both scales", () => {
  /** Every className on the page, as the app really paints it. */
  async function renderedClasses(): Promise<string[]> {
    // The transport is stubbed BEFORE the shell mounts. `AppShell` runs
    // `useStaleSelectionReconciliation`, which fires the chat, character,
    // model and settings queries on mount; the egress sentinel's host list
    // holds `127.0.0.1` on purpose, so with no stub in place it RECORDS those
    // calls and forwards them to the real transport - the running
    // application's own API, in front of a live vault. A sweep of a page
    // built that way is not a test of anything.
    mockFetch(shellRoutes());
    const { container } = render(createElement(Providers, null, createElement(AppShell)));
    // Waited for on a node that only exists once the character query has
    // ANSWERED, so the sweep reads a settled page and React is not left
    // logging an update outside act() on the way out.
    await screen.findByText(String(characterFixture.name));
    const out: string[] = [];
    for (const el of container.querySelectorAll<HTMLElement>("*")) {
      const raw = el.getAttribute("class");
      if (raw) out.push(...raw.split(/\s+/));
    }
    expect(out.length, "the shell rendered nothing to look at").toBeGreaterThan(50);
    expect(apiEscapes(), "the shell reached the real transport").toEqual([]);
    return out;
  }

  it("no retired size class and no arbitrary one survives on the page", async () => {
    // The second net, and its reach is what the shell paints. A retired stock
    // class emits no rule, so the sheet cannot see one; the render tree can.
    // The dialogs sit outside this sweep and are covered where they are
    // already opened, in css-contract.test.ts's dialog geometry block. Names
    // are built, never spelled, for the same reason as above.
    const classes = await renderedClasses();
    expect(
      classes.filter(isOffScaleSize),
      "a size class outside the scale is on the page",
    ).toEqual([]);
  });

  it("no weight class outside the scale survives on the page", async () => {
    const classes = await renderedClasses();
    expect(
      classes.filter(isOffScaleWeight),
      "a weight class outside 400/600/700 is on the page",
    ).toEqual([]);
  });

  it("POSITIVE CONTROL: the sweeps find what they are looking for", () => {
    // The REAL predicates, run over lists that do contain the shapes they
    // hunt. It used to be a hand-copied lookalike - one equality against a
    // single retired step, spelled out, where the matcher used a list - so
    // deleting a branch from the matcher left this reporting 4 of 4 and
    // green. One function, three callers. The step is not named here for the
    // reason the docstring above gives, and that is not pedantry: while it
    // WAS named, deleting the namespace reset put the retired utility back
    // into the shipped sheet out of this comment alone.
    const sizes = ["text-" + "sm", "text-" + "[10px]", "md:" + "text-" + "sm",
                   "text-" + "2x" + "l"];
    expect(sizes.filter(isOffScaleSize), "the size sweep went blind")
      .toHaveLength(sizes.length);
    const weights = ["font-" + "medium", "font-" + "[450]",
                     "font-" + "extra" + "light", "md:" + "font-" + "medium"];
    expect(weights.filter(isOffScaleWeight), "the weight sweep went blind")
      .toHaveLength(weights.length);
    // And neither reports what belongs on the page, so "found nothing" above
    // is a reading rather than a predicate that never fires.
    const kept = [`text-${TYPE_SCALE_STEPS[1]}`, `md:text-${TYPE_SCALE_STEPS[2]}`,
                  "font-" + "semi" + "bold", "font-" + "normal"];
    expect(kept.filter(isOffScaleSize)).toEqual([]);
    expect(kept.filter(isOffScaleWeight)).toEqual([]);
  });
});

describe("every button in the Notes tab is on the chrome step", () => {
  // IP-05's role rule over a whole tab: the words on a button are chrome,
  // and chrome is the ui step. The tab stacks four panels - the notebook,
  // with the backfill strip and the saved-notes block inside it, the
  // limits, the extraction settings and the worker card - and each block's
  // own rows hold only that block, which is how the worker card kept the
  // same ghost button a step below the two blocks above it. This row opens
  // the tab the way RightPanel composes it (test/helpers/notesTab.ts), so a
  // button added to any of the four, or a fifth panel, is read without
  // being named here, as long as one of the three states below renders it.
  //
  // What it covers, exactly: every `<button>` in the tab panel in three
  // states - at rest, with every confirm armed, and with a search typed.
  // Buttons are taken by tag, so the ones inside a shut Collapse are read
  // too. Two buttons exist only behind a state, and the fixture carries
  // both: "Keep suggestion" is on a proposed note, which the fixture lists,
  // and "Clear search" appears once a query is typed, which the third state
  // does.
  //
  // What is read is the size of the WORDS, not of the button: each text
  // node inside a button, at the element that holds it, through the
  // compiled sheet's cascade and inheritance. A label wrapped in a span of
  // its own size is read at that span; a button with no words, an icon, is
  // read at itself. And the size has to be the chrome step exactly: words
  // above it are off the role rule as surely as words below it.
  afterEach(() => closeNotesTab());

  /** The chrome step, as a key of the imported scale. */
  const CHROME: keyof typeof typeScale = "ui";

  /** Where each piece of a button's words gets its size: the element that
   *  holds each text node, or the button itself when it has no words. */
  function wordsOf(button: Element): { at: Element; words: string }[] {
    const out: { at: Element; words: string }[] = [];
    const walker = document.createTreeWalker(button, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const words = (node.textContent ?? "").trim();
      if (words !== "" && node.parentElement) out.push({ at: node.parentElement, words });
    }
    return out.length > 0 ? out : [{ at: button, words: "" }];
  }

  it("sizes every button's words at it, at rest, with every confirm armed and "
     + "with a search typed", async () => {
    const root = parseSheet(sheet);
    const tokenTable = rootTokens(root);
    const rem = remPx(root);
    const chrome = typeScale[CHROME].px;
    const { fetchMock } = mountNotesTab();
    const user = userEvent.setup();
    // Settled: each wait is a button only its own block renders.
    await settleNotesTab();
    const panel = screen.getByRole("tabpanel");

    const nameOf = (b: Element): string =>
      (b.getAttribute("aria-label") ?? "").trim() || (b.textContent ?? "").trim()
        || `the button named by ${b.getAttribute("aria-labelledby")}`;
    const sweep = (when: string) => [...panel.querySelectorAll("button")].flatMap((b) =>
      wordsOf(b).map(({ at, words }) => ({
        when, block: notesTabBlockOf(b), name: nameOf(b), words, nested: at !== b,
        px: fontSizePx(root, tokenTable, rem, at),
      })));

    const atRest = sweep("at rest");
    // GROUND: every block of the tab put buttons in the sweep, and the
    // worker card's three - the ones this row was written for - are there.
    expect([...new Set(atRest.map((b) => b.block))],
      "a block of the tab rendered no button to read")
      .toEqual(expect.arrayContaining([...NOTES_TAB_BLOCKS]));
    for (const name of [/^try again now$/i, /^try this chat again$/i,
                        /^hold this chat's suggestions for review$/i]) {
      expect(atRest.some((b) => b.block === "Background notes" && name.test(b.name)),
        `the worker card's ${name} was not swept`).toBe(true);
    }
    // And words really were read where they sit, not at their button: the
    // strip's scope field holds its words in a span of their own.
    expect(atRest.some((b) => b.block === "the strip" && b.nested && b.words !== ""),
      "no button's words were read at an element inside it").toBe(true);

    // Every confirm in the tab armed, so the buttons that only exist behind
    // a first press are read as well.
    await armNotesTabConfirms(user);
    const armed = sweep("with every confirm armed");
    // GROUND for the second pass: the confirms really are in it.
    for (const name of Object.values(NOTES_TAB_CONFIRMS)) {
      expect(armed.some((b) => name.test(b.name)), `${name} was not swept`).toBe(true);
    }

    // A search typed, which is the one state "Clear search" exists in. The
    // query matches every note, so the rows stay on screen with it.
    await user.type(screen.getByRole("textbox", { name: /search notes/i }), "note");
    await screen.findByRole("button", { name: /^clear search$/i });
    const searching = sweep("with a search typed");
    // GROUND for the third pass, and for the two buttons only a state shows.
    for (const name of [/^clear search$/i, /^keep suggestion$/i]) {
      expect(searching.some((b) => name.test(b.name)), `${name} was not swept`).toBe(true);
    }

    const offStep = (px: number) => px !== chrome;
    expect([...atRest, ...armed, ...searching].filter((b) => offStep(b.px))
      .map((b) => `${b.when}: ${b.block}: ${b.name}${b.words && b.words !== b.name
        ? ` ("${b.words}")` : ""} -> ${b.px}px`),
      "a button in the Notes tab sets its words off the chrome step")
      .toEqual([]);

    // POSITIVE CONTROL: the same reader, on the same tab, reports a size
    // off the chrome step where there is one - the worker card's prose is
    // on the label step - so the empty list above is a reading.
    const prose = screen.getByText(AUTO_ACCEPT_OFF);
    expect(fontSizePx(root, tokenTable, rem, prose),
      "the reader did not see the label step on the worker card's prose")
      .toBe(typeScale.label.px);
    // POSITIVE CONTROL for reading the words rather than the button: a
    // button on the chrome step whose label sits in a span a step below, and
    // one a step above, are each reported by the same two functions, at the
    // span. Every class is a step's own, taken off the imported scale.
    for (const step of ["label", "control"] as const) {
      const probe = document.createElement("button");
      probe.className = `text-${CHROME}`;
      const span = document.createElement("span");
      span.className = `text-${step}`;
      span.textContent = "words";
      probe.appendChild(span);
      document.body.appendChild(probe);
      try {
        const read = wordsOf(probe).map(({ at }) => fontSizePx(root, tokenTable, rem, at));
        expect(read, `the words reader did not read the ${step} span`)
          .toEqual([typeScale[step].px]);
        expect(read.filter(offStep), `the check passed words on the ${step} step`)
          .toHaveLength(1);
        expect(fontSizePx(root, tokenTable, rem, probe), "the probe button is not on the chrome step")
          .toBe(chrome);
      } finally {
        probe.remove();
      }
    }

    // GROUND for the fixture: every request the tab made was answered. A
    // route left to 404 puts its panel in an error state, and that panel
    // then shows fewer buttons to read without anything here saying so.
    const responses = await Promise.all(
      fetchMock.mock.results.map((r) => r.value as Promise<Response>));
    expect(responses.length, "the tab made no requests at all").toBeGreaterThan(0);
    expect(responses
      .map((res, i) => ({ status: res.status, url: String(fetchMock.mock.calls[i][0]) }))
      .filter((call) => call.status === 404),
      "the tab asked for a route the fixture does not answer").toEqual([]);
  }, 60_000);
});
