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
import { NEW_LIMIT_PROMPT } from "@/components/notebook/limitsProse";
import { WORKER_DEAD } from "@/components/notebook/workerProse";
import { CHOICE_UNREAD } from "@/components/notebook/noteProse";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MotionGlobalConfig } from "motion/react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import {
  AAA_NORMAL,
  AA_NON_TEXT,
  AA_NORMAL,
  contrastRatio,
  luminance,
  parseHex,
  type Rgb,
} from "@/lib/appearance/contrast";
import type { Root } from "postcss";

import {
  composite,
  glassRightStops,
  loadGlassRightCss,
  parseRgbColour,
  worstRatioOverStack,
} from "@/test/helpers/glassSurfaceCss";
import {
  declarationOnSelector,
  inkOf,
  ownDeclaration,
  ownOpacity,
  ownPaints,
  parseSheet,
  placeholderInkOf,
  resolvedSelectors,
  rootTokens,
  stackOf,
  winningDeclaration,
  type Ink,
  type StackStep,
} from "@/test/helpers/compiledSheet";
import { fogFloor, panelMistGrounds, panelMistVeil } from "@/test/helpers/fogGround";
import { PANEL_BANDS } from "@/components/backdrop/fogShader";
import {
  NOTES_TAB_BLOCKS,
  NOTES_TAB_CHAT,
  NOTES_TAB_CONFIRMS,
  NOTES_TAB_WORKER_DIED,
  armNotesTabConfirms,
  mountNotesTab,
  notesTabBlockOf,
  notesTabRoutes,
  settleNotesTab,
} from "@/test/helpers/notesTab";
import { useUiStore } from "@/lib/store/uiStore";
import { useDraftStore } from "@/lib/store/draftStore";
import { CHARS_PER_TOKEN } from "@/lib/context/estimateContextUsage";
import { getErrorMessage } from "@/lib/errors/errorMessages";
import { modelFixture } from "@/test/mocks/fixtures";
import { Providers } from "@/app/providers";
import { AppShell } from "@/components/layout/AppShell";
import { mockFetch } from "@/test/mocks/api";
import {
  DRAWING_MODEL,
  closeRightPanel,
  isNotesTab,
  motionAtRest,
  mountRightPanel,
  openRightPanelTab,
  rightPanelRoutes,
  rightPanelTabNames,
} from "@/test/helpers/rightPanelTabs";

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

/** Every ground a line on this panel is read on: the panel's own two stops,
 *  which are what shows with the mist off, and the two the mist puts there
 *  at its darkest with it on (helpers/fogGround.ts): the veil's stops, read
 *  from the sheet, over the darkest pixel the right panel's fog can paint.
 *  The mist is on by default and its canvas is opaque, so with it on the
 *  panel's gradient is not what the eye meets at all. */
const panelGrounds = (): Rgb[] => [...gradientStops(), ...panelMistGrounds(parseSheet(css), "right")];

/** The muted ink this panel shipped before its words were measured with the
 *  mist on, for the control that proves the mist is in the grounds. */
const MUTED_READ_WITHOUT_THE_MIST = "#5E6B7A";

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

  it("the SECONDARY ink clears AA against both stops and the mist at its darkest", () => {
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
    //
    // The mist is the ground most of the time: it is on by default, and
    // with it on the words sit on its veil over the fog. At the fog's darkest
    // the old #5E6B7A read 3.8:1 there, and on screen 3.85 to 4.49:1 on
    // every tab at the two wider panel widths.
    for (const token of ["--color-es-text-muted", "--muted-foreground"]) {
      const ink = parseHex(glassRightToken(token));
      expect(ink, `${token} is no longer a plain hex`).not.toBeNull();
      panelGrounds().forEach((ground, i) => {
        expect(contrastRatio(ink!, ground), `${token} on ground ${i}`)
          .toBeGreaterThanOrEqual(AA_NORMAL);
      });
    }
  });

  it("GROUND: the mist is in the grounds, and it is the darker of the two", () => {
    // The grounds are four, and the two the mist adds are darker than the
    // panel's own stops: the veil over the darkest fog lets the fog through.
    const grounds = panelGrounds();
    expect(grounds).toHaveLength(4);
    const [top, bottom] = gradientStops();
    const [veilTop, veilBottom] = grounds.slice(2);
    expect(luminance(veilTop)).toBeLessThan(luminance(top));
    expect(luminance(veilBottom)).toBeLessThan(luminance(bottom));
    // The fog's floor is swept from the shader's constants, and the sweep
    // settles: a finer grid finds the same pixel.
    expect(fogFloor(PANEL_BANDS.right, 90)).toEqual(fogFloor(PANEL_BANDS.right));
    // And the veil is read from the sheet, not assumed: it is declared where
    // the grounds read it.
    expect(declarationOnSelector(parseSheet(css), panelMistVeil("right"), /^background$/))
      .not.toBeNull();
    // POSITIVE CONTROL: the ink the panel shipped before the mist was
    // measured clears AA on the panel's own stops and not on the mist's.
    const before = parseHex(MUTED_READ_WITHOUT_THE_MIST)!;
    for (const stop of [top, bottom]) {
      expect(contrastRatio(before, stop)).toBeGreaterThanOrEqual(AA_NORMAL);
    }
    expect(Math.min(contrastRatio(before, veilTop), contrastRatio(before, veilBottom)))
      .toBeLessThan(AA_NORMAL);
  });

  it("GROUND: and it stays the QUIETER tier while doing it", () => {
    // The discriminating half, and the reason the floor above is AA rather
    // than AAA. Darkening the muted token until it cleared the primary's bar
    // would pass the assertion above and quietly collapse the panel's two
    // ink tiers into one - the same failure index.css:875-881 names as the
    // real cost of softening an ink. Ordering, on the surface as painted.
    const muted = parseRgbColour(glassRightToken("--color-es-text-muted"));
    const primary = parseRgbColour(glassRightToken("--color-es-text-light"));
    for (const ground of panelGrounds()) {
      expect(contrastRatio(muted, ground)).toBeLessThan(
        contrastRatio(primary, ground),
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

describe("the words the right panel paints", () => {
  // KURAL 2.4 on every line the panel draws, measured the way KURAL 2.4b
  // says: the ink with its opacity applied, against what it is laid over,
  // on each of the panel's grounds: its two gradient stops over the frame,
  // which show with the mist off, and the mist's veil over the darkest
  // pixel its fog can paint, which is the ground with the mist on, the
  // default (panelGrounds, above).
  //
  // Three defects gave these rows their shape. A line that names no ink of its
  // own inherits one, and until the panel re-declared the body's colour rule
  // (index.css, `.glass-right`) it inherited the body's: :root's
  // --foreground, resolved at the body for the dark theme and inherited as a
  // finished colour that the panel's redefinition of the token never
  // reached. Note words, limit labels, what is typed into the API key and
  // the two proxy fields, and "Test stored key" read 1.00 to 1.09:1 on
  // screen, with the mist on and off. A line can be dimmed after its ink is
  // chosen: the Notes tab's lifetime spend sat at `opacity-70` and read
  // 2.7:1 while a reader that took the ink alone passed it. And the grounds
  // held only the panel's own gradient while the mist, on by default, put a
  // darker one under every line: measured on screen with the mist on, the
  // muted ink read 3.85 to 4.49:1 on every tab at the wider panel widths,
  // and with the mist off it cleared 4.5:1.
  //
  // So the ink comes from the cascade and inheritance on the rendered panel
  // (inkOf), and the ratio from the stack the engine paints (stackOf and
  // worstRatioOverStack): every element between the panel and the words
  // with its own opacity and its own paint, a card, a tint, a button's fill,
  // the words last, over each stop. A control that is switched off is not
  // held to the floor, as WCAG 1.4.3 does not hold it; a closed Collapse is
  // on no screen, so it is not read.
  //
  // Each line is read at rest. The panel's entrances fade in with motion,
  // and a line read mid-fade carries the fade's opacity, so motion finishes
  // every animation at once while these rows run.
  beforeAll(() => {
    MotionGlobalConfig.skipAnimations = true;
  });
  afterAll(() => {
    MotionGlobalConfig.skipAnimations = false;
  });

  // Unmounted first: this hook runs before the library's own cleanup, and a
  // store change under a mounted tab is an update outside act().
  afterEach(() => {
    cleanup();
    closeRightPanel();
  });

  /** A control that is switched off. WCAG 1.4.3 does not hold the text of
   *  an inactive user interface component to the floor, and nothing else:
   *  a line that is not inside a control is held to it whatever it says
   *  about itself, so the let-off is asked of the nearest control only. */
  const INACTIVE = ':disabled, [aria-disabled="true"], [data-disabled]';
  const CONTROL = 'button, input, select, textarea, [role="button"], [role="switch"], '
    + '[role="checkbox"], [role="radio"], [role="option"], [role="tab"], [role="menuitem"], '
    + '[role="combobox"], [role="slider"]';
  const switchedOff = (at: Element): boolean => at.closest(CONTROL)?.matches(INACTIVE) === true;
  /** A Collapse that is shut: its content is mounted, clipped to nothing
   *  and inert, and no screen shows it. */
  const SHUT = '[data-slot="collapse"][data-open="false"]';
  /** The fields a person types into, whose typed words take the field's
   *  own ink. A field hidden from assistive technology is the form value a
   *  Select or a switch carries, clipped to nothing, and no one types in it. */
  const TEXT_FIELDS = 'textarea:not([aria-hidden="true"]), input:not([type="hidden"])'
    + ':not([type="checkbox"]):not([type="radio"]):not([type="range"]):not([type="file"])'
    + ':not([aria-hidden="true"])';

  /** The notes and limits the fixture puts on screen, by the words they
   *  show, taken from the fixture's own bodies rather than retyped. */
  function listed(): { what: string; words: string }[] {
    const bodies = Object.values(notesTabRoutes()).map((r) => r.body as Record<string, unknown>);
    const pick = (key: string): Record<string, unknown>[] =>
      bodies.flatMap((b) => (Array.isArray(b?.[key]) ? (b[key] as Record<string, unknown>[]) : []));
    return [
      ...pick("entries").map((n) => ({ what: "a note", words: String(n.text) })),
      ...pick("boundaries").map((l) => ({ what: "a limit", words: String(l.label) })),
    ];
  }

  /** The lowest ratio a line in `ink` reaches over what it is laid over in
   *  `panel`: `at` and every element above it inside the panel, each with
   *  its own opacity and paint, over each of the panel's grounds, the mist
   *  at its darkest among them. */
  const worstUnder = (
    root: Root, panel: Element, at: Element, ink: string, memo?: Map<Element, StackStep>,
  ): number => worstRatioOverStack(ink, stackOf(root, panel, at, memo), panelGrounds()).ratio;

  /** Whether a colour as written is one token reference, and so follows
   *  whichever surface resolves it, rather than a colour spelled out. A
   *  utility compiled from an inline theme value is spelled out: the root's
   *  value is baked into it, and the panel's redefinition never reaches it.
   *  A token dimmed by an opacity modifier, one reference mixed with
   *  `transparent`, still follows its surface. */
  function isTokenReference(written: string | null): boolean {
    const dimmed = written === null ? null
      : /^color-mix\(in [a-z-]+,\s*(var\(.*\))\s+\d+(?:\.\d+)?%,\s*transparent\)$/.exec(written);
    if (dimmed) return isTokenReference(dimmed[1]);
    if (written === null || !/^var\(\s*--[\w-]+/.test(written)) return false;
    let depth = 0;
    for (let i = 3; i < written.length; i += 1) {
      if (written[i] === "(") depth += 1;
      else if (written[i] === ")") {
        depth -= 1;
        if (depth === 0) return i === written.length - 1;
      }
    }
    return false;
  }

  /** GROUND for the fixture: every request the panel made was answered. A
   *  route left to 404 puts its block in an error state, which shows fewer
   *  lines to read without anything here saying so. */
  async function everyRequestAnswered(
    fetchMock: { mock: { calls: unknown[][]; results: { value: unknown }[] } },
  ) {
    const responses = await Promise.all(
      fetchMock.mock.results.map((r) => r.value as Promise<Response>));
    expect(responses.length, "the panel made no requests at all").toBeGreaterThan(0);
    expect(responses
      .map((res, i) => ({ status: res.status, url: String(fetchMock.mock.calls[i][0]) }))
      .filter((call) => call.status === 404),
      "the panel asked for a route the fixture does not answer").toEqual([]);
  }

  /** The panel the open tab is on. */
  const panelOf = (): HTMLElement => {
    const panel = screen.getByRole("tabpanel").closest<HTMLElement>(".glass-right");
    expect(panel, "the tab is not on the right panel").not.toBeNull();
    return panel!;
  };

  /** One run of words the panel paints: a text node's words, what is typed
   *  into a field (in the field's own ink), or a field's placeholder. */
  interface Reading {
    when: string;
    what: "words" | "typed" | "placeholder";
    words: string;
    at: Element;
    ink: Ink;
    faults: string[];
  }

  /**
   * Every run of words in `panel`, each judged on up to three questions:
   *  - where its ink was decided has to be inside the panel. An ink
   *    resolved above it was resolved for the dark theme;
   *  - with `tokens`, what was written there has to be a token reference,
   *    which follows whichever surface resolves it;
   *  - and the colour, with its opacity and every ancestor's applied, has
   *    to clear AA over what it is laid over, on the panel's two stops.
   * Judged while the sweep runs: a confirm swaps its row's buttons out, so
   * an element read at one moment may be gone by the next.
   */
  function sweep(
    root: Root,
    panel: Element,
    when: string,
    tokens: boolean,
  ): Reading[] {
    const memo = new Map<Element, Ink>();
    const steps = new Map<Element, StackStep>();
    const judge = (at: Element, ink: Ink): string[] => {
      if (switchedOff(at)) return [];
      const faults: string[] = [];
      if (ink.from === null || !panel.contains(ink.from)) {
        faults.push(`takes ${ink.colour} from <${ink.from?.tagName.toLowerCase() ?? "nothing"}>, `
          + "above the panel");
      }
      if (tokens && !isTokenReference(ink.written)) {
        faults.push(`is painted in ${ink.written ?? "no colour"}, spelled out rather than a token`);
      }
      const ratio = worstUnder(root, panel, at, ink.colour, steps);
      if (!(ratio >= AA_NORMAL)) {
        faults.push(`reads ${ratio.toFixed(2)}:1 on what it is laid over, in ${ink.colour}`);
      }
      return faults;
    };
    const out: Reading[] = [];
    const walker = document.createTreeWalker(panel, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const words = (node.textContent ?? "").trim();
      const at = node.parentElement;
      if (words === "" || !at || at.closest(SHUT) || at.matches(TEXT_FIELDS)) continue;
      const ink = inkOf(root, at, memo);
      out.push({ when, what: "words", words, at, ink, faults: judge(at, ink) });
    }
    for (const field of panel.querySelectorAll<HTMLInputElement>(TEXT_FIELDS)) {
      if (field.closest(SHUT)) continue;
      const name = field.getAttribute("aria-label") ?? field.getAttribute("placeholder") ?? "a field";
      const typed = inkOf(root, field, memo);
      out.push({ when, what: "typed", words: name, at: field, ink: typed, faults: judge(field, typed) });
      const placeholder = field.getAttribute("placeholder");
      if (placeholder && field.value === "") {
        const ink = placeholderInkOf(root, field);
        out.push({ when, what: "placeholder", words: placeholder, at: field, ink,
          faults: judge(field, ink) });
      }
    }
    return out;
  }

  const described = (r: Reading): string =>
    `${r.when}: ${r.what} "${r.words.slice(0, 60)}" ${r.faults.join("; ")}`;

  it("paints each note's words and each limit's label in an ink that clears AA on the "
     + "panel and on the row's card, opacity applied", async () => {
    const root = parseSheet(css);
    const lines = listed();
    // GROUND: the fixture lists both kinds of line, so the loop below reads
    // something of each.
    expect(lines.filter((l) => l.what === "a note").length).toBeGreaterThan(0);
    expect(lines.filter((l) => l.what === "a limit").length).toBeGreaterThan(0);
    const { fetchMock } = mountNotesTab();
    await settleNotesTab();
    const panel = panelOf();

    const low: string[] = [];
    for (const line of lines) {
      const el = screen.getByText(line.words);
      // GROUND: the line is on the panel this file measures, on a card, and
      // the card is in the stack it is read over, with its paint.
      expect(panel.contains(el), `${line.what} is not on the right panel`).toBe(true);
      const card = el.closest(".persona-card");
      expect(card, `${line.what} is not on a card`).not.toBeNull();
      const onCard = stackOf(root, panel, el).find((step) => step.element === card);
      expect(onCard?.paints.length, `${line.what}'s card paints nothing under it`)
        .toBeGreaterThan(0);
      const ink = inkOf(root, el);
      const ratio = worstUnder(root, panel, el, ink.colour);
      expect(Number.isFinite(ratio), `${line.what}'s ratio came out ${ratio}`).toBe(true);
      if (ratio < AA_NORMAL) {
        low.push(`${line.what} "${line.words}": ${ink.colour}, decided on `
          + `<${ink.from?.tagName.toLowerCase() ?? "nothing"}>, ${ratio.toFixed(2)}:1`);
      }
    }
    expect(low, "a line the Notes tab lists is under AA on its own panel").toEqual([]);

    // GROUND for the panel's own colour rule: a line on the same card that
    // names no ink takes the panel's primary ink, decided on the panel
    // itself. Before the rule it was decided on the body, in the dark
    // theme's text.
    const first = screen.getByText(lines[0].words);
    const primary = declarationOnSelector(root, ".glass-right", /^--foreground$/);
    expect(primary, "the panel's rule declares no --foreground").not.toBeNull();
    const probe = document.createElement("p");
    probe.textContent = "words with no ink";
    first.parentElement!.appendChild(probe);
    try {
      const bare = inkOf(root, probe);
      expect(bare.from, "the bare line did not take its ink from the panel").toBe(panel);
      expect(bare.colour).toBe(primary);
      expect(worstUnder(root, panel, probe, bare.colour), "the panel's own ink is under AA here")
        .toBeGreaterThanOrEqual(AA_NORMAL);
      // POSITIVE CONTROL for the reader: a token the panel resolves to the
      // dark shell's quiet ink, read on the same card, is under the floor.
      probe.setAttribute("style", "color: var(--es-quiet-72)");
      expect(worstUnder(root, panel, probe, inkOf(root, probe).colour),
        "the dark shell's quiet ink read as readable here").toBeLessThan(AA_NORMAL);
      // POSITIVE CONTROL for the opacity: the panel's own ink on the same
      // card, dimmed to a fifth, is under the floor, where undimmed it
      // cleared it just above.
      probe.setAttribute("style", "opacity: 0.2");
      expect(worstUnder(root, panel, probe, inkOf(root, probe).colour),
        "a line at a fifth of its opacity read as readable").toBeLessThan(AA_NORMAL);
    } finally {
      probe.remove();
    }
    // POSITIVE CONTROL for where a var() resolves: the tab's heading names
    // the panel's text token, and the reader answers the value the panel's
    // own rule declares for it, not the root's, both read off the parsed
    // sheet.
    const heading = screen.getByRole("heading", { name: /^notebook$/i });
    const token = "--color-es-text-light";
    const onPanel = declarationOnSelector(root, ".glass-right", new RegExp(`^${token}$`));
    const atRoot = rootTokens(root).get(token);
    expect(onPanel, `the panel's rule declares no ${token}`).not.toBeNull();
    expect(atRoot, `the root declares no ${token}`).toBeDefined();
    expect(onPanel, "the panel and the root agree, so nothing is measured").not.toBe(atRoot);
    expect(inkOf(root, heading).colour).toBe(onPanel);
    await everyRequestAnswered(fetchMock);
  }, 60_000);

  it("paints the strip's preview refusal in an ink that clears AA on its own tint and its "
     + "card, opacity applied", async () => {
    // The refusal line is the one the Notes tab shows only when the preview
    // could not be worked out, so the rest-state sweep never meets it. The
    // same sweep reads it here.
    const root = parseSheet(css);
    const user = userEvent.setup();
    const { fetchMock } = mountNotesTab({ previewFails: true });
    await settleNotesTab();
    await user.click(screen.getByRole("button", { name: /^read the earlier messages$/i }));
    const refusal = await screen.findByTestId("backfill-preview-error");
    const panel = panelOf();
    await motionAtRest(panel);

    const read = sweep(root, panel, "with the preview refused", true);
    // GROUND: the refusal's words are in the sweep, and it sits on its own
    // tint on the strip's painted card, which is the ground it is read on.
    expect(read.filter((r) => refusal.contains(r.at)).length, "the refusal was not swept")
      .toBeGreaterThan(0);
    const stack = stackOf(root, panel, refusal);
    expect(stack[stack.length - 1].paints.length, "the refusal paints no tint of its own")
      .toBeGreaterThan(0);
    expect(stack.some((s) => s.element.matches(".persona-card") && s.paints.length > 0),
      "the refusal is not on a painted card").toBe(true);
    expect(read.filter((r) => r.faults.length > 0).map(described),
      "a line in the Notes tab is under AA with the preview refused").toEqual([]);

    // GROUND: the refusal's ink is the one its class paints, the island's
    // danger ink, and not a colour given at the call site.
    expect(refusal.getAttribute("style")).toBeNull();
    expect(inkOf(root, refusal).written).toBe("var(--es-island-danger-ink)");
    // POSITIVE CONTROL: the panel's danger token, which the class painted
    // before, is under the floor on that same tint and card.
    refusal.setAttribute("style", "color: var(--color-es-danger)");
    try {
      const bare = sweep(root, panel, "in the panel's danger token", true)
        .filter((r) => refusal.contains(r.at));
      expect(bare.some((r) => r.faults.some((f) => f.startsWith("reads"))),
        "the panel's danger token read as readable on the refusal's tint").toBe(true);
    } finally {
      refusal.removeAttribute("style");
    }
    await everyRequestAnswered(fetchMock);
  }, 60_000);

  it("paints the Notes tab's error lines in an ink that clears AA on their own tint, with the "
     + "mist on and off", async () => {
    // Three lines the rest-state sweep never meets, because each is shown
    // only when something failed: the note model's list could not be
    // fetched, the saved choice could not be read, and the background reader
    // has stopped. They share the refusal class with the strip's preview
    // refusal, so they are read the same way.
    const root = parseSheet(css);
    const { fetchMock } = mountNotesTab({ errors: true });
    await settleNotesTab();
    const panel = panelOf();
    const lines = [
      await screen.findByText(/^the list of models could not be fetched/i),
      await screen.findByText(CHOICE_UNREAD),
      await screen.findByText(WORKER_DEAD),
    ];
    // GROUND: the stopped reader's line is the fixture's; its reason rides
    // in the title for a bug report, not in the sentence.
    expect(lines[2].getAttribute("title")).toBe(NOTES_TAB_WORKER_DIED);
    await motionAtRest(panel);
    const read = sweep(root, panel, "with its error lines", true);
    for (const line of lines) {
      // GROUND: each line is in the sweep, on the refusal class, and on its
      // own tint, which is the ground it is read on.
      expect(line.matches(".persona-local-error"), `${line.textContent} lost its class`).toBe(true);
      expect(read.some((r) => line.contains(r.at)), `${line.textContent} was not swept`).toBe(true);
      const stack = stackOf(root, panel, line);
      expect(stack[stack.length - 1].paints.length, "an error line paints no tint of its own")
        .toBeGreaterThan(0);
    }
    expect(read.filter((r) => r.faults.length > 0).map(described),
      "a line in the Notes tab is under AA with its error lines shown").toEqual([]);

    // POSITIVE CONTROL, per line: the panel's danger token, which the class
    // painted before, is under the floor on the same tint.
    for (const line of lines) {
      line.setAttribute("style", "color: var(--color-es-danger)");
      try {
        const bare = sweep(root, panel, "in the panel's danger token", true)
          .filter((r) => line.contains(r.at));
        expect(bare.some((r) => r.faults.some((f) => f.startsWith("reads"))),
          `${line.textContent} read as readable in the panel's danger token`).toBe(true);
      } finally {
        line.removeAttribute("style");
      }
    }
    await everyRequestAnswered(fetchMock);
  }, 60_000);

  it("paints the lines the Models and Security tabs show only when something is set or has "
     + "gone wrong, in an ink that clears AA on what it is laid over, with the mist on and off",
  async () => {
    // Three lines the rest-state sweep never meets: the Models tab's line
    // when the model list cannot be fetched, the context gauge's warning
    // that the turn will be refused, and "Remove proxy", which is drawn
    // only while a proxy is on file. Each is drawn here and read by the
    // same sweep, in its tab, with everything else that state puts on
    // screen.
    const root = parseSheet(css);
    const cases: {
      state: string;
      options: Parameters<typeof mountRightPanel>[0];
      tab: string;
      arrange?: () => void;
      line: (panel: HTMLElement) => Promise<HTMLElement>;
      /** The ink the line was painted in before it took the island's. */
      was: string;
    }[] = [
      {
        state: "the model list cannot be fetched",
        options: { modelListFails: true },
        tab: "Models",
        line: (panel) => within(panel).findByText(getErrorMessage("internal_error")),
        was: "var(--color-es-danger)",
      },
      {
        state: "the turn being written will be refused",
        options: {},
        tab: "Models",
        // A draft longer than the whole of the chosen model's context, in
        // the chat the panel is open on.
        arrange: () => {
          const context = modelFixture.context_length;
          expect(context, "the fixture's model states no context").not.toBeNull();
          expect(useDraftStore.getState().setComposerDraft(NOTES_TAB_CHAT,
            "x".repeat(context! * CHARS_PER_TOKEN + 1)),
          "the draft store refused the draft").toBe(true);
        },
        line: (panel) => within(panel).findByTestId("context-usage-refusal"),
        was: "var(--color-es-danger)",
      },
      {
        state: "a proxy is on file",
        options: { proxyConfigured: true },
        tab: "Security",
        line: (panel) => within(panel).findByRole("button", { name: /^remove proxy$/i }),
        was: "var(--color-es-danger)",
      },
    ];
    try {
      for (const { state, options, tab, arrange, line: find, was } of cases) {
        const user = userEvent.setup();
        arrange?.();
        const { fetchMock, queryClient } = mountRightPanel(options);
        await screen.findAllByRole("tab");
        await openRightPanelTab(user, tab, queryClient);
        const panel = panelOf();
        const line = await find(panel);
        await motionAtRest(panel);
        const read = sweep(root, panel, `${tab}, while ${state}`, false);
        // GROUND: the line is in the sweep.
        expect(read.some((r) => line.contains(r.at)), `${state}: its line was not swept`)
          .toBe(true);
        expect(read.filter((r) => r.faults.length > 0).map(described),
          `a line in the ${tab} tab is under AA while ${state}`).toEqual([]);
        // And its ink is the island's danger ink, which follows the token.
        expect(inkOf(root, line).written, `${state}: its line lost the island's danger ink`)
          .toBe("var(--es-island-danger-ink)");
        // POSITIVE CONTROL: the ink the line was painted in before is under
        // the floor where it sits.
        const own = line.getAttribute("style");
        line.setAttribute("style", `${own ?? ""};color: ${was}`);
        try {
          const before = sweep(root, panel, `${state}, in ${was}`, false)
            .filter((r) => line.contains(r.at));
          expect(before.some((r) => r.faults.some((f) => f.startsWith("reads"))),
            `${state}: ${was} read as readable where the line sits`).toBe(true);
        } finally {
          if (own === null) line.removeAttribute("style");
          else line.setAttribute("style", own);
        }
        await everyRequestAnswered(fetchMock);
        cleanup();
        closeRightPanel();
        useDraftStore.getState().clearAll();
      }
    } finally {
      useDraftStore.getState().clearAll();
    }
  }, 120_000);

  it("paints every line of every tab from inside the panel, in an ink that clears AA on what "
     + "it is laid over, opacity applied; the Notes tab from a token, and with every confirm "
     + "armed", async () => {
    const root = parseSheet(css);
    const user = userEvent.setup();
    const { fetchMock, queryClient } = mountRightPanel();
    await screen.findAllByRole("tab");
    const tabs = rightPanelTabNames();

    const read: Reading[] = [];
    const perTab = new Map<string, Reading[]>();
    let notesTab: string | null = null;
    let flipped = 0;
    /** Presses every switch and box in `panel` that is not switched off,
     *  and waits for what each press asked for. GROUND: each one changed
     *  state, so the sweep after it reads the other state. */
    const flipEveryToggle = async (panel: Element) => {
      const toggles = [...panel.querySelectorAll('[role="switch"], [role="checkbox"]')]
        .filter((t) => !t.matches(INACTIVE));
      for (const toggle of toggles) {
        const was = toggle.getAttribute("aria-checked");
        await user.click(toggle);
        const named = toggle.getAttribute("aria-label")
          ?? document.getElementById(toggle.getAttribute("aria-labelledby") ?? "")?.textContent
          ?? toggle.textContent;
        await waitFor(() => expect(toggle.getAttribute("aria-checked"), `"${named}" did not flip`)
          .not.toBe(was));
        await waitFor(() => expect(queryClient.isFetching() + queryClient.isMutating()).toBe(0));
      }
      flipped += toggles.length;
    };
    for (const tab of tabs) {
      await openRightPanelTab(user, tab, queryClient);
      const panel = panelOf();
      const isNotes = isNotesTab(panel);
      if (isNotes) notesTab = tab;
      const atRest = sweep(root, panel, `${tab}, at rest`, isNotes);
      perTab.set(tab, atRest);
      read.push(...atRest);
      const search = within(panel).queryByRole("textbox", { name: "Search models" });
      if (search) {
        // The Models tab's second state: every capability filter and the
        // sort switched the other way from rest, and a search that finds
        // nothing, so the active chips, their Clear, "Most capable first"
        // switched on and the chip that names the chosen model while it is
        // filtered out are drawn as well.
        const chips = within(panel).getAllByRole("checkbox", { name: /^only models that/i });
        // GROUND: a capability that comes in and one that goes out.
        expect(chips.length, "the fixture offers fewer than two capability filters")
          .toBeGreaterThanOrEqual(2);
        await flipEveryToggle(panel);
        await user.type(search, "no model is called this");
        await motionAtRest(panel);
        const filtered = sweep(root, panel, `${tab}, with every toggle flipped and a search`, false);
        // GROUND: the active chips, their Clear, the sort switched on and the
        // chosen model's chip are in it.
        for (const words of [/^clear$/i, /^selected:$/i]) {
          expect(filtered.some((r) => r.what === "words" && words.test(r.words)),
            `${words} was not swept`).toBe(true);
        }
        const sort = within(panel).getByRole("switch", { name: /^most capable first$/i });
        expect(sort).toHaveAttribute("aria-checked", "true");
        expect(filtered.some((r) => r.at === sort), "the sort switched on was not swept").toBe(true);
        read.push(...filtered);
        // The third: a model whose parameters the list does not give is the
        // chosen one, so the generation card says it cannot tell, in its pill
        // and in a note of its own.
        act(() => useUiStore.setState({ selectedModelId: DRAWING_MODEL.id }));
        await motionAtRest(panel);
        const unknown = sweep(root, panel, `${tab}, with a model of unknown parameters chosen`, false);
        expect(unknown.some((r) => /^parameter support is unknown/i.test(r.words)),
          "the generation card's note on unknown parameters was not swept").toBe(true);
        read.push(...unknown);
      }
      if (isNotes) {
        // GROUND: every block of the Notes tab put words in the sweep.
        expect([...new Set(atRest.map((r) => notesTabBlockOf(r.at)))],
          "a block of the Notes tab showed no words")
          .toEqual(expect.arrayContaining([...NOTES_TAB_BLOCKS]));
        await armNotesTabConfirms(user);
        await motionAtRest(panel);
        const armed = sweep(root, panel, `${tab}, with every confirm armed`, true);
        // GROUND for the second pass: the words on the two confirms that
        // carry words of their own are in it.
        for (const name of [NOTES_TAB_CONFIRMS.run, NOTES_TAB_CONFIRMS.saved]) {
          const confirm = screen.getByRole("button", { name });
          expect(armed.some((r) => confirm.contains(r.at)), `the words on ${name} were not swept`)
            .toBe(true);
        }
        read.push(...armed);
      }
      if (!search) {
        // Every other tab's switches and boxes the other way from rest,
        // swept again. A group of radios is on screen in both states at
        // rest already, one on and the rest off.
        await flipEveryToggle(panel);
        await motionAtRest(panel);
        read.push(...sweep(root, panel, `${tab}, with every toggle flipped`, isNotes));
      }
      const removeKey = within(panel).queryByRole("button", { name: /^remove api key$/i });
      if (removeKey) {
        // The key's removal asked for: its confirm is danger words of its own.
        await user.click(removeKey);
        const confirm = await within(panel).findByRole("button", { name: /^remove$/i });
        await motionAtRest(panel);
        const armed = sweep(root, panel, `${tab}, with the key's removal asked for`, false);
        // GROUND: the confirm's words are in it.
        expect(armed.some((r) => confirm.contains(r.at)), "the key's removal confirm was not swept")
          .toBe(true);
        read.push(...armed);
      }
    }
    // GROUND: every tab put words in the sweep, the Notes tab was one of
    // them, and the lines the Security tab drew in the body's ink are in it:
    // what is typed into the key and both proxy fields, and "Test stored key".
    for (const tab of tabs) {
      expect(perTab.get(tab)?.filter((r) => r.what === "words").length,
        `the ${tab} tab showed no words`).toBeGreaterThan(0);
    }
    expect(notesTab, "no tab held the Notes tab's blocks").not.toBeNull();
    for (const field of ["API key input", "Proxy URL input", "Proxy alias input"]) {
      expect(read.some((r) => r.what === "typed" && r.words === field),
        `what is typed into "${field}" was not swept`).toBe(true);
    }
    expect(read.some((r) => r.what === "words" && /^test stored key$/i.test(r.words)),
      "\"Test stored key\" was not swept").toBe(true);

    // GROUND: toggles were flipped, in more than the Models tab.
    expect(flipped, "the sweep flipped fewer toggles than the Models tab alone has")
      .toBeGreaterThan(3);
    expect(read.filter((r) => r.faults.length > 0).map(described),
      "a line in the right panel is painted in an ink this panel does not resolve, or under AA")
      .toEqual([]);

    // POSITIVE CONTROL for the first question and for the panel's own colour
    // rule at once: the same sweep, over the same panel, read against the
    // sheet with that rule taken out, reports the Security tab's lines in
    // the body's ink, above the panel and under the floor.
    await openRightPanelTab(user, tabs.find((t) => perTab.get(t)!
      .some((r) => /^test stored key$/i.test(r.words)))!, queryClient);
    const stripped = root.clone();
    stripped.walkRules((rule) => {
      if (resolvedSelectors(rule).includes(".glass-right")) {
        rule.walkDecls(/^color$/, (decl) => {
          decl.remove();
        });
      }
    });
    const withoutRule = sweep(stripped, panelOf(), "without the panel's colour rule", false);
    const stored = withoutRule.find((r) => /^test stored key$/i.test(r.words));
    expect(stored?.faults.some((f) => f.includes("above the panel")),
      "without the rule the sweep did not see the body's ink").toBe(true);
    expect(stored?.faults.some((f) => f.startsWith("reads")),
      "without the rule the sweep read the body's ink as readable").toBe(true);

    // POSITIVE CONTROLS, one probe per way a line fails or is let off, each
    // added to the open tab and read by the same sweep.
    const panel = panelOf();
    const tabPanel = screen.getByRole("tabpanel");
    const probeOf = (host: Element, style: string | null): HTMLParagraphElement => {
      const p = document.createElement("p");
      p.textContent = "a probe line";
      if (style !== null) p.setAttribute("style", style);
      host.appendChild(p);
      return p;
    };
    const readOf = (probe: Element, tokens = false): Reading[] =>
      sweep(root, panel, "probe", tokens).filter((r) => r.at === probe);
    const panelInk = declarationOnSelector(root, ".glass-right", /^--color-es-text-light$/);
    expect(panelInk, "the panel's rule declares no text token").not.toBeNull();
    for (const { style, fault, tokens } of [
      // an ink under the floor: a token the panel resolves to the dark
      // shell's quiet ink;
      { style: "color: var(--es-quiet-72)", fault: "reads", tokens: false },
      // the panel's own ink at a fifth of its opacity, and the same through
      // an ancestor's opacity rather than its own;
      { style: "opacity: 0.2", fault: "reads", tokens: false },
      // a colour spelled out, where the token question is asked.
      { style: `color: ${panelInk}`, fault: "spelled out", tokens: true },
    ]) {
      const probe = probeOf(tabPanel, style);
      try {
        const got = readOf(probe, tokens);
        expect(got, "the sweep did not read the probe").toHaveLength(1);
        expect(got[0].faults.some((f) => f.startsWith(fault) || f.includes(fault)),
          `the sweep passed "${style}", which it should report as ${fault}: ${got[0].faults}`)
          .toBe(true);
      } finally {
        probe.remove();
      }
    }
    const dim = document.createElement("div");
    dim.setAttribute("style", "opacity: 0.2");
    tabPanel.appendChild(dim);
    try {
      const probe = probeOf(dim, null);
      expect(readOf(probe)[0]?.faults.some((f) => f.startsWith("reads")),
        "the sweep passed a line an ancestor dims to a fifth").toBe(true);
    } finally {
      dim.remove();
    }
    // POSITIVE CONTROL for the let-off's reach: a line that says it is
    // disabled, and is not a control or inside one, is held to the floor.
    for (const mark of ['aria-disabled="true"', "data-disabled"]) {
      const probe = probeOf(tabPanel, "color: var(--es-quiet-72)");
      const [name, value] = mark.includes("=") ? mark.split("=") : [mark, ""];
      probe.setAttribute(name, value.replace(/"/g, ""));
      try {
        expect(readOf(probe)[0]?.faults.some((f) => f.startsWith("reads")),
          `a line marked ${mark} outside any control was let off the floor`).toBe(true);
      } finally {
        probe.remove();
      }
    }
    // POSITIVE CONTROL for the two lets-off: a failing line inside a
    // switched-off button is not held to the floor, the same line in a live
    // one is; a line in a shut Collapse is not read, in an open one it is.
    for (const disabled of [true, false]) {
      const button = document.createElement("button");
      button.disabled = disabled;
      tabPanel.appendChild(button);
      try {
        const probe = probeOf(button, "color: var(--es-quiet-72)");
        const got = readOf(probe);
        expect(got, "the sweep did not read the probe in a button").toHaveLength(1);
        expect(got[0].faults.length > 0,
          disabled ? "a switched-off control was held to the floor"
            : "a live control was let off the floor").toBe(!disabled);
      } finally {
        button.remove();
      }
    }
    for (const open of [false, true]) {
      const shut = document.createElement("div");
      shut.setAttribute("data-slot", "collapse");
      shut.setAttribute("data-open", String(open));
      tabPanel.appendChild(shut);
      try {
        const probe = probeOf(shut, null);
        expect(readOf(probe), open ? "an open Collapse was not read" : "a shut Collapse was read")
          .toHaveLength(open ? 1 : 0);
      } finally {
        shut.remove();
      }
    }
    await everyRequestAnswered(fetchMock);
  }, 120_000);
});

describe("the Security tab's danger actions", () => {
  // "Remove API key", its confirm "Remove" and "Remove proxy" are ghost
  // actions beside ghost actions: "Test stored key" in the key's rows,
  // "Cancel" beside the confirm. They take the island's danger ink for
  // their words, and nothing else of the Notes island's buttons: the class
  // that carries that ink there also draws a border and a fill, and on
  // these three it made one row of bare words hold one boxed button.
  beforeAll(() => {
    MotionGlobalConfig.skipAnimations = true;
  });
  afterAll(() => {
    MotionGlobalConfig.skipAnimations = false;
  });
  afterEach(() => {
    cleanup();
    closeRightPanel();
  });

  /** The box a button draws around its words at rest: the colours it lays
   *  under them and its border, as the cascade and its style give them. */
  const boxOf = (root: Root, button: Element) => ({
    fill: ownPaints(root, button),
    border: ownDeclaration(root, button, /^(border|border-color)$/)?.value ?? null,
  });

  it("draw the same box as the ghost action beside them, in the island's danger ink",
    async () => {
      const root = parseSheet(css);
      const user = userEvent.setup();
      const { queryClient } = mountRightPanel({ proxyConfigured: true });
      await screen.findAllByRole("tab");
      await openRightPanelTab(user, "Security", queryClient);
      const panel = screen.getByRole("tabpanel");
      const test = within(panel).getByRole("button", { name: /^test stored key$/i });
      const removeKey = within(panel).getByRole("button", { name: /^remove api key$/i });
      const removeProxy = within(panel).getByRole("button", { name: /^remove proxy$/i });
      const bare = boxOf(root, test);
      // GROUND: the ghost action beside them lays no fill under its words.
      expect(bare.fill, "\"Test stored key\" lays a fill under its words").toEqual([]);
      const judge = (name: string, danger: Element, beside: Element) => {
        expect(boxOf(root, danger), `${name} draws a box its neighbour does not`)
          .toEqual(boxOf(root, beside));
        expect(inkOf(root, danger).written, `${name} is not in the island's danger ink`)
          .toBe("var(--es-island-danger-ink)");
      };
      judge("Remove API key", removeKey, test);
      judge("Remove proxy", removeProxy, test);
      // The confirm replaces the key's trigger, beside its own "Cancel".
      await user.click(removeKey);
      const confirm = await within(panel).findByRole("button", { name: /^remove$/i });
      judge("Remove", confirm, within(panel).getByRole("button", { name: /^cancel$/i }));
      // POSITIVE CONTROL: the Notes island's danger class on the same button
      // is read as a box of its own.
      removeProxy.classList.add("persona-danger-action");
      try {
        expect(boxOf(root, removeProxy), "the island's box was not read").not.toEqual(bare);
      } finally {
        removeProxy.classList.remove("persona-danger-action");
      }
    }, 60_000);

  /** The attribute that stands in for a pointer or keyboard state. */
  const STATE_PROBE = "data-state-probe";

  /** The sheet as it applies to an element carrying STATE_PROBE while it is
   *  hovered or keyboard-focused. jsdom has no pointer and never matches
   *  `:hover`, and the compiler puts every `hover:` utility under
   *  `@media (hover: hover)`, which the cascade readers do not take. So in a
   *  copy of the sheet that media query is opened, and the state
   *  pseudo-class is rewritten to the probe attribute: the element that
   *  carries it is read in that state, every other element at rest, and the
   *  cascade, the inline style and the var() chase are the readers' own. */
  const sheetIn = (root: Root, state: "hover" | "focus-visible"): Root => {
    const copy = root.clone();
    if (state === "hover") {
      copy.walkAtRules("media", (at) => {
        if (/^\(\s*hover\s*:\s*hover\s*\)$/.test(at.params.trim())) at.replaceWith(at.nodes ?? []);
      });
    }
    // Not an escaped `\:hover` inside a class name (`.hover\:bg-muted`):
    // only the pseudo-class itself.
    const pseudo = new RegExp(`(?<!\\\\):${state}(?![\\w-])`, "g");
    copy.walkRules((rule) => {
      rule.selector = rule.selector.replace(pseudo, `[${STATE_PROBE}]`);
    });
    return copy;
  };

  /** The worst ratio a button's words reach in `state`, over its own fill
   *  in that state and everything above it inside the panel, on each of
   *  the panel's grounds, the mist's veil over the darkest fog among them. */
  const worstIn = (
    root: Root, panel: Element, button: Element, state: "hover" | "focus-visible",
    inkOverride?: string,
  ): { ratio: number; fill: string[] } => {
    const sheet = sheetIn(root, state);
    button.setAttribute(STATE_PROBE, "");
    try {
      const ink = inkOverride ?? inkOf(sheet, button).colour;
      const fill = ownPaints(sheet, button);
      return { ratio: worstRatioOverStack(ink, stackOf(sheet, panel, button), panelGrounds()).ratio, fill };
    } finally {
      button.removeAttribute(STATE_PROBE);
    }
  };

  it("hold AA on the fog grounds when hovered or keyboard-focused, as \"Test stored key\" "
     + "beside them does", async () => {
    const root = parseSheet(css);
    const user = userEvent.setup();
    const { queryClient } = mountRightPanel({ proxyConfigured: true });
    await screen.findAllByRole("tab");
    await openRightPanelTab(user, "Security", queryClient);
    const tab = screen.getByRole("tabpanel");
    const panel = tab.closest<HTMLElement>(".glass-right");
    expect(panel, "the tab is not on the right panel").not.toBeNull();
    const test = within(tab).getByRole("button", { name: /^test stored key$/i });
    const removeKey = within(tab).getByRole("button", { name: /^remove api key$/i });
    const removeProxy = within(tab).getByRole("button", { name: /^remove proxy$/i });

    // GROUND: the probe reaches the hover state. "Test stored key" lays no
    // fill at rest (the row above) and the ghost variant's fill on hover;
    // it clears AA there, which is the bar its neighbours are held to.
    const neighbour = worstIn(root, panel!, test, "hover");
    expect(neighbour.fill, "the hover state was not read: no ghost fill under \"Test stored key\"")
      .not.toEqual([]);
    expect(ownPaints(root, test), "the probe leaked into the resting sheet").toEqual([]);
    expect(neighbour.ratio, "\"Test stored key\" on hover").toBeGreaterThanOrEqual(AA_NORMAL);
    // POSITIVE CONTROL: the island's danger ink as it stands at rest, laid
    // on that hover fill, is under the floor on the mist's darkest ground.
    // That is the reading the three danger actions had before this fix,
    // so the meter can see it.
    const restInk = inkOf(root, removeProxy).colour;
    expect(worstIn(root, panel!, removeProxy, "hover", restInk).ratio,
      "the resting danger ink clears AA on the hover fill; this row proves nothing")
      .toBeLessThan(AA_NORMAL);

    const judge = (name: string, danger: Element) => {
      for (const state of ["hover", "focus-visible"] as const) {
        const read = worstIn(root, panel!, danger, state);
        const beside = worstIn(root, panel!, test, state);
        expect(read.fill, `${name} on ${state} lays a fill "Test stored key" does not`)
          .toEqual(beside.fill);
        expect(read.ratio, `${name} on ${state}`).toBeGreaterThanOrEqual(AA_NORMAL);
      }
    };
    judge("Remove API key", removeKey);
    judge("Remove proxy", removeProxy);
    await user.click(removeKey);
    const confirm = await within(tab).findByRole("button", { name: /^remove$/i });
    judge("Remove", confirm);
  }, 60_000);
});

describe("the shell the right panel is painted in", () => {
  // The rows above mount the panel on its own, and read every element from
  // the panel down to a line. What is ABOVE the panel is the shell's: a
  // wrapper there dimmed at 90% dims every line in the panel with it, and no
  // row that mounts the panel alone can see it. So the shell is mounted as
  // the app mounts it, and every element from the panel up to the document
  // is asked for its opacity, the sheet and the inline style both, at rest.
  beforeAll(() => {
    MotionGlobalConfig.skipAnimations = true;
  });
  afterAll(() => {
    MotionGlobalConfig.skipAnimations = false;
  });
  afterEach(() => {
    cleanup();
    closeRightPanel();
  });

  /** Every element from `panel` up to the document that is painted at an
   *  opacity other than 1, by its tag and classes. */
  const dimmedAbove = (root: Root, panel: Element): string[] => {
    const out: string[] = [];
    for (let node: Element | null = panel; node; node = node.parentElement) {
      const opacity = ownOpacity(root, node);
      if (opacity !== 1) out.push(`<${node.tagName.toLowerCase()} class="${node.className}"> at ${opacity}`);
    }
    return out;
  };

  it("lays the panel under no dimming, from the panel up to the document", async () => {
    const root = parseSheet(css);
    mockFetch(rightPanelRoutes());
    render(createElement(Providers, null, createElement(AppShell)));
    const panel = await screen.findByRole("complementary", { name: "Models, security and notes" });
    await motionAtRest(document.body);
    // GROUND: the panel is the shell's, inside the frame, the stage and the
    // page the shell builds, so the walk crosses every one of them.
    for (const shell of [".elysium-frame", ".elysium-stage", ".elysium-page"]) {
      expect(panel.closest(shell), `the panel is not inside ${shell}`).not.toBeNull();
    }
    expect(dimmedAbove(root, panel)).toEqual([]);
    // POSITIVE CONTROL: the frame dimmed to 90% in its style is reported.
    const frame = panel.closest(".elysium-frame")!;
    const own = frame.getAttribute("style");
    frame.setAttribute("style", "opacity: 0.9");
    try {
      expect(dimmedAbove(root, panel)).toEqual([`<div class="elysium-frame"> at 0.9`]);
    } finally {
      if (own === null) frame.removeAttribute("style");
      else frame.setAttribute("style", own);
    }
  }, 60_000);
});

describe("the lines the right panel cuts short", () => {
  // KURAL 4.4 in the form it binds today: a line the panel may cut with an
  // ellipsis at its 300 px floor keeps its whole words reachable, as a
  // title on the box that cuts it or on one around it. jsdom lays nothing
  // out, so whether a line IS cut is a question for the browser; what is
  // asked here is the structure, on every box the compiled sheet gives an
  // ellipsis, in every tab and in the states the ink sweep draws. The
  // generation card's "Default sampling and context" was cut at 300 with no
  // title, while the parameter support was unknown.
  beforeAll(() => {
    MotionGlobalConfig.skipAnimations = true;
  });
  afterAll(() => {
    MotionGlobalConfig.skipAnimations = false;
  });
  afterEach(() => {
    cleanup();
    closeRightPanel();
  });

  /** Every box in `panel` that ends its words in an ellipsis, with what
   *  keeps its whole words from being reachable. */
  function cutShort(root: Root, panel: Element): { words: string; faults: string[] }[] {
    const out: { words: string; faults: string[] }[] = [];
    for (const box of panel.querySelectorAll("*")) {
      if (box.closest('[data-slot="collapse"][data-open="false"]')) continue;
      const ellipsis = winningDeclaration(root, box, /^text-overflow$/);
      if (ellipsis?.value.trim() !== "ellipsis") continue;
      const words = (box.textContent ?? "").replace(/\s+/g, " ").trim();
      if (words === "") continue;
      const titled = box.closest("[title]");
      const title = (titled?.getAttribute("title") ?? "").replace(/\s+/g, " ").trim();
      out.push({ words, faults: title.includes(words) ? [] : ["no title with the whole of it"] });
    }
    // A field that ends its words in an ellipsis cuts its placeholder too,
    // and the field is where the placeholder's whole words are kept.
    for (const field of panel.querySelectorAll("input[placeholder], textarea[placeholder]")) {
      if (field.closest('[data-slot="collapse"][data-open="false"]')) continue;
      const ellipsis = winningDeclaration(root, field, /^text-overflow$/);
      if (ellipsis?.value.trim() !== "ellipsis") continue;
      const words = (field.getAttribute("placeholder") ?? "").trim();
      const title = (field.getAttribute("title") ?? "").trim();
      out.push({ words, faults: title.includes(words) ? [] : ["no title with the whole of it"] });
    }
    return out;
  }

  it("carries its whole words as a title, in every tab and state", async () => {
    const root = parseSheet(css);
    const user = userEvent.setup();
    const { queryClient } = mountRightPanel();
    await screen.findAllByRole("tab");
    const read: { when: string; words: string; faults: string[] }[] = [];
    for (const tab of rightPanelTabNames()) {
      await openRightPanelTab(user, tab, queryClient);
      const panel = screen.getByRole("tabpanel").closest<HTMLElement>(".glass-right")!;
      read.push(...cutShort(root, panel).map((r) => ({ when: tab, ...r })));
      if (within(panel).queryByRole("textbox", { name: "Search models" })) {
        // The generation card with a model of unknown parameters chosen.
        act(() => useUiStore.setState({ selectedModelId: DRAWING_MODEL.id }));
        await motionAtRest(panel);
        // GROUND: the card says it cannot tell, in its pill.
        expect(within(panel).getByTestId("generation-settings-trigger"))
          .toHaveTextContent(/unknown/i);
        read.push(...cutShort(root, panel).map((r) => ({ when: `${tab}, unknown`, ...r })));
      }
    }
    // The generation card with settings of the reader's own: saved stop
    // sequences make it custom, and its subtitle says so.
    cleanup();
    closeRightPanel();
    const custom = mountRightPanel({ stopSequences: ["The End."] });
    await screen.findAllByRole("tab");
    await openRightPanelTab(user, "Models", custom.queryClient);
    const trigger = await screen.findByTestId("generation-settings-trigger");
    await waitFor(() => expect(trigger).toHaveTextContent(/^.*custom sampling and context/i));
    const customPanel = screen.getByRole("tabpanel").closest<HTMLElement>(".glass-right")!;
    read.push(...cutShort(root, customPanel).map((r) => ({ when: "Models, custom", ...r })));
    // GROUND: the sweep met cut-short lines, the generation card's among them,
    // in both of its wordings.
    expect(read.length).toBeGreaterThan(0);
    expect(read.some((r) => /^default sampling and context$/i.test(r.words)),
      "the generation card's subtitle was not read").toBe(true);
    expect(read.some((r) => /^custom sampling and context$/i.test(r.words)),
      "the generation card's subtitle was not read with custom settings").toBe(true);
    expect(read.some((r) => r.words.includes(NEW_LIMIT_PROMPT)),
      "the new limit's placeholder was not read").toBe(true);
    expect(read.filter((r) => r.faults.length > 0)
      .map((r) => `${r.when}: "${r.words.slice(0, 60)}" ${r.faults.join("; ")}`)).toEqual([]);

    // POSITIVE CONTROL: a box the sheet truncates with no title is reported,
    // and the same box with its words as a title is not.
    const panel = screen.getByRole("tabpanel").closest(".glass-right")!;
    const probe = document.createElement("span");
    probe.className = "truncate";
    probe.textContent = "a probe line cut short";
    screen.getByRole("tabpanel").appendChild(probe);
    try {
      expect(cutShort(root, panel).filter((r) => r.faults.length > 0).map((r) => r.words))
        .toEqual(["a probe line cut short"]);
      probe.setAttribute("title", "a probe line cut short");
      expect(cutShort(root, panel).filter((r) => r.faults.length > 0)).toEqual([]);
    } finally {
      probe.remove();
    }
  }, 120_000);
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
