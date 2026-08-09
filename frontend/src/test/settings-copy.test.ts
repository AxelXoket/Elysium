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

describe("Settings copy conventions", () => {
  it("has settings screens to check", () => {
    expect(FILES.length).toBeGreaterThan(4);
  });

  it("uses the named type roles, not ad-hoc pixel sizes", () => {
    // The scale used to be spelled three ways - a CSS class, Tailwind's
    // `text-xs`, and `text-[11px]` written out by hand eighteen times - so
    // nothing kept them in step and the section heading ended up SMALLER than
    // the labels beneath it.
    for (const file of FILES) {
      const src = copyOf(file);
      const adhoc = [...src.matchAll(/text-\[\d+px\]/g)].map((m) => m[0]);
      // The install log is machine output, not prose, and keeps its own size.
      const offenders = adhoc.filter((c) => c !== "text-[10px]");
      expect(offenders, `${file} sets a font size by hand: ${offenders}`)
        .toEqual([]);
    }
  });

  it("keeps headings, labels and buttons in sentence case", () => {
    // "Message contrast", "Bubble finish", "Performed replies", "Standing tone",
    // "Reference voices" - the convention almost everywhere. Two files held
    // out with "OpenRouter API Key" and "Vault Passphrase".
    const PROPER = new Set([
      "OpenRouter", "API", "Elysium", "WebView2", "GPU", "VRAM", "PNG", "JPEG",
      "WebP", "URL", "Fish", "Audio", "Chatterbox", "XTTS", "S2", "Pro",
      "Whisper", "AA", "AAA", "Windows", "Python", "CUDA", "NVIDIA", "I",
    ]);
    const failures: string[] = [];
    for (const file of FILES) {
      for (const text of stringsIn(file)) {
        // Only short label-shaped strings; a sentence legitimately contains
        // capitalised words mid-way.
        const words = text.split(/\s+/);
        if (words.length > 5 || /[.:?!]/.test(text)) continue;
        const bad = words
          .slice(1)
          .filter((w) => /^[A-Z][a-z]{2,}$/.test(w) && !PROPER.has(w));
        if (bad.length > 0) failures.push(`${file}: "${text}" -> ${bad}`);
      }
    }
    expect(failures, `Title Case found:\n${failures.join("\n")}`).toEqual([]);
  });

  it("uses one dash character", () => {
    // " - " is what this codebase already uses ~60 times; an em dash or a
    // middle dot in three places is the inconsistency, not the fix.
    for (const file of FILES) {
      for (const text of stringsIn(file)) {
        expect(text, `${file}: em dash in "${text}"`).not.toMatch(/\u2014/);
        expect(text, `${file}: middle dot separator in "${text}"`)
          .not.toMatch(/\s·\s/);
      }
    }
  });

  it("avoids contractions", () => {
    // "could not reach", "cannot run yet", "will not run", "do not match" -
    // the register everywhere else. One "Couldn't" was the outlier.
    for (const file of FILES) {
      for (const text of stringsIn(file)) {
        expect(text, `${file}: contraction in "${text}"`)
          .not.toMatch(/\b\w+n't\b|\b\w+'(re|ll|ve)\b/);
      }
    }
  });

  it("does not HTML-escape apostrophes in prose", () => {
    // `&apos;` in one option label while every other string uses a real
    // apostrophe - it renders the same and reads differently in the source.
    for (const file of FILES) {
      expect(copyOf(file), `${file} uses &apos;`).not.toMatch(/&apos;/);
    }
  });

  it("keeps helper sentences short enough to read", () => {
    // Not a style rule - a 113-character two-sentence error message under a
    // text field is where people stop reading.
    for (const file of FILES) {
      for (const text of stringsIn(file)) {
        expect(text.length, `${file}: over-long copy "${text}"`)
          .toBeLessThanOrEqual(90);
      }
    }
  });
});

describe("a control says the same thing to everybody", () => {
  it("a toggle row's aria-label matches the label it displays", () => {
    // These drifted once: the row read "Performed replies" on screen while a
    // screen reader was told "Voice replies", so the two users of one control
    // were given different names for it. Renaming visible copy is easy to do
    // and easy to half-do.
    //
    // Scoped to `settings-toggle-row` on purpose. A section landmark and a
    // slider legitimately carry names that differ from the nearest
    // settings-label, and a looser rule reports those as faults - a test that
    // cries wolf is a test that gets deleted.
    const offences: string[] = [];
    for (const file of FILES) {
      const src = copyOf(file);
      // Bounded to the row's OWN body: split alone lets the last segment run
      // to the end of the file and pick up an unrelated control's label.
      const rows = src
        .split("settings-toggle-row")
        .slice(1)
        .map((seg) => seg.slice(0, 400));
      for (const row of rows) {
        const aria = /aria-label="([^"]+)"/.exec(row);
        const visible = /className="settings-label">([^<{]+)</.exec(row);
        if (!aria || !visible) continue;
        if (aria[1].trim() !== visible[1].trim()) {
          offences.push(`${file}: aria "${aria[1]}" vs visible "${visible[1]}"`);
        }
      }
    }
    expect(offences).toEqual([]);
  });
});
