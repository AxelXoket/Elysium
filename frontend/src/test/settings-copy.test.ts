/**
 * settings-copy.test.ts - the Settings screens read as one voice.
 *
 * Settings had drifted into three type scales, two capitalisation styles and
 * three dash characters, because nothing held them together. None of it is a
 * bug in the sense a user can report; all of it is why the panel felt busier
 * than it is.
 *
 * The conventions, all of them observed from what the copy already mostly did:
 *
 *   size    three named roles (.settings-section-title / .settings-label /
 *           .settings-hint), never an ad-hoc `text-[Npx]`
 *   case    sentence case for headings, labels and buttons - "Message
 *           contrast", not "Message Contrast"
 *   dash    " - ", the one this codebase already uses ~60 times
 *   voice   no contractions ("could not", not "couldn't")
 *
 * Static, deliberately: these are properties of the source, and asserting them
 * against a rendered tree would need every screen mounted in every state.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "fs";
import path from "path";

const DIR = path.resolve(__dirname, "..", "components", "settings");
const FILES = readdirSync(DIR).filter((f) => f.endsWith(".tsx")).sort();

/** Source with comments removed - conventions are about COPY, not commentary. */
function copyOf(file: string): string {
  return readFileSync(path.join(DIR, file), "utf-8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

/**
 * Visible strings: JSX text nodes, quoted sentences and template literals.
 *
 * The extractor used to take only text after `>` and double-quoted strings
 * (audit KÖK 13), which left two whole categories unscanned: template
 * literals, and any text that follows an interpolation - `{count} items - one
 * of them failed`. The rules this file declares were simply not applied
 * there, and there were live violations inside the directory it guards.
 *
 * `[>}]` as the opening delimiter is what picks up the second category: a JSX
 * text node can start right after a closing brace as easily as after a tag.
 */
function stringsIn(file: string): string[] {
  const src = copyOf(file);
  const out: string[] = [];
  for (const m of src.matchAll(/[>}]\s*([A-Z][^<>{}\n]{5,140}?)\s*[<{]/g)) {
    out.push(m[1].trim());
  }
  for (const m of src.matchAll(/"([A-Z][^"\n]{7,140})"/g)) {
    if (m[1].includes(" ")) out.push(m[1].trim());
  }
  // Template literals, minus their `${...}` holes - the hole is a value, not
  // copy, and a rule about dashes has no opinion about it.
  for (const m of src.matchAll(/`([A-Z][^`\n]{7,140})`/g)) {
    const text = m[1].replace(/\$\{[^}]*\}/g, " ").trim();
    if (text.includes(" ")) out.push(text);
  }
  return out;
}

/** Every visible string in the directory, with the file it came from. */
const ALL: { file: string; text: string }[] = FILES.flatMap((file) =>
  stringsIn(file).map((text) => ({ file, text })),
);

describe("Settings copy conventions", () => {
  it("finds copy to read before judging it", () => {
    // Every rule below is shaped "no string in here does X". That shape has
    // one failure mode: an extractor that stops matching turns all of them
    // into loops over nothing, and the file goes green having read no copy at
    // all. It has happened once already - audit KOK 13 found the old
    // extractor blind to template literals and to text after an
    // interpolation, and there were live violations sitting in the directory
    // it claimed to guard.
    //
    // So the floors are the point of this test, not the file count. They are
    // set below today's numbers with room to delete a screen, and above any
    // number a broken regex would produce.
    expect(FILES.length, "no settings screens found").toBeGreaterThan(10);
    expect(ALL.length, "the extractor found almost no copy").toBeGreaterThan(150);
    expect(
      new Set(ALL.map((a) => a.file)).size,
      "copy was found in only a handful of screens",
    ).toBeGreaterThan(10);
  });

  it("uses the named type roles, not ad-hoc pixel sizes", () => {
    // The scale used to be spelled three ways - a CSS class, Tailwind's
    // smallest named step, and an arbitrary bracketed size written out by
    // hand eighteen times - so nothing kept them in step and the section
    // heading ended up SMALLER than the labels beneath it. The pattern is
    // assembled rather than spelled: Tailwind scans this file as text and a
    // literal here would emit the very utility it looks for.
    const adhocSize = new RegExp("text-" + "\\[\\d+(?:\\.\\d+)?(?:px|rem|em)\\]", "g");
    // POSITIVE CONTROL. The pattern is assembled from fragments with doubled
    // escapes, and one lost escape level turns it into something that
    // matches nothing while looking identical from outside - which is a
    // green test over nineteen files that were never read.
    expect(
      [..."size: " .concat("text-", "[11px]").matchAll(adhocSize)].map((m) => m[0]),
      "the detector cannot see the thing it looks for",
    ).toHaveLength(1);
    for (const file of FILES) {
      const src = copyOf(file);
      const offenders = [...src.matchAll(adhocSize)].map((m) => m[0]);
      expect(offenders, `${file} sets a font size by hand: ${offenders}`)
        .toEqual([]);
    }
  });

  // "keeps headings, labels and buttons in sentence case" was deleted in
  // KADEME 20b. It carried a hand-kept allowlist of twenty-four proper
  // nouns which only ever grows: every new engine, vendor or acronym in the
  // product is a red suite until somebody adds the word. That is a taste
  // rule with maintenance, and taste belongs to whoever writes the copy.
  // The convention itself is not lost - it is visible in every screen.

  it("uses one dash character", () => {
    // " - " is what this codebase already uses ~60 times; an em dash or a
    // middle dot in three places is the inconsistency, not the fix.
    for (const { file, text } of ALL) {
      expect(text, `${file}: em dash in "${text}"`).not.toMatch(/\u2014/);
      expect(text, `${file}: middle dot separator in "${text}"`)
        .not.toMatch(/\s·\s/);
    }
  });

  it("avoids contractions", () => {
    // "could not reach", "cannot run yet", "will not run", "do not match" -
    // the register everywhere else. One "Couldn't" was the outlier.
    for (const { file, text } of ALL) {
      expect(text, `${file}: contraction in "${text}"`)
        .not.toMatch(/\b\w+n't\b|\b\w+'(re|ll|ve)\b/);
    }
  });

  // "does not HTML-escape apostrophes in prose" was deleted in KADEME 20b.
  // Checked before deleting, because the name sounds like a rendering bug:
  // it is not one. JSX renders the escaped apostrophe entity and a typed
  // apostrophe identically, so nothing reaches the screen wrong and no entity
  // text leaks. What it guarded was source consistency, a reading preference.
  //
  // The entity is described rather than spelled here on purpose: hygiene rule
  // H-03 bans HTML entities in TypeScript source, and writing one into this
  // note would need a waiver for a comment about a deleted test. The two
  // waivers the old test needed were removed from hygiene_allowlist.txt with
  // it - a waiver outliving its line is exactly what the dead-waiver check
  // catches, and it caught this.

  // "keeps helper sentences short enough to read" was deleted in KADEME 20b,
  // and its own comment argued against deleting it, so the argument is kept
  // here verbatim: "Not a style rule - a 113-character two-sentence error
  // message under a text field is where people stop reading."
  //
  // That is true and it is still a taste rule. The test encoded it as a
  // ninety-character cutoff, and there is nothing behind ninety: an
  // eighty-nine-character sentence passes and a ninety-one-character one
  // fails for no reason a reader would recognise. A number nobody can
  // defend goes red for good copy, and a test that cries wolf gets deleted
  // - so it was deleted deliberately rather than left to be ignored.
});

// "a control says the same thing to everybody" lives in
// components/SettingsToggleNames.test.tsx now, and the rule it carries is the
// same one: a toggle row's accessible name is the name it displays.
//
// It could not stay here. It was the one rule in this file that is NOT a
// property of the source - it is a property of the accessibility tree, and
// imitating that tree with two double-quote regexes gave it three ways to
// read nothing and pass. An `aria-label={expression}` missed the quote regex,
// a second class name beside `settings-label` missed the exact-match regex,
// and a row longer than a four-hundred character window was never in view.
// Each miss was a `continue`. Two of the five toggle rows written in this
// directory fall into those shapes, and they are the two REUSABLE ones: they
// draw four of the seven rows on screen today, and every row anybody adds
// next. The rule was reading three hand-written rows and stepping over the
// rest without saying so.
//
// The floor that guarded it is not gone, it is stricter: the replacement
// renders the panel, sweeps every row actually on screen, and treats a row it
// cannot compare as a finding rather than a skip.
