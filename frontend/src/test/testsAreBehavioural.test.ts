/**
 * The house rule enforced over the FRONTEND suite: a test that reads source
 * text to make an assertion is banned.
 *
 * backend/tests/test_tests_are_behavioural.py has enforced this over the
 * python suite since 2026-08-20. Nothing enforced it here, and the reason the
 * rule exists does not stop at a language boundary: a test that greps a file
 * passes when the line is present but dead, present in an unreachable branch,
 * present inside a string, or when the behaviour moved somewhere the grep no
 * longer looks. It reports coverage it does not have.
 *
 * THE THREE RULES, and why they are the ones
 * ------------------------------------------
 * FE-01  A function handed back as its own source text and then asserted on.
 *        `Function.prototype.toString` is the explicit spelling; a plain
 *        `fn.toString()` is the common one and is caught when the same line
 *        looks inside the result. It is NOT caught on its own, because
 *        `url.toString()` is ordinary and this suite's own mocks are full of
 *        it. Hard-gated, no exemption to argue.
 *
 * FE-02  A source file imported AS TEXT - the `?raw` query vite supports, and
 *        `import.meta.glob` with a raw query. Same shape as FE-03 with the
 *        read call hidden inside the bundler. Hard-gated for the same reason.
 *
 * FE-03  A read call whose surrounding lines name a file in one of this app's
 *        own source languages, in a file that also asserts. This is the one
 *        that needs judgement, so it is TRACKED rather than hard-gated
 *        everywhere except this file's own name, exactly as the python side
 *        does it. What the sweep already found is pinned below by the exact
 *        text of the offending line. A pinned line that no longer matches
 *        means somebody fixed it and the entry is stale; a hit anywhere else
 *        that is not pinned is a new one and fails the build.
 *
 * THE THREE LAYERS
 * ----------------
 * ALLOWLIST      an argued exemption, and the argument is checked: an entry
 *                needs a real sentence behind it, an allowlisted file is
 *                still scanned so what is being excused stays visible, and a
 *                file cannot sit in this column and the debt column at once.
 *                Empty today, because nothing has been argued yet.
 * OWNED_FILES    hard gate. Membership means neither registry can excuse a
 *                hit in that file, which is measured rather than assumed.
 *                This file owns itself.
 * KNOWN_PENDING  tracked, not enforced. Eleven files, pinned by exact line
 *                AND by how many times that line is hit: twenty-five real
 *                violations sat behind twenty pinned lines until the counts
 *                went in, so seven of one file's eight could have been paid
 *                off with the ledger saying nothing.
 *
 * WHY THIS FILE DOES NOT TRIP ITSELF
 * ----------------------------------
 * Its own samples have to spell out the shapes it looks for. So the source
 * extensions and the raw-import query are assembled from pieces at runtime,
 * the same trick the python file uses on ".py": the finished shapes exist
 * only in memory once this module has been imported. That is not a blanket
 * claim about every literal in the file. The pinned anchors and the list of
 * files the queue counted do spell extensions out, deliberately, because
 * they are far from any read call and the self-gate below measures whether
 * that is still true rather than trusting it.
 */

import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

/** frontend/src */
const SRC = path.resolve(__dirname, "..");

// Assembled, never typed whole. See the docstring's last paragraph.
const TS = "." + "ts";
const TSX = "." + "tsx";
const CSS = "." + "css";
const HTML = "." + "html";
/**
 * Every language this app ships source in, not only the two it is written in.
 * index.html and the plain .js config files are source too, and leaving them
 * out hid a live violation: a test in this suite reads index.html and asserts
 * on its text while six of its own siblings are pinned below for doing the
 * same thing to a .ts file.
 */
const SOURCE_EXTS = [TS, TSX, CSS, HTML, "." + "js", "." + "jsx",
                     "." + "mjs", "." + "cjs"].map((e) => e.slice(1));

/** Lines of look-around for a path near a read call. */
const WINDOW = 5;

/**
 * Below this, a clean sweep means the glob broke, not that the tree is clean.
 * 146 files under src/test today. The floor sits well under that, so an
 * ordinary deletion does not trip it and a broken glob does.
 */
const FILE_FLOOR = 100;

/**
 * FE-01. The shape is "turn a function into its own text, then assert on the
 * text". `Function.prototype.toString` is the explicit spelling and the rare
 * one; `fn.toString()` and `String(fn)` are what anybody actually writes.
 *
 * `fn.toString()` is not decidable on its own - `url.toString()` is ordinary
 * and lives in this suite's own mocks - so it counts only when the same line
 * also asserts on what came back, and only when the assertion is one that
 * looks INSIDE the string. That pairing is the violation; the coercion by
 * itself is not.
 *
 * `String(x)` is deliberately absent. Measured against the real tree it
 * matched `expect(String(url))` in four component tests and
 * `String(BOUNDARY_MAX_CHARS)` in a fifth: stringifying a URL or a number,
 * which is not this rule's business and never will be.
 */
const RE_FN_SOURCE = new RegExp(
  [
    String.raw`\bFunction\s*\.\s*prototype\s*\.\s*toString\b`,
    String.raw`\.toString\s*\(\s*\)[^\n]*\.(toContain|toMatch|includes)\b`,
    String.raw`\b(expect|assert)\b[^\n]*\.toString\s*\(\s*\)[^\n]*\.(toContain|toMatch|includes)\b`,
  ].join("|"),
);

/**
 * FE-02. Vite serves a module as text when the specifier carries the raw
 * query.
 *
 * The query is assembled rather than typed, the same way the extensions are:
 * spelled out, this pattern matched ITSELF once comments were stripped and
 * this file reported a violation it did not contain.
 */
const RAW_QUERY = "\\?" + "raw";
const RE_RAW_IMPORT = new RegExp(
  [
    "[\"'`][^\"'`\\n]*" + RAW_QUERY + "\\b[^\"'`\\n]*[\"'`]",
    String.raw`\bquery\s*:\s*["'\`][^"'\`\n]*\b` + "r" + "aw\\b",
    String.raw`\bas\s*:\s*["'\`]` + "r" + "aw[\"'`]",
  ].join("|"),
);

const RE_READ_CALL = /\breadFileSync\s*\(|\breadFile\s*\(|\breadFileAsync\s*\(/;

/**
 * A path literal naming one of this app's own source languages.
 *
 * The character class before the extension is not optional. `".css"` on its
 * own is not a path, it is what `file.endsWith(".css")` looks like, and this
 * suite writes that line in half a dozen whole-tree scanners. Allowing the
 * empty case turned every read within five lines of one of them into a hit.
 *
 * Backticks are in the quote class because a template literal is the ordinary
 * modern way to build a path, and leaving it out made the rule dodgeable by
 * changing a quote.
 */
const RE_SOURCE_PATH = new RegExp(
  "[\"'`][^\"'`\\n]*[A-Za-z0-9_)\\-]\\." +
    `(${SOURCE_EXTS.join("|")})` +
    "[\"'`]",
);

/**
 * A glob naming this app's source languages, which is how the whole-tree
 * scanners spell it.
 *
 * Held apart from RE_SOURCE_PATH because a glob has a `*` where a filename
 * has a name, and requiring a name character before the dot - which is what
 * keeps a bare `".css"` from counting - excluded every glob at the same time.
 * Measured: static-safety.test.ts asks a helper for every source file under a
 * brace glob and then reads and asserts on each one it returns. Thirty-three
 * read calls in that single file, and only thirteen were being seen: the
 * thirteen that happened to have a literal filename within five lines. A
 * ledger built on the other twenty being invisible is not a ledger.
 *
 * The glob itself is not written out in this comment. A brace glob contains
 * the two characters that end a block comment, and spelling it here ended
 * this one in the middle of a sentence.
 */
const RE_SOURCE_GLOB = new RegExp(
  "[\"'`][^\"'`\\n]*\\*\\.(?:" +
    `(?:${SOURCE_EXTS.join("|")})\\b` +
    "|\\{[^}\"'`\\n]*\\b(?:" + SOURCE_EXTS.join("|") + ")\\b[^}\"'`\\n]*\\})",
);

/**
 * Any assertion, not only vitest's. `expect.soft(`, `expect.poll(`, node's
 * `assert(` and a hand-rolled `invariant(` are all assertions, and matching
 * the literal `expect(` alone meant one character turned the whole rule off.
 */
const RE_ASSERTS = /\bexpect\b|\bassert\b|\binvariant\s*\(/;

/**
 * The other way a read call names a source file: it does not spell one path,
 * it filters a directory listing by a source extension.
 *
 * This is separate from RE_SOURCE_PATH on purpose. A bare `".css"` is not a
 * path and must not count as one - `if (file.endsWith(".css")) continue;`
 * appears beside half the whole-tree scanners in this suite and made every
 * nearby read a hit for the wrong reason. But `readdirSync(DIR).filter((f) =>
 * f.endsWith(".tsx"))` beside a read IS a test walking this app's source, and
 * dropping it lost four real entries the first time the path rule was
 * tightened.
 */
const RE_SOURCE_FILTER = new RegExp(
  "\\bendsWith\\s*\\(\\s*[\"'`]\\." + `(${SOURCE_EXTS.join("|")})` +
    "[\"'`]|\\\\\\.(" + SOURCE_EXTS.join("|") + ")x?\\$",
);

/**
 * The escape hatch, mirroring the python side's `ast.parse(` window: text fed
 * to a real parser is a structural claim, not a grep standing in for
 * behaviour. Named parsers plus the general shape, because the argument is
 * about parsing and not about which library does it.
 */
const RE_PARSER =
  /\b(createSourceFile|postcss|acorn|csstree|parse5|babelParse|parseSync|parseAst|parseCss)\s*[.(]|\b(JSON|[A-Za-z_$][\w$]*)\.parse\s*\(|\bparse\s*\(/;

const RE_DECLARATION =
  /\b(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=\n]*)?=\s*([\s\S]{0,600}?)(?:;|\n\s*(?=(?:export\s+)?(?:const|let|var|function|class|it|describe|import)\b))/g;

/**
 * Comments are removed before anything is scanned.
 *
 * Two failures came from not doing it. A commented-out read counted as a live
 * violation, and - the worse direction - writing `// nothing to do with
 * postcss(` on the line above a real read silenced the rule entirely, because
 * the parser escape hatch matched inside the comment. A one-line dodge is not
 * a gate.
 */
function withoutComments(text: string): string {
  // Character by character, not by regex. A regex-based stripper was tried
  // first and it ate real code: this suite writes `/\*[\s\S]*?\*\//` inside
  // its own comment strippers, and a naive block-comment pattern treats that
  // as an opening delimiter and blanks everything up to the next `*/` a
  // hundred lines later. Three genuine hits vanished that way, silently.
  //
  // Strings, template literals and regex literals are tracked so the `//` in
  // a URL and the `/*` in a pattern stay where they are. Newlines are kept so
  // every line number the sweep reports still points at the real line.
  let out = "";
  let i = 0;
  const canStartRegex = (): boolean => {
    for (let j = out.length - 1; j >= 0; j -= 1) {
      const c = out[j];
      if (/\s/.test(c)) continue;
      return "(,=:[!&|?{};+-*%^~<>".includes(c);
    }
    return true;
  };
  while (i < text.length) {
    const c = text[i];
    const next = text[i + 1];
    if (c === "/" && next === "/") {
      while (i < text.length && text[i] !== "\n") i += 1;
      continue;
    }
    if (c === "/" && next === "*") {
      i += 2;
      while (i < text.length && !(text[i] === "*" && text[i + 1] === "/")) {
        if (text[i] === "\n") out += "\n";
        i += 1;
      }
      i += 2;
      continue;
    }
    if (c === '"' || c === "'" || c === "`") {
      out += c;
      i += 1;
      while (i < text.length && text[i] !== c) {
        if (text[i] === "\\") {
          out += text.slice(i, i + 2);
          i += 2;
          continue;
        }
        out += text[i];
        i += 1;
      }
      out += text[i] ?? "";
      i += 1;
      continue;
    }
    if (c === "/" && canStartRegex()) {
      out += c;
      i += 1;
      let inClass = false;
      while (i < text.length && text[i] !== "\n") {
        if (text[i] === "\\") {
          out += text.slice(i, i + 2);
          i += 2;
          continue;
        }
        if (text[i] === "[") inClass = true;
        else if (text[i] === "]") inClass = false;
        else if (text[i] === "/" && !inClass) break;
        out += text[i];
        i += 1;
      }
      out += text[i] ?? "";
      i += 1;
      continue;
    }
    out += c;
    i += 1;
  }
  return out;
}

export interface Hit {
  file: string;
  rule: string;
  /** Exact text of the offending line, trimmed. The anchor. */
  line: string;
}

/**
 * Does this line use that identifier?
 *
 * Built by hand rather than with a `\b${name}\b` regex. `$` is a regex anchor,
 * so a name like `$css` produced a pattern that could never match anything and
 * the rule went quietly dead for it.
 */
function mentions(line: string, name: string): boolean {
  const isWord = (c: string) => c !== "" && /[\w$]/.test(c);
  let from = 0;
  for (;;) {
    const at = line.indexOf(name, from);
    if (at === -1) return false;
    const before = at === 0 ? "" : line[at - 1];
    const after = line[at + name.length] ?? "";
    if (!isWord(before) && !isWord(after)) return true;
    from = at + 1;
  }
}

/** Identifiers this file assigns from an expression naming a source path. */
function sourcePathNames(text: string): Set<string> {
  const names = new Set<string>();
  const re = new RegExp(RE_DECLARATION.source, "g");
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (RE_SOURCE_PATH.test(m[2])) names.add(m[1]);
  }
  return names;
}

/**
 * Every hit in one (file, text) pair. Pure - no disk, no glob - so the tests
 * below can hand it synthetic content and mean it.
 */
export function scanFile(file: string, raw: string): Hit[] {
  const text = withoutComments(raw);
  const hits: Hit[] = [];
  if (RE_FN_SOURCE.test(text)) {
    hits.push({
      file,
      rule: "FE-01",
      line: "a function handed back as its own source text",
    });
  }
  if (RE_RAW_IMPORT.test(text)) {
    hits.push({
      file,
      rule: "FE-02",
      line: "a source file imported as text",
    });
  }
  if (!RE_ASSERTS.test(text)) return hits;

  const names = sourcePathNames(text);
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i += 1) {
    if (!RE_READ_CALL.test(lines[i])) continue;
    const window = lines
      .slice(Math.max(0, i - WINDOW), Math.min(lines.length, i + WINDOW + 1))
      .join("\n");
    if (RE_PARSER.test(window)) continue;
    const named =
      RE_SOURCE_PATH.test(window) ||
      RE_SOURCE_GLOB.test(window) ||
      RE_SOURCE_FILTER.test(window) ||
      [...names].some((n) => mentions(lines[i], n));
    if (named) hits.push({ file, rule: "FE-03", line: lines[i].trim() });
  }
  return hits;
}

// ── Layer one: argued exemptions ────────────────────────────────────────────
//
// A whole-tree SHAPE claim with no single module's behaviour to observe
// instead earns an entry here, with the argument written out, the way the
// python side's nine entries do. Nothing has been argued yet, so this is
// empty rather than pre-filled.
//
// An entry here does NOT stop the file being scanned. It was written that way
// first and measured: a one-line entry with an empty string for a reason made
// a brand new violation invisible and no test objected, which is a back door
// wearing a registry's clothes. Allowlisted files are scanned like every
// other, their hits are counted, and the exemption only decides whether those
// hits fail the build. What is being excused stays on the page.
export const ALLOWLIST: Record<string, string> = {};

/** An argument has to be one. Anything shorter is a rubber stamp. */
const MIN_JUSTIFICATION = 40;

// ── Layer two: this file's own name, hard gate ──────────────────────────────
const SELF = "testsAreBehavioural.test" + TS;
export const OWNED_FILES: ReadonlySet<string> = new Set([SELF]);

// ── Layer three: tracked, not enforced ──────────────────────────────────────
//
// Compiled from a scanFile() pass over the real tree on 2026-08-30. Every
// entry is the same shape: a readFileSync result, string-matched or measured,
// standing in for behaviour that could have been observed instead. None of
// these files are this pass's to edit.
//
// The anchor is the exact trimmed text of the offending line, AND how many
// times that line occurs. The count is not decoration: static-safety.test.ts
// writes `const content = readFile(file);` eight separate times, and with a
// bare list of strings seven of those eight could be paid off and the ledger
// would report nothing. Twenty-five real violations were recorded as twenty
// lines until this was measured. A count that no longer matches fails the
// sweep in both directions, so the debt can only be argued down one hit at a
// time, in writing.
//
// Extensions are held apart from the rest of each line, the same way they are
// everywhere else in this file.
const READ_CSS_BESIDE_ME =
  'const CSS = readFileSync(path.resolve(__dirname, "../../index' +
  CSS +
  '"), "utf-8");';
const READ_CSS_FROM_SRC =
  'const css = readFileSync(path.join(SRC, "index' + CSS + '"), "utf-8");';

export const KNOWN_PENDING: Record<
  string,
  ReadonlyArray<readonly [line: string, occurrences: number]>
> = {
  ["components/AppShell.test" + TSX]: [["const css = readFileSync(", 1]],
  ["components/ConfirmPlacement.test" + TSX]: [[READ_CSS_BESIDE_ME, 1]],
  ["components/EditBoxAppearance.test" + TSX]: [["const CSS = readFileSync(", 1]],
  ["components/PanelToggleAppearance.test" + TSX]: [[READ_CSS_BESIDE_ME, 1]],
  ["css-contract.test" + TS]: [
    ['const CSS_RAW = readFileSync(path.join(SRC, "index' + CSS + '"), "utf-8");', 1],
  ],
  ["lib/contrast.test" + TS]: [
    ['const css = readFileSync(CSS_PATH, "utf-8");', 1],
  ],
  ["lib/motionTokens.test" + TS]: [
    ["const providers = readFileSync(", 1],
    [READ_CSS_FROM_SRC, 1],
  ],
  ["palette-guard.test" + TS]: [
    ['const colors = parseColors(readFileSync(CSS, "utf-8"));', 1],
    ['const css = readFileSync(CSS, "utf-8");', 3],
    ['const css = readFileSync(CSS, "utf8");', 2],
  ],
  ["right-panel-readability.test" + TS]: [
    ["const src = readFileSync(", 1],
    ['out.push({ file: full, text: readFileSync(full, "utf8") });', 1],
  ],
  ["settings-copy.test" + TS]: [
    ['return readFileSync(path.join(DIR, file), "utf-8")', 1],
  ],
  ["static-safety.test" + TS]: [
    // PAID, 31 August 2026. This was the line that read `uiStore.ts` and
    // parsed `partialize` out of it by balancing braces. The persisted key
    // list is one exported constant now, so S-09b and S-09c import it
    // instead of reading the file - and what the source scan could never
    // answer (what those keys HOLD at runtime) is answered by a test that
    // reads the blob zustand actually wrote.

    // Nine of them, and the number is the point. See the note above. It was
    // eight until the rule learned to read a brace glob: the ninth sits
    // beside a whole-tree scan whose only clue is the glob it was given.
    ["const content = readFile(file);", 9],
    ["const denylist = readFile(", 1],
    [
      "tags.push(...findFieldTags(readFile(file), " +
        "path.relative(SRC_DIR, file)));",
      1,
    ],
    ['const main = readFile(path.resolve(SRC_DIR, "src", "main' + TSX + '"));', 1],
    // index.html, and it is the one this sweep found that nobody had counted.
    // Six of its siblings in this same file were already written down as debt
    // while this one read a source document and asserted on its text with
    // nothing saying so, because the first cut of the rule only knew about
    // .ts, .tsx and .css.
    ['const html = readFile(path.resolve(SRC_DIR, "index' + HTML + '"));', 1],
    ["const content = readFile(draftStorePath);", 1],
  ],
};

/**
 * Everything under src/test, not only the files named *.test.ts.
 *
 * The python side sweeps every .py in its tests directory, helpers included,
 * and the reason is the obvious dodge: move the read into a helper and the
 * gate that only looks at test files never sees it again. src/test/helpers
 * and src/test/mocks read nothing today, measured, and they are inside the
 * sweep so that stays true.
 */
function testFiles(): string[] {
  const out: string[] = [];
  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir)) {
      const full = path.join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.tsx?$/.test(entry)) out.push(full);
    }
  };
  walk(path.join(SRC, "test"));
  return out.sort();
}

/** Path as the registries spell it: relative to src/test, forward slashes. */
function key(full: string): string {
  return path.relative(path.join(SRC, "test"), full).split(path.sep).join("/");
}

/**
 * Does a hit in this file still count?
 *
 * A file this pass owns is never excused, by an allowlist entry or by a pin.
 * That is what "hard gate" has to mean; the python side spells the same
 * distinction out where it decides whether a hit is a violation.
 *
 * Pulled out as a function on purpose. Inlined in the sweep, dropping the
 * owned-files half changed nothing observable while ALLOWLIST was empty, so
 * the promise "membership buys something" could not be measured at all.
 */
export function isEnforced(
  file: string,
  allowlist: Record<string, string>,
  owned: ReadonlySet<string>,
): boolean {
  return owned.has(file) || !(file in allowlist);
}

interface Sweep {
  /** Every hit in the tree, allowlisted files included. */
  hits: Hit[];
  /** Hits that still count: not excused by an argued exemption. */
  enforced: Hit[];
  scanned: number;
  /** Top-level directory under src/test -> how many files came from it. */
  perDirectory: Record<string, number>;
}

function sweep(): Sweep {
  const files = testFiles();
  const hits: Hit[] = [];
  const perDirectory: Record<string, number> = {};
  for (const full of files) {
    const name = key(full);
    const head = name.includes("/") ? name.slice(0, name.indexOf("/")) : ".";
    perDirectory[head] = (perDirectory[head] ?? 0) + 1;
    hits.push(...scanFile(name, readFileSync(full, "utf8")));
  }
  const enforced = hits.filter(
    (h) => isEnforced(h.file, ALLOWLIST, OWNED_FILES),
  );
  return { hits, enforced, scanned: files.length, perDirectory };
}

/** How many times each line was hit, for one file. */
function tally(hits: Hit[], file: string): Map<string, number> {
  const out = new Map<string, number>();
  for (const h of hits) {
    if (h.file !== file) continue;
    out.set(h.line, (out.get(h.line) ?? 0) + 1);
  }
  return out;
}

describe("the detector fires, and only on the shape it names", () => {
  const READ = "readFileSync";

  it("flags reading a source file and asserting on its text", () => {
    const violating = [
      'import { readFileSync } from "node:fs";',
      `const css = ${READ}(path.join(SRC, "index${CSS}"), "utf-8");`,
      'it("a rule", () => {',
      '  expect(css).toContain("--token");',
      "});",
    ].join("\n");

    const hits = scanFile("probe.test" + TS, violating);
    expect(hits.map((h) => h.rule)).toEqual(["FE-03"]);
    expect(hits[0].line).toContain(READ);
  });

  it("says nothing about the same read with no assertion anywhere", () => {
    // GROUND CONTROL. Without it, a detector that fired on every input would
    // satisfy the case above perfectly.
    const innocent = [
      'import { readFileSync } from "node:fs";',
      `const css = ${READ}(path.join(SRC, "index${CSS}"), "utf-8");`,
      // Not `parse(css)`: that is the parser escape hatch, and using it here
      // made this ground control pass for the wrong reason. Removing the
      // assertion gate entirely left it green, which is the one thing it
      // exists to notice.
      "export const tokens = tokenise(css);",
    ].join("\n");

    expect(scanFile("probe.test" + TS, innocent)).toEqual([]);
  });

  it("says nothing about a read of a document rather than source", () => {
    // The second half of the ground: a test may read the README or a
    // package manifest. Those are artefacts under test, not code under test.
    const docs = [
      'const readme = readFileSync(path.join(ROOT, "README.md"), "utf-8");',
      'it("x", () => { expect(readme).toContain("Elysium"); });',
    ].join("\n");

    expect(scanFile("probe.test" + TS, docs)).toEqual([]);
  });

  it("waves through text that is handed to a real parser", () => {
    const parsed = [
      `const text = ${READ}(path.join(SRC, "main${TSX}"), "utf-8");`,
      "const tree = parseAst(text);",
      'it("x", () => { expect(tree.body).toHaveLength(3); });',
    ].join("\n");

    expect(scanFile("probe.test" + TS, parsed)).toEqual([]);
  });

  it("flags a function handed back as its own source text", () => {
    // Assembled, like the extensions: typed whole, this sample would make
    // the file trip its own FE-01.
    const reflection = "Function" + ".prototype" + ".toString";
    const reflected = [
      `const body = ${reflection}.call(handler);`,
      'it("x", () => { expect(body).toContain("fetch("); });',
    ].join("\n");

    expect(scanFile("probe.test" + TS, reflected).map((h) => h.rule)).toEqual([
      "FE-01",
    ]);
  });

  it("says nothing when no reflection call is there", () => {
    // GROUND CONTROL for FE-01.
    const plain = [
      "const body = handler.name;",
      'it("x", () => { expect(body).toBe("handler"); });',
    ].join("\n");
    expect(scanFile("probe.test" + TS, plain)).toEqual([]);
  });

  it("flags a source file imported as text", () => {
    const rawQuery = "?" + "raw";
    const raw = [
      `import source from "../lib/store${TS}${rawQuery}";`,
      'it("x", () => { expect(source).toContain("partialize"); });',
    ].join("\n");

    expect(scanFile("probe.test" + TS, raw).map((h) => h.rule)).toEqual([
      "FE-02",
    ]);
  });

  it("says nothing about an ordinary import of the same module", () => {
    // GROUND CONTROL for FE-02: importing the module is the behavioural way
    // to test it, and must never be confused with importing its text.
    const ordinary = [
      `import { store } from "../lib/store${TS}";`,
      'it("x", () => { expect(store.getState()).toEqual({}); });',
    ].join("\n");
    expect(scanFile("probe.test" + TS, ordinary)).toEqual([]);
  });

  it("resolves a path held in a constant, not only one written inline", () => {
    // lib/contrast.test.ts and palette-guard.test.ts both read through a
    // module-level constant. A window-only rule reports them clean, which is
    // how a whole class of hit would have stayed invisible.
    const indirect = [
      `const CSS_PATH = path.resolve(__dirname, "..", "index${CSS}");`,
      "",
      "",
      "",
      "",
      "",
      `const css = ${READ}(CSS_PATH, "utf-8");`,
      'it("x", () => { expect(css).toContain("--token"); });',
    ].join("\n");

    expect(scanFile("probe.test" + TS, indirect).map((h) => h.rule)).toEqual([
      "FE-03",
    ]);
  });
});

describe("the shapes the rule was measured against and lost to", () => {
  // Every case below was a live escape at some point while this file was
  // being written. They are here so narrowing the rule back down goes red
  // instead of quietly erasing debt: a pinned violation the detector stops
  // seeing looks exactly like one somebody fixed.

  const violating = (read: string, tail = 'expect(x).toContain("y");') =>
    [`const x = ${read};`, tail].join("\n");

  it.each([
    ["readFileSync", `readFileSync(path.join(SRC, "index${CSS}"), "utf-8")`],
    ["readFile", `readFile(path.resolve(SRC, "main${TSX}"))`],
    ["readFileAsync", `await readFileAsync("../lib/store${TS}")`],
  ])("sees the %s spelling of a read", (_name, call) => {
    expect(scanFile("probe.test" + TS, violating(call)).length).toBe(1);
  });

  it.each([
    ["expect", 'expect(x).toContain("y");'],
    ["expect.soft", 'expect.soft(x).toContain("y");'],
    ["node assert", 'assert(x.includes("y"));'],
    ["invariant", 'invariant(x.includes("y"));'],
  ])("counts %s as an assertion", (_name, tail) => {
    const call = `readFileSync(path.join(SRC, "index${CSS}"), "utf-8")`;
    expect(scanFile("probe.test" + TS, violating(call, tail)).length).toBe(1);
  });

  it("reads a path out of a template literal", () => {
    const probe = [
      "const CSS_PATH = `${SRC}/index" + CSS + "`;",
      'const css = readFileSync(CSS_PATH, "utf-8");',
      'it("x", () => { expect(css).toContain("--token"); });',
    ].join("\n");
    expect(scanFile("probe.test" + TS, probe).length).toBe(1);
  });

  it("does not mistake a document read for a source read", () => {
    // GROUND for the case above. A markdown file is an artefact under test,
    // not code under test, and nothing near this read names source.
    const probe = [
      'if (name.endsWith(".md")) return;',
      'const notes = readFileSync(path.join(ROOT, "NOTES.md"), "utf-8");',
      'it("x", () => { expect(notes).toContain("hello"); });',
    ].join("\n");
    expect(scanFile("probe.test" + TS, probe)).toEqual([]);
  });

  it("still sees a directory listing filtered by a source extension", () => {
    // Deliberately broad: a source-extension filter beside a read counts even
    // when the read's own argument is a variable. settings-copy.test.ts is
    // exactly this shape, and the first, narrower cut of the rule lost it.
    const probe = [
      'const FILES = readdirSync(DIR).filter((f) => f.endsWith("' +
        TSX +
        '"));',
      'const body = readFileSync(path.join(DIR, FILES[0]), "utf-8");',
      'it("x", () => { expect(body).toContain("aria-label"); });',
    ].join("\n");
    expect(scanFile("probe.test" + TS, probe).length).toBe(1);
  });

  it("is not silenced by a comment mentioning a parser", () => {
    // A one-line dodge: writing `// nothing to do with postcss(` above a read
    // turned the whole rule off, because the escape hatch matched inside the
    // comment.
    const probe = [
      "// nothing to do with postcss( here",
      `const css = readFileSync(path.join(SRC, "index${CSS}"), "utf-8");`,
      'it("x", () => { expect(css).toContain("--token"); });',
    ].join("\n");
    expect(scanFile("probe.test" + TS, probe).length).toBe(1);
  });

  it("does not report a read that is only in a comment", () => {
    const probe = [
      `// const css = readFileSync(path.join(SRC, "index${CSS}"), "utf-8");`,
      'it("x", () => { expect(1).toBe(1); });',
    ].join("\n");
    expect(scanFile("probe.test" + TS, probe)).toEqual([]);
  });

  it("keeps a pattern that looks like a comment inside a regex literal", () => {
    // The stripper this file uses is character by character for exactly this
    // reason. A regex-based one blanked everything from a `/*` inside a
    // pattern to the next `*/` a hundred lines later, and three real hits
    // disappeared without a word.
    const probe = [
      "const strip = (s) => s.replace(/\\/\\*[\\s\\S]*?\\*\\//g, \"\");",
      `const css = readFileSync(path.join(SRC, "index${CSS}"), "utf-8");`,
      'it("x", () => { expect(strip(css)).toContain("--token"); });',
    ].join("\n");
    expect(scanFile("probe.test" + TS, probe).length).toBe(1);
  });

  it("sees a whole-tree scan that only names a glob", () => {
    // The biggest offender in this suite never writes a filename: it asks a
    // helper for every source file under a brace glob and reads what comes
    // back. Twenty of its thirty-three read calls were invisible until the
    // rule learned that spelling.
    const brace = "*" + "." + "{" + SOURCE_EXTS.join(",") + "}";
    const probe = [
      `const files = getSourceFiles("**/${brace}");`,
      "for (const file of files) {",
      "  const content = readFile(file);",
      '  expect(content).not.toContain("localStorage");',
      "}",
    ].join("\n");
    expect(scanFile("probe.test" + TS, probe).length).toBe(1);
  });

  it("sees the plain star form of the same glob", () => {
    const probe = [
      `const files = getSourceFiles("*${CSS}");`,
      "const content = readFile(files[0]);",
      'it("x", () => { expect(content).toContain("--token"); });',
    ].join("\n");
    expect(scanFile("probe.test" + TS, probe).length).toBe(1);
  });

  it("does not treat a bare extension as a glob", () => {
    // GROUND for the two above, and the false-positive engine that was
    // measured out in the first place: the extension on its own is not a
    // path and not a glob.
    const probe = [
      `const STYLE_SUFFIX = "${CSS}";`,
      'const notes = readFileSync(path.join(ROOT, "NOTES.md"), "utf-8");',
      'it("x", () => { expect(notes).toContain("hello"); });',
    ].join("\n");
    expect(scanFile("probe.test" + TS, probe)).toEqual([]);
  });

  it("sweeps helpers and mocks, not only files named *.test.ts", () => {
    // The obvious dodge is to move the read into a helper. The python side
    // sweeps every .py in its tests directory for exactly this reason, and
    // the first cut of this file only looked at *.test.ts - measured by
    // narrowing it back, which changed nothing anywhere and so proved
    // nothing either.
    const swept = testFiles().map(key);
    expect(
      swept.some((f) => !/\.test\.tsx?$/.test(f)),
      "the sweep is back to test files only, so a read moved into a helper " +
        "would never be seen again",
    ).toBe(true);
  });

  it("matches an identifier whose name starts with a dollar sign", () => {
    // `new RegExp("\\b$css\\b")` is a pattern that can never match anything,
    // because `$` is an anchor. The rule went quietly dead for every name
    // spelled that way.
    const probe = [
      `const $css = path.join(SRC, "index${CSS}");`,
      'const text = readFileSync($css, "utf-8");',
      'it("x", () => { expect(text).toContain("--token"); });',
    ].join("\n");
    expect(scanFile("probe.test" + TS, probe).length).toBe(1);
  });
});

describe("the sweep over the real tree", () => {
  it("actually looked at the suite", () => {
    // GROUND before any verdict. A broken glob returns nothing to report and
    // reads exactly like a clean tree.
    const { scanned } = sweep();
    expect(
      scanned,
      "the sweep found almost no test files, so a clean result means nothing",
    ).toBeGreaterThan(FILE_FLOOR);
  });

  it("holds the floor high enough for that to mean something", () => {
    // The assertion above is only worth what the floor is worth: set to zero
    // it passes on an empty sweep, which is the failure it exists to catch.
    expect(
      FILE_FLOOR,
      "a floor this low would pass on a tree with nothing in it",
    ).toBeGreaterThanOrEqual(100);
  });

  it("reaches every corner of the suite, not just the biggest one", () => {
    // A count on its own is a weak floor: 146 files today against a floor of
    // 100 means a whole directory can vanish in silence as long as the total
    // stays above it. lib/ is 23 files, and losing all of them would not move
    // the number enough to notice. So each corner is named.
    const { perDirectory } = sweep();
    for (const corner of [".", "components", "lib"]) {
      expect(
        perDirectory[corner] ?? 0,
        `the sweep saw nothing under src/test/${corner}`,
      ).toBeGreaterThan(5);
    }
  });

  it("finds every hit already pinned, and no other", () => {
    const { enforced } = sweep();
    const unpinned: string[] = [];
    for (const file of new Set(enforced.map((h) => h.file))) {
      const pinned = new Map(KNOWN_PENDING[file] ?? []);
      for (const [line, seen] of tally(enforced, file)) {
        const owed = pinned.get(line) ?? 0;
        if (seen > owed) {
          unpinned.push(`${file}: ${line} (${seen} hits, ${owed} written down)`);
        }
      }
    }
    expect(
      unpinned,
      "a test reads source text and asserts on it, and nothing said so",
    ).toEqual([]);
  });

  it("holds this file to the rule with no exemption at all", () => {
    const self = path.join(SRC, "test", SELF);
    expect(OWNED_FILES.has(SELF)).toBe(true);
    expect(scanFile(SELF, readFileSync(self, "utf8"))).toEqual([]);
  });

  it("makes owning a file mean something on its own", () => {
    // Measured against a synthetic pair rather than the real registries: with
    // ALLOWLIST empty, dropping the owned-files half of this decision changes
    // nothing in the tree and the promise reads as kept when it is not.
    const allowlist = { "owned.test.ts": "argued", "other.test.ts": "argued" };
    const owned = new Set(["owned.test.ts"]);
    expect(isEnforced("owned.test.ts", allowlist, owned)).toBe(true);
    // GROUND: an ordinary allowlisted file IS excused, so this is not a
    // function that just answers true to everything.
    expect(isEnforced("other.test.ts", allowlist, owned)).toBe(false);
    expect(isEnforced("unlisted.test.ts", allowlist, owned)).toBe(true);
  });

  it("never lets an owned file be excused by either registry", () => {
    // OWNED_FILES has to buy something. Written the obvious way, membership
    // bought nothing at all: the sweep hard-gated one hardcoded name and a
    // second entry would have been a decoration that read like a promise.
    const { hits, enforced } = sweep();
    for (const owned of OWNED_FILES) {
      expect(owned in ALLOWLIST, `${owned} is owned and allowlisted`).toBe(
        false,
      );
      expect(owned in KNOWN_PENDING, `${owned} is owned and pinned`).toBe(
        false,
      );
      const excused = hits.filter((h) => h.file === owned).length -
        enforced.filter((h) => h.file === owned).length;
      expect(excused, `${owned} had hits excused`).toBe(0);
    }
  });
});

describe("the registries stay honest", () => {
  it("pins nothing that has already been fixed", () => {
    // Self-cleaning, the same property verify_hygiene.py's waiver list has.
    // A pinned line that no longer exists, or occurs fewer times than the
    // count says, means the debt was paid and the entry is stale.
    const { enforced } = sweep();
    const stale: string[] = [];
    for (const [file, entries] of Object.entries(KNOWN_PENDING)) {
      const seen = tally(enforced, file);
      for (const [line, owed] of entries) {
        const now = seen.get(line) ?? 0;
        if (now < owed) {
          stale.push(`${file}: ${line} (${owed} written down, ${now} left)`);
        }
      }
    }
    expect(
      stale,
      "these pinned violations are gone, or the detector stopped seeing " +
        "them. Check which before dropping the entries: a rule that stopped " +
        "matching and a debt that was paid look identical from here.",
    ).toEqual([]);
  });

  it("allowlists nothing that is not there", () => {
    const present = new Set(testFiles().map(key));
    const dead = Object.keys(ALLOWLIST).filter((f) => !present.has(f));
    expect(dead, "allowlist entries naming files that do not exist").toEqual([]);
  });

  it("makes every exemption carry a written argument", () => {
    // An entry with "" for a reason was measured to hide a brand new
    // violation with nothing objecting. The argument is the whole price of
    // the exemption, so it is the thing that gets checked.
    const thin = Object.entries(ALLOWLIST)
      .filter(([, why]) => why.trim().length < MIN_JUSTIFICATION)
      .map(([file]) => file);
    expect(thin, "allowlist entries with no argument behind them").toEqual([]);
  });

  it("never lets one file sit in both the excused and the owed column", () => {
    // Moving a pinned file into ALLOWLIST used to report its six pinned
    // violations as PAID, which is the opposite of what happened to them.
    const both = Object.keys(KNOWN_PENDING).filter((f) => f in ALLOWLIST);
    expect(both, "a file cannot be both excused and written down as debt")
      .toEqual([]);
  });

  it("pins nothing that is not there", () => {
    const present = new Set(testFiles().map(key));
    const dead = Object.keys(KNOWN_PENDING).filter((f) => !present.has(f));
    expect(dead, "pinned files that do not exist").toEqual([]);
  });

  it("counts the debt in hits, not in lines", () => {
    // The number this ledger is worth. Twenty pinned lines stood for
    // twenty-five real violations until the counts went in.
    const owed = Object.values(KNOWN_PENDING)
      .flat()
      .reduce((sum, [, n]) => sum + n, 0);
    const { enforced } = sweep();
    expect(enforced.length, "the ledger and the sweep disagree").toBe(owed);
    expect(owed, "the recorded debt collapsed; something stopped scanning")
      .toBeGreaterThanOrEqual(25);
  });

  it("names every file the python side named for this rule", () => {
    // The queue that ordered this scanner counted seven frontend files
    // reading source text. The sweep finds those seven and four more, all in
    // components/ and all reading index.css to measure a token. Recorded
    // here so the difference is a measurement somebody can check rather than
    // a number nobody re-derived.
    const fromTheQueue = [
      "css-contract.test" + TS,
      "lib/contrast.test" + TS,
      "lib/motionTokens.test" + TS,
      "palette-guard.test" + TS,
      "right-panel-readability.test" + TS,
      "settings-copy.test" + TS,
      "static-safety.test" + TS,
    ];
    const pinned = new Set(Object.keys(KNOWN_PENDING));
    expect(fromTheQueue.filter((f) => !pinned.has(f))).toEqual([]);
  });
});
