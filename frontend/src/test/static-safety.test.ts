/**
 * Static safety tests - scan frontend source for privacy/security violations.
 * These tests read source files on disk and check for forbidden patterns.
 * They exclude themselves, node_modules, dist, and test fixture files.
 *
 * Almost every rule here passes by finding NOTHING. That is a weak shape: an
 * empty file list, a regex that stopped matching, and a genuinely clean repo
 * all look identical from the outside. Two devices exist to tell them apart,
 * and a new rule needs both:
 *
 *  - a FLOOR on any list built at runtime (`expect(files.length)
 *    .toBeGreaterThan(n)`), so a broken glob cannot iterate zero times;
 *  - a POSITIVE CONTROL feeding the SAME matcher object a known-bad string,
 *    so a matcher that went blind is caught. A separate copy of the pattern
 *    would drift; the control must reuse the one the scan just ran.
 *
 * A rule built on a plain `includes("literal")` needs no control - it cannot
 * silently stop matching. Controls exist on S-09b, S-11b, S-20b and S-23.
 */
import { describe, it, expect } from "vitest";
import { PERSISTED } from "@/lib/store/uiStore";
import { readFileSync, existsSync } from "fs";
import { globSync } from "glob";
import path from "path";
import catalogue from "../../../shared/error_catalogue.json";
// @ts-expect-error - plain JS, shared with eslint.config.js so the two gates
// answer "is this scaffolding" the same way. No types, and none wanted: a .d.ts
// beside it would be a second declaration of the same thing.
import { SUPPORT_FILES, isScaffolding } from "../../privacy-scope.js";
// S-09c reads the store's REAL defaults and the REAL bounds rather than
// retyping them. A shape table that only agrees with itself is a comment.
import {
  useUiStore,
  MSG_FONT_MIN,
  MSG_FONT_MAX,
  MSG_LINE_MIN,
  MSG_LINE_MAX,
  MSG_OPACITY_MIN,
  MSG_OPACITY_MAX,
} from "@/lib/store/uiStore";
import { CHAT_BG_ZOOM_MIN } from "@/lib/appearance/chatBackground";

const SRC_DIR = path.resolve(__dirname, "../../");
const THIS_FILE = path.resolve(__filename);

/** Get all source files matching a glob pattern, excluding this test and non-source dirs. */
function getSourceFiles(pattern: string): string[] {
  const files = globSync(pattern, {
    cwd: SRC_DIR,
    absolute: true,
    ignore: ["**/node_modules/**", "**/dist/**", "**/.vite/**"],
  });
  return files.filter((f) => f !== THIS_FILE);
}

function readFile(filePath: string): string {
  return readFileSync(filePath, "utf-8");
}

/** True for files that are part of the test suite (not shipped app source).
 *
 * Decided by name, and by the same list ESLint reads. It used to be decided by
 * whether a `test` folder appeared anywhere in the path, and so did ESLint's
 * exemption, which meant one misplaced file switched off both gates at once.
 * The mocks folder was skipped even harder - excluded from the glob, so those
 * files were not read at all - and that is gone too.
 */
function isTestFile(filePath: string): boolean {
  return isScaffolding(path.relative(SRC_DIR, filePath));
}

/** Source files with test files removed - used by app-source-only guards. */
function getAppSourceFiles(pattern: string): string[] {
  return getSourceFiles(pattern).filter((f) => !isTestFile(f));
}

/**
 * Remove block and line comments so token scans don't trip on prose inside
 * comments (e.g. a JSDoc "no Authorization header" note, or "never sends
 * image_url"). Deliberately conservative: it only ever removes text, so it can
 * never turn a clean file into a false positive. `://` inside URLs is preserved
 * by refusing to treat `//` as a line comment when a colon immediately precedes.
 */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

/**
 * The `partialize` object literal out of uiStore.ts, brace-balanced.
 *
 * Lifted to module scope because two rules need it (S-09b checks the SHAPE of
 * every entry, S-09c checks what each persisted key is allowed to HOLD) and a
 * second copy of a brace walker is a second place for it to be wrong.
 *
 * Brace-balances from `=> ({` to its matching `})`. A non-greedy regex stops
 * at the FIRST `})`, so a value containing one truncates the scanned region
 * and every later key escapes unread.
 */
/**
 * The persisted keys, from the store's own export.
 *
 * This used to parse `partialize`'s body out of `uiStore.ts` by balancing
 * braces, and then run two line regexes over the text. That was the best
 * available answer while the list existed only as thirty-two hand-written
 * `key: state.key` lines - and it could only ever check the NAMES, so
 * `msgFontPx: state.vaultKey` went straight through it.
 *
 * The list is one exported constant now, so the region does not need finding
 * and the names do not need parsing. What the two rules below still do -
 * check that every persisted key is described, and that no key is described
 * as holding something a person reads on screen - they now do against the
 * real list. What a source scan never could do, and still cannot, is say
 * what those keys HOLD at runtime; `whatIsWrittenToDisk.test.ts` reads the
 * written blob for that, and the two are complementary.
 */
function persistedKeys(): readonly string[] {
  return PERSISTED;
}

/* ────────────────────────────────────────────────────────────────────────
 * S-09c: what an allowlisted persisted key is allowed to HOLD.
 *
 * S-09b answers "may this key be persisted" and "does it mirror its own
 * field". Neither question can tell `msgFontPx: state.msgFontPx` (a number
 * about type size) from `chatTitle: state.chatTitle` (a name a person reads
 * on screen) once somebody adds the second one to the allowlist - the entry
 * is well formed, the name is not on the forbidden-substring list, and both
 * rules go green. localStorage is plaintext on disk, outside the vault, and
 * survives every purge browser_profile.py performs, so that key would put a
 * chat's real name where any process on the machine can read it.
 *
 * The vibe version of this rule is "do not allowlist anything content-like",
 * which is unenforceable. The testable version is: every allowlisted key
 * declares the SHAPE of what it may hold, and the whole table is then swept
 * with a list of names and sentences a person would actually see in this app.
 * A key whose declared shape accepts any of them fails. Adding `chatTitle`
 * means writing a guard that accepts a chat title, and that guard cannot
 * survive the sweep - so the rule bites at the moment of the mistake rather
 * than relying on a reviewer noticing.
 *
 * Three anti-cheats, because a declaration is only worth what checks it:
 *  - each guard must ACCEPT its own stated sample, so `accepts: () => false`
 *    cannot be used to slip past the name sweep;
 *  - each guard must ACCEPT the store's live default for that key, so the
 *    declared shape is measured against the running app, not against itself;
 *  - the table and `partialize` must name the SAME keys in both directions,
 *    so an entry cannot rot into a description of a field nobody persists.
 *
 * FORMER HONEST LIMIT, now moot. `selectedModelId` used to live in this
 * table and held a provider/model slug shaped like `a/b` - the same shape as
 * a nickname somebody might type ("he/him"), which no shape guard here could
 * tell apart from a real slug. v1.2 removed the whole question rather than
 * answering it: that key is a NAME a person reads on screen, so it moved out
 * of localStorage into the encrypted settings table (see uiStore.ts's
 * version-3 migrate) and is no longer in `partialize` or in this table at
 * all. The name sweep below uses names of the kind this app really renders
 * (chat titles, character names, message text).
 * ──────────────────────────────────────────────────────────────────────── */

interface PersistedShape {
  /** Why a device-readable copy of this is acceptable. */
  why: string;
  /** What this key may hold. Must reject every NAME_PROBE. */
  accepts: (value: unknown) => boolean;
  /** A legitimate value. Proves `accepts` is not simply always false. */
  sample: unknown;
}

const isBool = (v: unknown) => typeof v === "boolean";
const inRange =
  (min: number, max: number) =>
  (v: unknown): boolean =>
    typeof v === "number" && Number.isFinite(v) && v >= min && v <= max;
const oneOf =
  (...allowed: string[]) =>
  (v: unknown): boolean =>
    typeof v === "string" && allowed.includes(v);
/** A database row id, or nothing selected. Never a title. */
const rowId = (v: unknown) =>
  v === null || (typeof v === "number" && Number.isInteger(v) && v >= 0);
const HEX = /^#[0-9a-fA-F]{6}$/;

const PERSISTED_KEY_SHAPES: Record<string, PersistedShape> = {
  selectedCharacterId: {
    why: "A row id. The owner's rule allows a numeric id outside the vault; the NAME that id resolves to is fetched over 127.0.0.1 and never stored here.",
    accepts: rowId,
    sample: 7,
  },
  selectedChatId: {
    why: "A row id, same argument as selectedCharacterId. This is the exact key a `chatTitle` sibling would be added next to, which is why the sweep below exists.",
    accepts: rowId,
    sample: 42,
  },
  activeRightPanelTab: {
    why: "Which of four fixed panels was open.",
    accepts: oneOf("models", "secrets", "persona", "notebook"),
    sample: "models",
  },
  sidebarCollapsed: {
    why: "One boolean about window furniture.",
    accepts: isBool,
    sample: true,
  },
  rightPanelCollapsed: {
    why: "The other half of the same window furniture - focus mode closes the two side panels independently, so each carries its own boolean.",
    accepts: isBool,
    sample: true,
  },
  msgFontPx: {
    why: "Reader type size, bounded by the store's own constants.",
    accepts: inRange(MSG_FONT_MIN, MSG_FONT_MAX),
    sample: MSG_FONT_MIN,
  },
  msgLineHeight: {
    why: "Reader line height, bounded by the store's own constants.",
    accepts: inRange(MSG_LINE_MIN, MSG_LINE_MAX),
    sample: MSG_LINE_MIN,
  },
  msgContrast: {
    why: "One of three contrast presets.",
    accepts: oneOf("soft", "default", "high"),
    sample: "high",
  },
  narrationEnabled: {
    why: "Style asterisk spans. A display flag.",
    accepts: isBool,
    sample: false,
  },
  quoteTintEnabled: {
    why: "Tint quoted spans. A display flag.",
    accepts: isBool,
    sample: false,
  },
  continuousVoice: {
    why: "Speak replies aloud. Says nothing about WHAT was said.",
    accepts: isBool,
    sample: true,
  },
  voiceHintDismissed: {
    why: "A hint was closed. UI chrome.",
    accepts: isBool,
    sample: true,
  },
  narrationMigrated: {
    why: "The one-shot flag saying the old device-local narration mode was moved into the vault.",
    accepts: isBool,
    sample: true,
  },
  msgInk: {
    why: "A six-digit hex colour for message text, or nothing chosen.",
    accepts: (v) => v === null || (typeof v === "string" && HEX.test(v)),
    sample: "#c8d8ec",
  },
  surfaceFinish: {
    why: "One of three bubble finishes.",
    accepts: oneOf("matte", "glossy", "metallic"),
    sample: "glossy",
  },
  msgOpacity: {
    why: "How solid a bubble is, bounded by the store's own constants.",
    accepts: inRange(MSG_OPACITY_MIN, MSG_OPACITY_MAX),
    sample: MSG_OPACITY_MIN,
  },
  chatBgOn: {
    why: "Is the wallpaper shown. The picture itself is a Blob in the S-13 store; no filename or path is ever persisted.",
    accepts: isBool,
    sample: true,
  },
  chatBgLum: {
    why: "Average luminance of that picture, 0..1. One number about brightness.",
    accepts: inRange(0, 1),
    sample: 0.5,
  },
  chatBgContrast: {
    why: "Scrim strength, 0..0.85.",
    accepts: inRange(0, 0.85),
    sample: 0.35,
  },
  chatBgTint: {
    why: "The literal 'auto', or a six-digit hex tint.",
    accepts: (v) => v === "auto" || (typeof v === "string" && HEX.test(v)),
    sample: "auto",
  },
  chatBgFocusX: {
    why: "Which part of the picture to show, as a percentage.",
    accepts: inRange(0, 100),
    sample: 50,
  },
  chatBgFocusY: {
    why: "Which part of the picture to show, as a percentage.",
    accepts: inRange(0, 100),
    sample: 50,
  },
  chatBgZoom: {
    why: "How far the picture is cropped in.",
    accepts: inRange(CHAT_BG_ZOOM_MIN, 100),
    sample: CHAT_BG_ZOOM_MIN,
  },
  chatBgAspect: {
    why: "Width over height of that picture, or unknown. A ratio, not a size and not a name.",
    accepts: (v) => v === null || inRange(0.01, 100)(v),
    sample: 1.5,
  },
  ambientFogOn: {
    why: "A decorative effect flag.",
    accepts: isBool,
    sample: false,
  },
  genTemperature: {
    why: "Sampling scalar, 0..2.",
    accepts: inRange(0, 2),
    sample: 0.8,
  },
  genTopP: { why: "Sampling scalar, 0..1.", accepts: inRange(0, 1), sample: 0.9 },
  genTopK: { why: "Sampling scalar, 0..500.", accepts: inRange(0, 500), sample: 40 },
  genRepetitionPenalty: {
    why: "Sampling scalar, 0..2.",
    accepts: inRange(0, 2),
    sample: 1.05,
  },
  genMaxOutput: {
    why: "A token budget. A count, not a text.",
    accepts: inRange(1, 2_000_000),
    sample: 1024,
  },
  genSeed: {
    why: "The ONE free string here, and the reason it is narrow: the field is `type=\"number\"`, so it holds digits with an optional sign, or nothing. A guard of `typeof v === 'string'` would have let a title in through this key.",
    accepts: (v) => typeof v === "string" && /^-?\d*$/.test(v),
    sample: "12345",
  },
  genContextBudget: {
    why: "A token budget. A count, not a text.",
    accepts: inRange(512, 2_000_000),
    sample: 16384,
  },
};

/**
 * Things a person actually reads on screen in THIS app: chat titles, character
 * names, and message text. No allowlisted key may accept any of them.
 *
 * Deliberately not slug-shaped and not digit-only, per the HONEST LIMIT note:
 * this list is what the rule claims to catch, and claiming more than it
 * catches is the failure the file exists to prevent.
 */
const NAME_PROBES: string[] = [
  "Aria",
  "Ada Lovelace",
  "Dr. Vale",
  "Untitled chat",
  "My chat about the divorce",
  "hey, are you there?",
  "Chat 3 (draft)",
  "Мария",
  // Slug-shaped, and a real thing somebody types into a name field. This
  // probe could not be here while selectedModelId was persisted, because no
  // guard can tell "he/him" from "anthropic/claude-sonnet-4". That key left
  // localStorage in v1.2, so the shape is now free to be forbidden outright -
  // and forbidding it is what keeps the exception from being reintroduced
  // under a different name.
  "he/him",
  "The assistant said something I would not want read off my disk.",
];

/** Every (key, probe) pair the table would wave through. Empty is the pass. */
function keysAcceptingAProbe(table: Record<string, PersistedShape>): string[] {
  const bad: string[] = [];
  for (const [key, shape] of Object.entries(table)) {
    for (const probe of NAME_PROBES) {
      if (shape.accepts(probe)) bad.push(`${key} accepts ${JSON.stringify(probe)}`);
    }
  }
  return bad;
}

/* ────────────────────────────────────────────────────────────────────────
 * S-27 support: reading text-entry elements out of JSX.
 * ──────────────────────────────────────────────────────────────────────── */

/** One opening tag for a text-entry element, with its attribute text. */
interface FieldTag {
  tag: string;
  attrs: string;
  file: string;
  line: number;
}

/**
 * Every `<input>` / `<textarea>` opening tag in a source file.
 *
 * `Input` and `Textarea` are in the list because both wrappers end with
 * `{...props}` (src/components/ui/input.tsx, ui/textarea.tsx), so an
 * attribute written on the wrapper lands on the real DOM element. Scanning
 * only the lowercase tags would miss every dialog field in the app.
 *
 * Walks forward from the tag name to the `>` that closes the opening tag,
 * tracking `{}` depth and quotes so that `id={`a-${b}`}` is read as one
 * attribute rather than ending the tag at the first `>` inside an expression.
 * Comments are NOT stripped first: line numbers have to survive so a failure
 * names a place somebody can go to, and a commented-out `<input name="x">`
 * tripping this rule is the right outcome anyway.
 */
function findFieldTags(source: string, file: string): FieldTag[] {
  const out: FieldTag[] = [];
  const openRe = /<(input|textarea|Input|Textarea|InputPrimitive)(?=[\s/>])/g;
  for (const m of source.matchAll(openRe)) {
    const start = m.index + m[0].length;
    let depth = 0;
    let quote = "";
    let i = start;
    for (; i < source.length; i++) {
      const ch = source[i];
      if (quote) {
        if (ch === quote) quote = "";
        continue;
      }
      if (ch === '"' || ch === "'" || ch === "`") {
        quote = ch;
        continue;
      }
      if (ch === "{") depth++;
      else if (ch === "}") depth--;
      else if (ch === ">" && depth === 0) break;
    }
    out.push({
      tag: m[1],
      attrs: source.slice(start, i),
      file,
      line: source.slice(0, m.index).split("\n").length,
    });
  }
  return out;
}

/** `name=` written as an attribute of this tag (not `aria-...` or `data-...`). */
const NAME_ATTR = /(^|\s)name\s*=/;
/** `id=` written as an attribute of this tag. */
const ID_ATTR = /(^|\s)id\s*=/;
/** `autoComplete="off"`, in either the string or the brace form. */
const AUTOCOMPLETE_OFF =
  /(^|\s)autoComplete\s*=\s*(?:["']off["']|\{\s*["']off["']\s*\})/;
/** A literal `type="..."` on the tag, if there is one. */
const TYPE_ATTR = /(^|\s)type\s*=\s*["']([a-z]+)["']/;

/**
 * Input types that cannot reach Chromium's form-history table at all.
 *
 * `AutocompleteHistoryManager` stores a submitted field only when
 * `FormFieldData::IsTextInputElement()` is true, which covers text, search,
 * tel, url, email, number and password and nothing else. A range, a checkbox,
 * a colour swatch or a file picker has no typed text to remember.
 */
const NON_TEXT_INPUT_TYPES = new Set([
  "range",
  "checkbox",
  "radio",
  "file",
  "color",
  "button",
  "submit",
  "reset",
  "hidden",
  "image",
]);

describe("Static safety tests", () => {
  const allSrcFiles = getSourceFiles("**/*.{ts,tsx,css}");

  // S-01: No openrouter.ai URL
  it("S-01: no openrouter.ai in source", () => {
    for (const file of allSrcFiles) {
      const content = readFile(file);
      expect(
        content.includes("openrouter.ai"),
        `Found "openrouter.ai" in ${path.relative(SRC_DIR, file)}`,
      ).toBe(false);
    }
  });

  // S-02: No /api/v1/chat/completions
  it("S-02: no /api/v1/chat/completions in source", () => {
    for (const file of allSrcFiles) {
      const content = readFile(file);
      expect(
        content.includes("/api/v1/chat/completions"),
        `Found "/api/v1/chat/completions" in ${path.relative(SRC_DIR, file)}`,
      ).toBe(false);
    }
  });

  // S-03: No console.log anywhere; no other console.* in app source.
  //  - console.log stays banned in every file (tests included) - unchanged.
  //  - error/warn/debug/info are additionally banned in shipped app source, so
  //    nothing leaks user data to the devtools console. Tests may use them for
  //    diagnostics, so the extended ban is scoped to non-test source (comments
  //    stripped, so an explanatory "don't use console.error" note is ignored).
  it("S-03: no console.log in source (and no console.* in app source)", () => {
    for (const file of allSrcFiles) {
      const content = readFile(file);
      expect(
        content.includes("console.log"),
        `Found "console.log" in ${path.relative(SRC_DIR, file)}`,
      ).toBe(false);
    }

    const appFiles = getAppSourceFiles("**/*.{ts,tsx}");
    expect(
      appFiles.length,
      "app-source glob returned too few files (guard against a broken scan)",
    ).toBeGreaterThan(10);
    const bannedMethods = [
      "console.error",
      "console.warn",
      "console.debug",
      "console.info",
    ];
    for (const file of appFiles) {
      const content = stripComments(readFile(file));
      for (const method of bannedMethods) {
        expect(
          content.includes(method),
          `Found "${method}" in ${path.relative(SRC_DIR, file)}`,
        ).toBe(false);
      }
    }
  });

  // S-04: No sessionStorage.setItem
  it("S-04: no sessionStorage.setItem in source", () => {
    for (const file of allSrcFiles) {
      const content = readFile(file);
      expect(
        content.includes("sessionStorage.setItem"),
        `Found "sessionStorage.setItem" in ${path.relative(SRC_DIR, file)}`,
      ).toBe(false);
    }
  });

  // S-05: No remote CSS url(http in stylesheets
  it("S-05: no url(http in CSS source", () => {
    const cssFiles = getSourceFiles("**/*.css");
    // Floor. This rule builds its OWN glob, so S-12's count does not cover it:
    // if this pattern ever stopped matching, the rule would sweep an empty
    // list and pass green while guarding nothing.
    expect(cssFiles.length, "S-05 found no stylesheet to scan").toBeGreaterThan(0);
    for (const file of cssFiles) {
      const content = readFile(file);
      expect(
        content.includes("url(http"),
        `Found "url(http" in ${path.relative(SRC_DIR, file)}`,
      ).toBe(false);
    }
  });

  // S-06: No @import url(http
  // KEPT in KADEME 20b, against section 4's list. Section 4 said S-05 already
  // catches this shape and stylelint's function-url-scheme-allowed-list makes
  // it structurally redundant. Measured, both halves are wrong today: S-05
  // globs `**/*.css` only, so this literal inside a .ts or .tsx template
  // string is outside its file set; and stylelint appears nowhere in this
  // repo. This is currently the only guard for the non-CSS case.
  it("S-06: no @import url(http in source", () => {
    for (const file of allSrcFiles) {
      const content = readFile(file);
      expect(
        content.includes("@import url(http"),
        `Found "@import url(http" in ${path.relative(SRC_DIR, file)}`,
      ).toBe(false);
    }
  });

  // S-07 removed: see S-23, which replaces it. @fontsource self-hosts font
  // files into the bundle and issues no runtime request, so banning it forbade
  // the privacy-friendlier option while leaving the actual hole (a remote
  // reference written by hand into index.html) wide open.

  // S-08: /complete only in approved completion API files
  it("S-08: /complete only in approved completion API file", () => {
    const approvedFiles = new Set([
      path.resolve(SRC_DIR, "src", "lib", "api", "completions.ts"),
      // SSE streaming endpoints (…/complete/stream, …/regenerate/stream) -
      // the streaming API module is an approved completion call-site.
      path.resolve(SRC_DIR, "src", "lib", "api", "stream.ts"),
    ]);
    const scanFiles = getSourceFiles(
      "src/{lib/api,components,lib/query}/**/*.{ts,tsx}",
    );
    // Floor - own glob, not covered by S-12. 103 files today; the bar is set
    // low enough to survive a directory being reorganised and high enough
    // that a broken pattern cannot slip past.
    expect(scanFiles.length, "S-08 found nothing to scan").toBeGreaterThan(50);
    for (const file of scanFiles) {
      if (approvedFiles.has(file)) continue;
      const content = readFile(file);
      expect(
        content.includes("/complete"),
        `Found "/complete" in unapproved file: ${path.relative(SRC_DIR, file)}`,
      ).toBe(false);
    }
  });

  // S-09: localStorage.setItem only in store files
  it("S-09: no localStorage.setItem outside lib/store", () => {
    // Scaffolding is filtered by name now. It used to be filtered by folder,
    // and further up, by leaving `src/test/mocks/**` out of the glob entirely
    // - so legacyStorage.ts, which seeds the legacy blob the narration
    // migration reads, was never even opened. It is scaffolding either way;
    // the difference is that saying so out loud is what an ordinary file
    // dropped in that folder no longer gets for free.
    const nonStoreFiles = allSrcFiles.filter(
      (f) => !f.includes(path.join("lib", "store")) && !isTestFile(f),
    );
    // Floor. S-12 bounds `allSrcFiles`, but this rule scans what is LEFT
    // after a filter, and a filter that matched everything would leave
    // nothing to scan while the source glob still looked healthy.
    expect(
      nonStoreFiles.length,
      "S-09 filtered away every file it was meant to scan",
    ).toBeGreaterThan(50);
    for (const file of nonStoreFiles) {
      const content = readFile(file);
      expect(
        content.includes("localStorage.setItem"),
        `Found "localStorage.setItem" in ${path.relative(SRC_DIR, file)}`,
      ).toBe(false);
    }
  });

  // S-09b: The persisted UI state (Zustand `persist` + `partialize`) may only
  // carry an allowlisted set of harmless UI-preference keys. The app must never
  // persist drafts, message content, personas, attachments, or secrets to
  // localStorage - literal `localStorage.setItem` scans (S-09) miss this path
  // entirely because the store persists via partialize, not a direct call.
  it("S-09b: uiStore partialize persists only allowlisted keys", () => {
    // The brace-balanced parse moved to readPartializeBody() at module scope
    // when S-09c was added, so both rules read the same region by the same
    // walk. The reason it is a walk and not a regex is unchanged: a non-greedy
    // pattern stops at the FIRST `})`, so a value containing one truncates the
    // scanned region and every later key escapes.
    const keys = persistedKeys();

    // The allowlist is now the key set of PERSISTED_KEY_SHAPES (module scope),
    // where each key also declares WHAT it may hold. Deriving it here rather
    // than keeping a second list means a key cannot be allowlisted in one
    // place and left undescribed in the other. S-09c enforces the description.
    const ALLOWED_PERSISTED_KEYS = new Set(Object.keys(PERSISTED_KEY_SHAPES));
    // The names this used to be spelled as, kept only as a floor: if the table
    // above were emptied or renamed away, every later assertion would still be
    // structurally valid and would guard nothing.
    expect(
      ALLOWED_PERSISTED_KEYS.size,
      "the persisted-key allowlist emptied itself",
    ).toBeGreaterThan(25);

    // PROVENANCE IS NOW STRUCTURAL, and that is a stronger statement than
    // this rule could ever make by reading text.
    //
    // The old version parsed `key: state.key` pairs out of partialize and
    // insisted the two names matched, because the gap it closed was
    // `genSeed: state.vaultKey` - an allowlisted key aliasing a secret.
    // partialize is now `PERSISTED.map((key) => [key, state[key]])`: the
    // value reads the field of its own name by construction, and there is no
    // second name to disagree with the first. Reintroducing the old failure
    // would mean rewriting that one line, which the behavioural test reading
    // the written blob would catch.
    //
    // What is left for a source-level rule is the part that IS about names:
    // every persisted key must be described in the table, and nothing may be
    // described that is not persisted.
    expect(
      keys.length,
      "the persisted list emptied itself (broken scan guard)",
    ).toBeGreaterThan(25);

    for (const key of keys) {
      expect(
        ALLOWED_PERSISTED_KEYS.has(key),
        `Non-allowlisted key "${key}" persisted by uiStore`,
      ).toBe(true);
    }

    // No persisted key may be NAMED for drafts, message content, personas,
    // attachments or secrets. A name sweep cannot see what a key holds - the
    // blob test does that - but it can see somebody adding `composerDraft`
    // to the list.
    const forbiddenSubstrings = [
      "draft",
      "message",
      "attachment",
      "persona",
      "apikey",
      "api_key",
      "secret",
      "token",
      "password",
    ];
    for (const key of keys) {
      const lower = key.toLowerCase();
      for (const needle of forbiddenSubstrings) {
        expect(
          lower.includes(needle),
          `Forbidden field "${needle}" persisted as "${key}"`,
        ).toBe(false);
      }
    }

    // POSITIVE CONTROL. Both checks above pass by finding nothing wrong with
    // today's list. That is also what they would do if the allowlist or the
    // name sweep had quietly stopped discriminating, so each is handed a key
    // it MUST reject - using the same constants the real scan just used.
    const rejects = (key: string) => {
      const lower = key.toLowerCase();
      return (
        !ALLOWED_PERSISTED_KEYS.has(key) ||
        forbiddenSubstrings.some((needle) => lower.includes(needle))
      );
    };
    expect(rejects("composerDraft"), "a draft key was not rejected").toBe(true);
    expect(rejects("apiKey"), "a secret key was not rejected").toBe(true);
    expect(rejects("somethingNobodyDescribed"),
      "an undescribed key was not rejected").toBe(true);
    // And the other direction: a real key is NOT rejected, so the guard is
    // discriminating rather than refusing everything.
    expect(rejects(keys[0]), "a legitimate key was rejected").toBe(false);
  });

  // S-10 was deleted in KADEME 19a. It banned "openrouter.ai" from
  // src/lib/api/completions.ts alone; S-01 above bans the same string from
  // every .ts/.tsx/.css file under src, and completions.ts is in that list
  // (measured, not assumed). A weaker copy of an existing gate only dilutes
  // the count of what is actually guarded.

  // S-11: No Authorization header in any source file
  // KEPT in KADEME 20b, against section 4's list. The listed condition was
  // explicit: delete only after the PC-02 prefix collision is fixed. That
  // collision IS fixed now: the privacy-contract registry anchors a frontend
  // name on both sides, `("S-11` followed by a quote or a colon, so `"S-11`
  // no longer resolves through `"S-11b` and deleting this rule would show up
  // as a missing proof. The condition is met; the rule stays anyway, for the
  // second reason below.
  //
  // The premise is wrong too. S-11b is broader in PATTERN but narrower in
  // SCOPE: it scans app source only, while this scans every source file
  // including tests. An Authorization literal in a test file trips this and
  // not S-11b, so S-11b does not subsume it.
  it("S-11: no Authorization header in source", () => {
    for (const file of allSrcFiles) {
      if (file.endsWith(".css")) continue;
      const content = readFile(file);
      expect(
        content.includes('"Authorization"') ||
          content.includes("'Authorization'"),
        `Found Authorization header in ${path.relative(SRC_DIR, file)}`,
      ).toBe(false);
    }
  });

  // S-11b: Broaden the Authorization scan beyond the quoted form. Catches an
  // unquoted object key (`Authorization:`), the lowercase header name, and a
  // `Bearer ` token literal - the exact shapes a hand-rolled auth header takes.
  // Comments are stripped (so the "no Authorization header" doc note in
  // stream.ts is ignored) and test files are excluded. This MUST fail on
  // `headers: { Authorization: \`Bearer ${key}\` }`.
  it("S-11b: no Authorization/Bearer token construction in app source", () => {
    const appFiles = getAppSourceFiles("**/*.{ts,tsx}");
    expect(
      appFiles.length,
      "app-source glob returned too few files (guard against a broken scan)",
    ).toBeGreaterThan(10);
    const patterns: RegExp[] = [
      /\bauthorization\b/i, // the header name (quoted or unquoted, any case)
      /bearer\s/i, // a "Bearer " token literal
    ];
    for (const file of appFiles) {
      const content = stripComments(readFile(file));
      for (const pattern of patterns) {
        expect(
          pattern.test(content),
          `Found Authorization/Bearer pattern ${pattern} in ${path.relative(SRC_DIR, file)}`,
        ).toBe(false);
      }
    }

    // POSITIVE CONTROL - same two RegExp objects the scan just used.
    for (const bad of [
      'const headers = { Authorization: "..." }',
      "req.headers.authorization = k;",
      "`Bearer ${apiKey}`",
    ]) {
      expect(
        patterns.some((p) => p.test(bad)),
        `S-11b went blind to: ${bad}`,
      ).toBe(true);
    }
    // Narrow enough not to fire on ordinary prose.
    expect(patterns.some((p) => p.test("const author = meta.author;"))).toBe(false);
  });

  // S-12: Source file count guard (prevents vacuous pass if glob breaks)
  // KEPT in KADEME 20b, against section 4's list, and it is the most
  // load-bearing refusal of the set. Section 4 called it an apparatus
  // tripwire, on condition that a vacuity check land on the lint side first
  // (an ESLint run over an empty glob exits 0). No such check exists.
  //
  // Counted: TWELVE rules in this file iterate `allSrcFiles` with no floor
  // of their own - S-01, S-02, S-03, S-04, S-06, S-13 through S-19. If the
  // shared glob broke and this line were gone, every one of them would
  // sweep an empty list and report a clean repo. The file's own header
  // states the doctrine; this is the line that implements it.
  it("S-12: source file count above safe threshold", () => {
    expect(allSrcFiles.length).toBeGreaterThan(10);
  });

  // S-13: No indexedDB usage outside the approved appearance-blob store.
  // Deliberate exception (chat background feature): the user-chosen wallpaper
  // image is stored as a Blob in a dedicated object store - it is decorative
  // user preference data, never conversation content, drafts, or secrets.
  // Keeping it OUT of localStorage avoids the data-URI size/serialization
  // trap, and the Blob pipeline never touches base64/data: URIs (S-21).
  it("S-13: no indexedDB in source outside the approved blob store", () => {
    const approvedFiles = new Set([
      path.resolve(SRC_DIR, "src", "lib", "store", "chatBgDb.ts"),
    ]);
    for (const file of allSrcFiles) {
      if (file.endsWith(".css")) continue;
      if (approvedFiles.has(file)) continue;
      const content = readFile(file);
      expect(
        content.includes("indexedDB"),
        `Found "indexedDB" in ${path.relative(SRC_DIR, file)}`,
      ).toBe(false);
    }
  });

  // S-14: No document.cookie usage
  it("S-14: no document.cookie in source", () => {
    for (const file of allSrcFiles) {
      if (file.endsWith(".css")) continue;
      const content = readFile(file);
      expect(
        content.includes("document.cookie"),
        `Found "document.cookie" in ${path.relative(SRC_DIR, file)}`,
      ).toBe(false);
    }
  });

  // S-15: No navigator.sendBeacon usage
  it("S-15: no navigator.sendBeacon in source", () => {
    for (const file of allSrcFiles) {
      if (file.endsWith(".css")) continue;
      const content = readFile(file);
      expect(
        content.includes("navigator.sendBeacon"),
        `Found "navigator.sendBeacon" in ${path.relative(SRC_DIR, file)}`,
      ).toBe(false);
    }
  });

  // S-16: No serviceWorker.register usage
  it("S-16: no serviceWorker.register in source", () => {
    for (const file of allSrcFiles) {
      if (file.endsWith(".css")) continue;
      const content = readFile(file);
      expect(
        content.includes("serviceWorker.register"),
        `Found "serviceWorker.register" in ${path.relative(SRC_DIR, file)}`,
      ).toBe(false);
    }
  });

  // S-17: No caches.open usage
  it("S-17: no caches.open in source", () => {
    for (const file of allSrcFiles) {
      if (file.endsWith(".css")) continue;
      const content = readFile(file);
      expect(
        content.includes("caches.open"),
        `Found "caches.open" in ${path.relative(SRC_DIR, file)}`,
      ).toBe(false);
    }
  });

  // S-18: No dangerouslySetInnerHTML usage
  it("S-18: no dangerouslySetInnerHTML in source", () => {
    for (const file of allSrcFiles) {
      if (file.endsWith(".css")) continue;
      const content = readFile(file);
      expect(
        content.includes("dangerouslySetInnerHTML"),
        `Found "dangerouslySetInnerHTML" in ${path.relative(SRC_DIR, file)}`,
      ).toBe(false);
    }
  });

  // S-19: No VITE_OPENROUTER / VITE_API_KEY / OPENROUTER_API_KEY references
  it("S-19: no secret env var references in source", () => {
    const forbidden = ["VITE_OPENROUTER", "VITE_API_KEY", "OPENROUTER_API_KEY"];
    for (const file of allSrcFiles) {
      if (file.endsWith(".css")) continue;
      const content = readFile(file);
      for (const pattern of forbidden) {
        expect(
          content.includes(pattern),
          `Found "${pattern}" in ${path.relative(SRC_DIR, file)}`,
        ).toBe(false);
      }
    }
  });

  // S-20: No frontend provider privacy fields in API/query request code
  it("S-20: no provider privacy fields in frontend request code", () => {
    const forbidden = ["zdr", "data_collection", "allow_fallbacks"];
    const requestFiles = getSourceFiles("src/lib/{api,query}/**/*.ts");
    // Floor - own glob, not covered by S-12. This is the privacy contract:
    // an empty list here means the provider-privacy fields are unguarded and
    // nothing says so. 23 files today.
    expect(requestFiles.length, "S-20 found nothing to scan").toBeGreaterThan(10);

    for (const file of requestFiles) {
      const content = readFile(file);
      for (const pattern of forbidden) {
        expect(
          content.includes(pattern),
          `Found "${pattern}" in ${path.relative(SRC_DIR, file)}`,
        ).toBe(false);
      }
    }
  });

  // S-20b: lib/generation builds the completion/regenerate payloads, so it must
  // never *inject* provider-privacy fields. A plain substring scan is wrong
  // here - generationParams.ts legitimately lists these names in a DENYLIST it
  // uses to strip them. So we scan (comments removed) only for the injection
  // shapes: an object key (`field:`) or a property write/read (`.field`). The
  // quoted denylist members (`"zdr",`) and doc comments are intentionally not
  // matched. This MUST fail on `payload.provider = {…}` or `{ zdr: true }`.
  it("S-20b: no provider-field injection in lib/generation", () => {
    const genFiles = getSourceFiles("src/lib/generation/**/*.ts");
    expect(
      genFiles.length,
      "lib/generation glob returned no files (guard against a broken scan)",
    ).toBeGreaterThan(0);
    const fields = ["provider", "zdr", "data_collection", "allow_fallbacks"];

    // The list is spelled out here on purpose - deriving it from production
    // would make this rule shrink silently whenever production's own list
    // shrank. But a HAND-KEPT copy drifts the other way: a fifth forbidden
    // field would go unscanned and nothing would say so. So pin the list AND
    // make the two agree. Measured in KADEME 19a: 4 against 4 today.
    const denylist = readFile(
      path.resolve(SRC_DIR, "src", "lib", "generation", "generationParams.ts"),
    );
    const declared = denylist.slice(
      denylist.indexOf("FORBIDDEN_FIELDS = new Set(["),
      denylist.indexOf("]", denylist.indexOf("FORBIDDEN_FIELDS = new Set([")),
    );
    expect(
      declared,
      "FORBIDDEN_FIELDS is gone or renamed - this rule is scanning a stale list",
    ).toContain("provider");
    expect(
      [...declared.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]).sort(),
      "generationParams forbids a field S-20b does not scan for (or the reverse)",
    ).toEqual([...fields].sort());
    // One builder, used by both the scan and its control, so a change to the
    // pattern cannot pass the control while breaking the scan.
    const mentions = (field: string, text: string) =>
      new RegExp("(?<![\\w\"'`])" + field + "\\s*:").test(text) ||
      new RegExp("\\." + field + "\\b").test(text);

    for (const file of genFiles) {
      const content = stripComments(readFile(file));
      for (const field of fields) {
        expect(
          mentions(field, content),
          `Provider field "${field}" injected in ${path.relative(SRC_DIR, file)}`,
        ).toBe(false);
      }
    }

    // POSITIVE CONTROL. Another absence rule: it is green today because
    // lib/generation says nothing about routing, which is indistinguishable
    // from a pattern that stopped matching. Every field gets a known-bad
    // payload in both shapes the rule claims to cover.
    for (const field of fields) {
      expect(mentions(field, `body = { ${field}: true };`),
        `S-20b went blind to a literal key: ${field}`).toBe(true);
      expect(mentions(field, `payload.${field} = order;`),
        `S-20b went blind to a member write: ${field}`).toBe(true);
      // The lookbehind exists so a longer identifier does not read as the
      // field itself; prove it still discriminates.
      expect(mentions(field, `const my_${field}: number = 1;`)).toBe(false);
    }
  });

  // S-21: The frontend never constructs multimodal image payloads - the backend
  // builds `image_url` content parts and any `data:` image URLs. The client only
  // sends a File via FormData (there is zero base64/FileReader/toDataURL usage).
  // Comments (e.g. "never sends image_url") are stripped; tests are excluded.
  // This MUST fail if a component builds an image_url part or a data: image URL.
  it("S-21: no image_url/data:image payload construction in app source", () => {
    const appFiles = getAppSourceFiles("**/*.{ts,tsx}");
    expect(
      appFiles.length,
      "app-source glob returned too few files (guard against a broken scan)",
    ).toBeGreaterThan(10);
    const forbidden = ["image_url", "data:image", ";base64"];
    for (const file of appFiles) {
      const content = stripComments(readFile(file));
      for (const needle of forbidden) {
        expect(
          content.includes(needle),
          `Found "${needle}" in ${path.relative(SRC_DIR, file)}`,
        ).toBe(false);
      }
    }
  });

  // S-22: The Zod jitless flag has to be set before ANY schema is built.
  // `allowsEval` is a lazily cached getter and our schema modules read it while
  // creating schemas at module scope, so importing the side-effect module second
  // is the same as not importing it: the `new Function("")` probe has already
  // fired and Chromium has already logged an enforced CSP violation. Ordering is
  // the whole behaviour here, and ordering is not observable from a unit test -
  // so it is pinned statically.
  it("S-22: zodJitless is the first import in main.tsx", () => {
    const main = readFile(path.resolve(SRC_DIR, "src", "main.tsx"));
    const firstImport = stripComments(main)
      .split("\n")
      .map((l) => l.trim())
      .find((l) => l.startsWith("import "));
    expect(firstImport, "main.tsx has no imports at all?").toBeTruthy();
    expect(firstImport).toContain("zodJitless");
  });

  // S-23: no remote resource references in HTML. Replaces the deleted S-07.
  //
  // Every S-01..S-22 scan globs ts/tsx/css, so index.html - the one file the
  // browser loads first - was read by nothing at all. The runtime backstop is
  // the CSP in backend/main.py (`default-src 'self'`), which blocks a remote
  // stylesheet or font fetch outright, and backend/tests/test_security_headers.py
  // proves that header ships on every response.
  //
  // But CSP has no directive for `<link rel="preconnect">`, because preconnect
  // transfers no content: it performs DNS, TCP and TLS against the remote host
  // and stops. Nothing is fetched, nothing is blocked, and the host learns the
  // user's IP anyway. That is precisely the leak the single-egress promise
  // forbids, and it is the one shape CSP cannot catch - so it is caught here.
  it("S-23: no remote href/src in HTML", () => {
    const htmlFiles = getSourceFiles("**/*.html");
    expect(htmlFiles.length, "no HTML files found - did the glob break?")
      .toBeGreaterThan(0);
    // Protocol-relative `//host` counts: it inherits the page scheme and
    // still leaves the machine.
    const remoteRe =
      /\b(?:href|src|imagesrcset|srcset)\s*=\s*["'](?:https?:)?\/\//gi;
    for (const file of htmlFiles) {
      const remote = readFile(file).match(remoteRe);
      expect(
        remote,
        `Remote reference in ${path.relative(SRC_DIR, file)}: ${remote?.join(", ")}`,
      ).toBeNull();
    }

    // POSITIVE CONTROL. This rule passes by finding NOTHING, so a matcher that
    // matched nothing would look exactly like a clean repo. The floor above
    // catches an empty file list; only this catches a blind pattern. It feeds
    // the SAME regex object the scan used, so the two cannot drift apart.
    for (const bad of [
      '<script src="https://cdn.example.com/x.js"></script>',
      '<link href="//fonts.example.com/f.css">',
      "<img SRC = 'http://tracker.example.com/p.gif'>",
    ]) {
      expect(bad.match(remoteRe), `S-23 went blind to: ${bad}`).not.toBeNull();
    }
    // ...and is still narrow enough to leave local references alone.
    expect('<a href="/settings"><img src="./logo.svg">'.match(remoteRe)).toBeNull();
  });

  /**
   * S-24 (K-36): every sentence a reader sees comes from the catalogue.
   *
   * The direction nothing checked. Two gates already guard the error codes and
   * both were green while five codes went straight past them:
   *
   *   catalogue <-> errorMessages.ts   frontend, both directions
   *   backend code -> catalogue        backend, by AST walk
   *
   * Drawn out, that is a LINE, not a triangle. Nothing looked at the frontend's
   * own source, so `pushErrorDirect("some_code", "a sentence typed right here")`
   * satisfied every existing assertion by never being seen at all. Five of the
   * ten production calls did exactly that, and none of the five appeared in any
   * test in either suite.
   *
   * `pushError` is not in scope: it goes through parseApiError, which ends at
   * getErrorMessage on all four of its exits. The hole is only in the direct
   * call.
   */
  it("S-24: pushErrorDirect never invents a code or a sentence", () => {
    const files = getAppSourceFiles("src/**/*.{ts,tsx}");
    expect(files.length, "S-24 scanned no files").toBeGreaterThan(50);

    const catalogued = new Set(
      (catalogue.codes as { code: string }[]).map((r) => r.code),
    );
    expect(catalogued.size, "the catalogue is empty").toBeGreaterThan(100);

    // Multi-line on purpose. Both of the calls that hid here longest wrap their
    // arguments onto separate lines, so a single-line pattern would have been
    // written, passed, and measured nothing at all.
    const callRe =
      /pushErrorDirect\(\s*("([a-z0-9_]+)"|[A-Za-z_$][\w$.?]*)\s*,\s*([\s\S]*?)(?:,\s*"(?:warning|error)"\s*)?\)/g;

    /** Second argument shapes that keep the sentence in one place. */
    const fromCatalogue = /^(getErrorMessage|getCountMessage)\s*\(/;

    /**
     * The one call allowed to pass a sentence we did not write, and why.
     *
     * voice_notice carries the worker's own diagnostic text - seventeen fixed
     * strings under tts/worker plus one interpolated exception. Reducing it to
     * a generic line would undo the reason the carrier was added: "every load
     * will be slow" is the whole message, and a machine without MSVC spoke two
     * to three times slower forever while nothing said so.
     *
     * Compared by EQUALITY below, not membership. An exemption list that only
     * ever grows is the failure this file exists to prevent, so a second entry
     * has to be argued for in a diff.
     */
    const ALLOWED_FOREIGN_SENTENCE = new Set(["tts_notice"]);

    const inventedCode: string[] = [];
    const inventedSentence: string[] = [];
    const foreignSeen = new Set<string>();
    let sites = 0;

    for (const file of files) {
      const rel = path.relative(SRC_DIR, file);
      for (const m of readFile(file).matchAll(callRe)) {
        sites += 1;
        const literalCode = m[2];
        const second = (m[3] ?? "").trim();

        // A code passed as a variable (`event.code`, `code`) is checked where
        // it is produced, not here; only a literal can be invented in place.
        if (literalCode && !catalogued.has(literalCode)) {
          inventedCode.push(`${rel}: ${literalCode}`);
        }
        if (fromCatalogue.test(second)) continue;
        if (literalCode && ALLOWED_FOREIGN_SENTENCE.has(literalCode)) {
          foreignSeen.add(literalCode);
          continue;
        }
        inventedSentence.push(`${rel}: ${literalCode ?? m[1]} <- ${second.slice(0, 60)}`);
      }
    }

    expect(sites, "S-24 matched no pushErrorDirect calls").toBeGreaterThan(8);
    expect(inventedCode, "codes with no catalogue record").toEqual([]);
    expect(inventedSentence, "sentences typed at the call site").toEqual([]);
    expect(
      [...foreignSeen].sort(),
      "the foreign-sentence exemption no longer matches what is in the code",
    ).toEqual([...ALLOWED_FOREIGN_SENTENCE].sort());

    // POSITIVE CONTROL, on the SAME regex object the scan just used, and
    // deliberately WRAPPED - the single-line version of this control is what
    // would have let the real multi-line offenders through.
    const synthetic = [
      'useErrorStore.getState().pushErrorDirect(',
      '  "banana_code",',
      '  "A sentence typed right here.",',
      '  "warning",',
      ');',
    ].join("\n");
    const probes = [...synthetic.matchAll(callRe)];
    expect(probes.length, "S-24 went blind to a wrapped call").toBe(1);
    expect(probes[0][2]).toBe("banana_code");
    expect(catalogued.has("banana_code")).toBe(false);
    expect(fromCatalogue.test((probes[0][3] ?? "").trim())).toBe(false);

    // ...and discriminating: the shape we WANT must not be reported. Both
    // helpers, because a control that only knew one of them would turn the
    // other into a violation the day it was used.
    for (const good of [
      'pushErrorDirect("attachment_gate_closed", getErrorMessage("attachment_gate_closed"), "warning")',
      'pushErrorDirect(\n  "tts_lines_dropped",\n  getCountMessage("tts_lines_dropped", n),\n  "warning",\n)',
      "pushErrorDirect(event.code, getErrorMessage(event.code))",
    ]) {
      const [hit] = [...good.matchAll(callRe)];
      expect(hit, `S-24 stopped seeing: ${good.slice(0, 40)}`).toBeDefined();
      expect(
        fromCatalogue.test((hit[3] ?? "").trim()),
        `S-24 would reject a legitimate call: ${good.slice(0, 40)}`,
      ).toBe(true);
    }
  });

  /**
   * S-25 (K-35): the exemption list is itself checked.
   *
   * Both privacy gates ask privacy-scope.js whether a file is scaffolding, so
   * that list is now the single thing standing between a file and every rule.
   * A list like that goes wrong in two directions and both are checked here:
   * it names a file that no longer exists, and it stops matching the files it
   * was written for.
   */
  it("S-25: every exempted path names a file that is really there", () => {
    expect(SUPPORT_FILES.length, "the exemption list emptied itself").toBe(8);
    for (const rel of SUPPORT_FILES as string[]) {
      expect(
        existsSync(path.resolve(SRC_DIR, rel)),
        `exempted but gone: ${rel}. A stale entry excuses nothing and hides ` +
          `that the real file lost its exemption.`,
      ).toBe(true);
    }
  });

  it("S-25: living in the test folder is not what earns the exemption", () => {
    // The defect, stated as a test. An ordinary name in the test tree used to
    // be exempt from both gates; now only a `.test.` name or a written-down
    // path is.
    expect(isScaffolding("src/test/leak.ts")).toBe(false);
    expect(isScaffolding("src/test/mocks/leak.ts")).toBe(false);
    expect(isScaffolding("src/test/helpers/deep/leak.tsx")).toBe(false);
    // And the other direction, or the rule would just be "nothing is exempt".
    expect(isScaffolding("src/test/setup.ts")).toBe(true);
    expect(isScaffolding("src/components/chat/MessageBubble.test.tsx")).toBe(
      true,
    );
    // Windows hands out backslashes; the list is written with forward ones.
    expect(isScaffolding("src\\test\\mocks\\api.ts")).toBe(true);
  });

  /**
   * S-09c (persisted-store allowlist, second half): a NAME cannot be added.
   *
   * See PERSISTED_KEY_SHAPES at module scope for the full argument and for the
   * one honest limit this rule does not cover.
   */
  it("S-09c: no persisted key may hold a name a person reads on screen", () => {
    const persisted = [...persistedKeys()];
    expect(
      persisted.length,
      "the persisted list emptied itself (broken scan guard)",
    ).toBeGreaterThan(25);

    // Both directions. A key persisted but undescribed is a key nobody argued
    // for; a key described but not persisted is a stale argument that makes
    // the table look more considered than it is.
    expect(
      persisted.slice().sort(),
      "uiStore persists a key with no entry in PERSISTED_KEY_SHAPES, or the " +
        "table describes a key nothing persists. Every persisted key has to " +
        "say what it may hold, because localStorage is plaintext on disk and " +
        "outside the vault.",
    ).toEqual(Object.keys(PERSISTED_KEY_SHAPES).sort());

    // Anti-cheat 1: a guard that rejects everything would sail through the
    // name sweep below while describing nothing.
    for (const [key, shape] of Object.entries(PERSISTED_KEY_SHAPES)) {
      expect(
        shape.accepts(shape.sample),
        `"${key}" declares a shape that rejects its own sample ` +
          `${JSON.stringify(shape.sample)}. A guard that accepts nothing is ` +
          `not a description, it is a way past the name sweep.`,
      ).toBe(true);
      expect(
        shape.why.length,
        `"${key}" is persisted with no reason written down. Putting a value ` +
          `on the device outside the vault is a decision, not a default.`,
      ).toBeGreaterThan(20);
    }

    // Anti-cheat 2: measured against the running store, not against itself.
    const live = useUiStore.getState() as unknown as Record<string, unknown>;
    for (const [key, shape] of Object.entries(PERSISTED_KEY_SHAPES)) {
      expect(
        shape.accepts(live[key]),
        `"${key}" holds ${JSON.stringify(live[key])} in the real store but ` +
          `its declared shape rejects it. Either the field changed and the ` +
          `declaration rotted, or the declaration was never true.`,
      ).toBe(true);
    }

    // THE RULE. Nothing on this list may be storable under any allowlisted key.
    expect(
      keysAcceptingAProbe(PERSISTED_KEY_SHAPES),
      "A persisted key accepts something a person reads on screen. That key " +
        "would write a chat or character NAME into localStorage, which is " +
        "plaintext, sits outside the SQLCipher vault, and is not among the " +
        "paths browser_profile.py purges. Ids may leave the vault; names " +
        "never may. Narrow the shape, or keep the value in the vault.",
    ).toEqual([]);

    // POSITIVE CONTROL. The sweep above passes by finding nothing, which is
    // also what it would do if every guard had stopped being consulted. Same
    // function, same probe list, fed the exact mistake the rule exists for:
    // somebody adds the chat's title next to the chat's id.
    const withATitle: Record<string, PersistedShape> = {
      chatTitle: {
        why: "so the sidebar remembers what it was called",
        accepts: (v) => typeof v === "string",
        sample: "Untitled chat",
      },
    };
    expect(
      keysAcceptingAProbe(withATitle).length,
      "S-09c went blind: a `typeof v === 'string'` guard was not flagged",
    ).toBeGreaterThan(0);
    // A subtler one: somebody narrows the guard to "short, no punctuation"
    // and believes that excludes names. It does not.
    const withAShortName: Record<string, PersistedShape> = {
      lastPersonaName: {
        why: "just a short label, surely harmless",
        accepts: (v) => typeof v === "string" && v.length <= 32,
        sample: "Aria",
      },
    };
    expect(
      keysAcceptingAProbe(withAShortName).length,
      "S-09c went blind to a length-capped free string",
    ).toBeGreaterThan(0);
    // ...and discriminating: the shapes actually in use must stay silent, or
    // the control is just an assertion that everything fails.
    expect(
      keysAcceptingAProbe({ msgFontPx: PERSISTED_KEY_SHAPES.msgFontPx }),
    ).toEqual([]);
    expect(
      keysAcceptingAProbe({ genSeed: PERSISTED_KEY_SHAPES.genSeed }),
    ).toEqual([]);
    expect(
      keysAcceptingAProbe({ chatBgTint: PERSISTED_KEY_SHAPES.chatBgTint }),
    ).toEqual([]);
  });

  /**
   * S-26: the window title never says what the app is showing.
   *
   * Setting `document.title` in a chat app is the most ordinary feature there
   * is - the tab, or the taskbar, showing which conversation is open. In this
   * app it is a leak, and a quiet one.
   *
   * WHY IT LEAKS. The page title becomes the top-level window's title, and
   * window titles are ENUMERABLE: any process on the machine can walk the
   * window list with EnumWindows/GetWindowText, at no privilege at all, and
   * read it. That is the whole vault defeated by a string - the conversation
   * stays encrypted at rest while its name sits in a list anyone can read.
   *
   * WHY IT LOOKS SAFE. Under pywebview the native window title is set once at
   * creation and is NOT re-synced from the document, so setting
   * `document.title` leaves the visible window still reading "Elysium".
   * Somebody testing this by eye concludes it did nothing. Whether the string
   * reaches the native title is a property of the host build, not of this
   * code, and it is not a property worth betting a vault on.
   *
   * FOUR VECTORS, not one. A `document.title` scan alone is not enough here:
   *
   *  1. `document.title = chat.name` - the obvious one.
   *  2. Reaching the element (`querySelector("title").textContent = ...`).
   *  3. A `<title>` rendered ANYWHERE in the tree. This is React 19, which
   *     hoists a rendered title element into the head by itself. No helmet
   *     library, no import, no `document` reference - one JSX tag in a
   *     component and the window title is live. A `document.title` grep never
   *     sees it, which is exactly why it is listed separately.
   *  4. A document-head library (react-helmet and its forks) doing 3 for you.
   *
   * And index.html's own title must stay a fixed literal, since a build-time
   * substitution there would be the same leak one layer earlier.
   */
  it("S-26: the document title is never set from anything the app is showing", () => {
    const patterns: { re: RegExp; leak: string }[] = [
      {
        re: /\bdocument\s*\.\s*title\b/,
        leak:
          "assigning document.title publishes that string on the window title, " +
          "which any process can read with EnumWindows. pywebview does not " +
          "re-sync it to the native window, so the visible title staying " +
          '"Elysium" is not evidence that nothing left.',
      },
      {
        re: /(?:querySelector|getElementsByTagName)\s*\(\s*["'`][^"'`]*\btitle\b/i,
        leak:
          "reaching the title element and writing to it is the same leak as " +
          "document.title, one indirection further from the grep that looks " +
          "for it.",
      },
      {
        re: /<title[\s>]/,
        leak:
          "React 19 hoists a rendered title element into the head from " +
          "anywhere in the tree, so a title tag holding chat.name in an " +
          "ordinary component sets the window title without ever naming " +
          "`document`.",
      },
      {
        re: /\breact-helmet\b|from\s+["']react-head["']/,
        leak:
          "a document-head library exists to write the title element, which " +
          "is the one thing this app must not write.",
      },
    ];

    const appFiles = getAppSourceFiles("**/*.{ts,tsx}");
    expect(
      appFiles.length,
      "S-26 scanned too few files (guard against a broken glob)",
    ).toBeGreaterThan(50);
    for (const file of appFiles) {
      const content = stripComments(readFile(file));
      for (const { re, leak } of patterns) {
        expect(
          re.test(content),
          `${path.relative(SRC_DIR, file)} matches ${re}: ${leak}`,
        ).toBe(false);
      }
    }

    // index.html: exactly one title, and a fixed literal. A build-time
    // substitution here is the same leak moved one layer earlier.
    const html = readFile(path.resolve(SRC_DIR, "index.html"));
    const titles = [...html.matchAll(/<title[^>]*>([\s\S]*?)<\/title>/gi)];
    expect(titles.length, "index.html should have exactly one title element").toBe(1);
    expect(
      titles[0][1].trim(),
      "index.html's title must stay a fixed word that says nothing about what " +
        "is open. It is the window title before any script runs.",
    ).toBe("Elysium");

    // POSITIVE CONTROL, on the SAME RegExp objects the scan just used. Every
    // vector gets the one-line version of the feature request it stands for.
    const knownBad: [string, number][] = [
      ["document.title = chat.name;", 0],
      ["window.document . title = `${c.name} in Elysium`;", 0],
      ['document.querySelector("head > title").textContent = chat.name;', 1],
      ['document.getElementsByTagName("title")[0].text = n;', 1],
      ["return <title>{chat.name}</title>;", 2],
      ['import { Helmet } from "react-helmet-async";', 3],
    ];
    for (const [bad, idx] of knownBad) {
      expect(patterns[idx].re.test(bad), `S-26 went blind to: ${bad}`).toBe(true);
    }
    // ...and discriminating. All of these are in the tree today and none of
    // them is a window title, so a rule that fired on them would be deleted
    // by the next person who hit it.
    for (const good of [
      "title={attachTitle}",
      'const title = chat.name ?? "New chat";',
      '<h2 className="chat-title">{title}</h2>',
      "document.titleBarHeight",
      "aria-label={`Rename chat ${title}`}",
    ]) {
      expect(
        patterns.some((p) => p.re.test(good)),
        `S-26 would reject a legitimate line: ${good}`,
      ).toBe(false);
    }
  });

  /**
   * S-27: no text field carries an autofill identity.
   *
   * WHAT THE LEAK IS. Chromium keeps a form-history table in `Web Data`, a
   * plain unencrypted SQLite file in the WebView2 profile. On form submission
   * it copies typed values into that table so it can offer them back later.
   * `backend/browser_profile.py` purges Cache, Network, History, Sessions,
   * Top Sites and the Crashpad reports - `Web Data` is on none of those lists
   * (measured against its `_BODY_DIRS` / `_BODY_FILES`). So a chat title typed
   * once would sit in cleartext on disk, outside the vault, across every
   * launch, and be offered back as a dropdown suggestion the next time
   * somebody renames a chat. That is the owner's rule broken twice over: the
   * name is outside the vault, and it is back on screen unasked.
   *
   * WHY IT IS CLEAN TODAY, AND WHY THAT IS AN ACCIDENT. Nothing is stored
   * because Chromium skips a field whose autofill name is empty, and the
   * autofill name comes from the `name` attribute falling back to `id`
   * (Blink's HTMLFormControlElement::NameForAutofill). Not one input or
   * textarea in this tree carries a `name`, and the chat-title form has
   * neither - but nothing said so, and neither attribute is unusual to add.
   *
   * WHICH MECHANISM IS HONEST. Not `autoComplete="off"`. Chromium's stated
   * position is that it overrides `autocomplete=off` for the password manager
   * and for structured address/credit-card Autofill, on the grounds that users
   * want their own data filled regardless of what a site asked for. A field
   * labelled "Title" sitting next to a name is exactly the shape those
   * heuristics classify, so `off` is a request, not a guarantee. ABSENCE OF
   * IDENTITY is structural instead: the form-history row is KEYED on the field
   * name, so a field with no name has nothing to be stored under. That is the
   * primary rule here. `autoComplete="off"` is accepted only as the second
   * line for a field that genuinely needs an `id`, and never on its own.
   *
   * WHAT THIS RULE DOES NOT CLAIM. `select` is out of scope: Chromium's
   * single-field history only stores text inputs, so a select cannot reach
   * that table and banning ids on selects would cost accessibility for
   * nothing. `textarea` IS in scope even though it is likewise skipped by form
   * history, because it is where message text and character descriptions live
   * and the cost of covering it is zero.
   */
  it("S-27: no input or textarea carries a name attribute", () => {
    const tsxFiles = getAppSourceFiles("**/*.tsx");
    expect(
      tsxFiles.length,
      "S-27 found no components to scan (broken glob guard)",
    ).toBeGreaterThan(30);

    const tags: FieldTag[] = [];
    for (const file of tsxFiles) {
      tags.push(...findFieldTags(readFile(file), path.relative(SRC_DIR, file)));
    }
    // Floor on the PARSE, not just the file list. A tag walker that stopped
    // matching would hand back an empty array from a healthy file list.
    expect(
      tags.length,
      "S-27 parsed no input/textarea tags at all - the JSX walker is broken, " +
        "not the tree clean",
    ).toBeGreaterThan(20);

    const named = tags
      .filter((t) => NAME_ATTR.test(t.attrs))
      .map((t) => `${t.file}:${t.line} <${t.tag}>`);
    expect(
      named,
      "A text field carries a `name` attribute. `name` is the key Chromium " +
        "files typed values under in the unencrypted `Web Data` form-history " +
        "table, which browser_profile.py does not purge - so whatever is typed " +
        "here would persist in cleartext outside the vault and be suggested " +
        "back on screen later. This app never submits a form natively, so " +
        "`name` buys nothing. Delete it and use aria-label for the accessible " +
        "name. If a label association is genuinely needed, use `id` plus " +
        '`autoComplete="off"` and add the field to ID_BEARING_FIELDS.',
    ).toEqual([]);

    // POSITIVE CONTROL, on the SAME walker and the SAME attribute pattern.
    // This rule passes by finding nothing, so a walker that returned nothing
    // and a clean tree are indistinguishable without this.
    const synthetic = [
      '<input name="chatTitle" value={draftTitle} />',
      "<textarea\n  name={`chat-${id}`}\n  value={draft}\n/>",
      '<Input name="title" value={titleInput} />',
    ];
    for (const bad of synthetic) {
      const parsed = findFieldTags(bad, "synthetic.tsx");
      expect(parsed.length, `S-27's walker went blind to: ${bad}`).toBe(1);
      expect(
        NAME_ATTR.test(parsed[0].attrs),
        `S-27 went blind to a name attribute in: ${bad}`,
      ).toBe(true);
    }
    // ...and discriminating. Every one of these is real code in this tree and
    // none of them is an autofill identity.
    for (const good of [
      '<input type="text" aria-label={`Rename chat ${title}`} value={draftTitle} />',
      '<input type="range" data-name="x" aria-label="Speed slider" />',
      "<textarea ref={textareaRef} value={draft} onChange={handleInput} />",
    ]) {
      const parsed = findFieldTags(good, "synthetic.tsx");
      expect(parsed.length).toBe(1);
      expect(
        NAME_ATTR.test(parsed[0].attrs),
        `S-27 would reject a legitimate field: ${good}`,
      ).toBe(false);
    }
  });

  it("S-27b: a text field with an id must also switch autofill off", () => {
    /**
     * Text fields allowed to carry an `id`, each with the argument for it.
     *
     * `id` is the FALLBACK autofill name, so a text input carrying one sits in
     * exactly the position a `name` would put it. The only reason to accept
     * one is a label pairing through `htmlFor`, which is a real accessibility
     * need, and the price of accepting it is `autoComplete="off"` on the same
     * tag.
     *
     * `openDefect` is not a blessing. It marks a field that is on this list
     * because it exists and could not be changed from here, NOT because the
     * argument for it is good. The count of those is pinned below, so the list
     * cannot quietly grow, and fixing one turns this test red until the entry
     * is deleted - which is the point.
     */
    const ID_BEARING_FIELDS: {
      file: string;
      identity: string;
      why: string;
      openDefect: boolean;
    }[] = [
      // CLOSED 21 August 2026, and the entry is deleted rather than kept with
      // openDefect flipped, because this comment block says a stale exemption
      // excuses the next one. The field was the free-text TTS engine
      // parameter in VoiceSettingsPage: a type="text" input with a DOM id and
      // no autoComplete, so its id was a valid autofill key and an engine
      // parameter typed there could reach Chromium's Web Data file, outside
      // the vault. It took the first of the two fixes this file suggested,
      // autoComplete="off", because the id is still load-bearing: the label
      // above it pairs by htmlFor.
      //
      // The list is deliberately empty. If it stays empty, the count below
      // stays 0 and any NEW id-bearing text field has to be argued for in a
      // diff rather than quietly added.
    ];

    const tsxFiles = getAppSourceFiles("**/*.tsx");
    const tags: FieldTag[] = [];
    for (const file of tsxFiles) {
      tags.push(...findFieldTags(readFile(file), path.relative(SRC_DIR, file)));
    }
    expect(tags.length, "S-27b parsed no tags (broken walker guard)").toBeGreaterThan(
      20,
    );

    /** Can this tag's value ever reach Chromium's form-history table? */
    const isTextCapable = (t: FieldTag) => {
      const type = TYPE_ATTR.exec(t.attrs)?.[2];
      return !(type && NON_TEXT_INPUT_TYPES.has(type));
    };

    const offenders: string[] = [];
    const claimed = new Set<string>();
    for (const t of tags) {
      if (!ID_ATTR.test(t.attrs)) continue;
      if (!isTextCapable(t)) continue; // a range/checkbox/file has no typed text
      if (AUTOCOMPLETE_OFF.test(t.attrs)) continue; // paid the price
      const rel = t.file.split("\\").join("/");
      const hit = ID_BEARING_FIELDS.find(
        (e) => e.file === rel && t.attrs.includes(e.identity),
      );
      if (hit) {
        claimed.add(hit.identity);
        continue;
      }
      offenders.push(`${t.file}:${t.line} <${t.tag}>`);
    }

    expect(
      offenders,
      "A text field carries an `id` and does not switch autofill off. Blink " +
        "uses the `id` attribute as the autofill name when there is no `name` " +
        "(HTMLFormControlElement::NameForAutofill), so this field has a valid " +
        "key for Chromium's form-history table in the unencrypted `Web Data` " +
        "file - which browser_profile.py does not purge. Anything typed here " +
        "would outlive the session in cleartext outside the vault. Either " +
        "drop the id and name the field with aria-label, or add " +
        '`autoComplete="off"` and write the field into ID_BEARING_FIELDS ' +
        "with the reason the id is needed.",
    ).toEqual([]);

    // The exemption list cannot rot in either direction: an entry that no
    // longer matches anything is a stale argument, and the number of entries
    // standing on "it exists" rather than "it is fine" is pinned so it cannot
    // creep upward one commit at a time.
    expect(
      [...claimed].sort(),
      "an ID_BEARING_FIELDS entry matches nothing in the tree - the field was " +
        "fixed or moved, and a stale exemption excuses the next one",
    ).toEqual(ID_BEARING_FIELDS.map((e) => e.identity).sort());
    expect(
      ID_BEARING_FIELDS.filter((e) => e.openDefect).length,
      "the number of KNOWN-BAD id-bearing text fields changed. Going up needs " +
        "an argument in the diff; going down means one was fixed, so delete " +
        "its entry.",
    ).toBe(0);
    for (const e of ID_BEARING_FIELDS) {
      expect(
        e.why.length,
        `${e.file} is exempt with no reason written down`,
      ).toBeGreaterThan(40);
    }

    // POSITIVE CONTROL, on the SAME three patterns the scan just used.
    const cases: [string, boolean][] = [
      // [source, should this be reported]
      ['<input id="chat-title" type="text" value={draftTitle} />', true],
      ["<textarea id={`edit-${id}`} value={editDraft} />", true],
      ['<Input id="seed" type="number" value={seed} />', true],
      // The escape hatch has to actually work, or nobody can satisfy the rule.
      ['<input id="chat-title" type="text" autoComplete="off" value={t} />', false],
      // Not text-capable: no typed value can reach the form-history table.
      ['<input id="fog" type="checkbox" checked={on} />', false],
      ['<input id="zoom" type="range" value={z} />', false],
      ['<input id="pick" type="file" onChange={h} />', false],
      // No identity at all: the shape the whole tree already uses.
      ['<input type="text" aria-label="Search characters" value={v} />', false],
    ];
    for (const [source, expected] of cases) {
      const [t] = findFieldTags(source, "synthetic.tsx");
      expect(t, `S-27b's walker went blind to: ${source}`).toBeDefined();
      const reported =
        ID_ATTR.test(t.attrs) && isTextCapable(t) && !AUTOCOMPLETE_OFF.test(t.attrs);
      expect(reported, `S-27b misjudged: ${source}`).toBe(expected);
    }
  });

  // S-28: the draft cache may not reach the device, and the folder it lives
  // in is the one folder that cannot catch it doing so.
  //
  // S-09 exempts everything under lib/store from its `localStorage.setItem`
  // scan, because that is where the persisted UI store legitimately lives.
  // draftStore.ts was asked to live there too - it is a store - which means
  // the single rule that would normally notice a persistence mistake is
  // blind to this exact file. S-09b does not cover it either: its brace
  // walker reads uiStore.ts by hard-coded path and nothing else.
  //
  // So the gap is named and closed here, pinned to the file rather than the
  // directory. Drafts are the most sensitive text in the app - unsent
  // sentences the user has not decided to keep - and browser storage is
  // plaintext on disk, outside the vault, and outlives the process.
  it("S-28: the draft store persists nothing", () => {
    const draftStorePath = path.resolve(
      SRC_DIR,
      "src",
      "lib",
      "store",
      "draftStore.ts",
    );
    // FLOOR. A rule whose target file was renamed away would otherwise read
    // an empty string and pass by finding nothing in nothing.
    expect(
      existsSync(draftStorePath),
      "S-28's target file is gone - the rule is guarding nothing",
    ).toBe(true);
    const content = readFile(draftStorePath);
    // The floor measures CODE, not prose. Measured raw, the leading docblock
    // alone clears any sane threshold, so the file could be gutted down to a
    // docblock plus a single re-export and this rule would stay green while
    // every consumer silently got a different store. `stripComments` is only
    // safe to use for a length floor: blinding it can only make this number
    // smaller, which fails closed.
    const code = stripComments(content);
    expect(
      code.length,
      "S-28 read a draftStore.ts with almost no code in it",
    ).toBeGreaterThan(1500);

    // The matcher, reused verbatim by the positive control below so the two
    // can never drift apart.
    const FORBIDDEN = [
      "localStorage",
      "sessionStorage",
      "indexedDB",
      "document.cookie",
      "caches",
      // The persist path writes to localStorage without ever naming it,
      // which is exactly how S-09 was escaped once before.
      "persist",
      "createJSONStorage",
    ];
    // RAW text, deliberately un-stripped.
    //
    // This started out stripping comments, so the file's docblock could
    // describe the prohibition without tripping it. The control below proved
    // that unsafe: `stripComments` is a regex, not a lexer, so an unbalanced
    // comment opener inside an ordinary string literal opens a comment that
    // never existed and deletes the real lines after it - including a storage
    // write. Everywhere else in this suite that is survivable because another
    // rule is behind it. Here it is not: S-09 exempts every path under
    // lib/store from its device-storage scan, so for THIS file S-28 is the
    // only text gate there is.
    //
    // So the scanner lost the stripper and draftStore.ts's docblock lost the
    // API names instead. Blunter prose, exact gate.
    const offenders = (source: string) =>
      FORBIDDEN.filter((needle) => source.includes(needle));

    expect(
      offenders(content),
      "draftStore.ts reaches for device storage",
    ).toEqual([]);

    // Re-export laundering: the forbidden token can sit in a NEIGHBOUR file
    // that this rule never opens. Scanning one file cannot see that, so the
    // rule at least refuses to let this file hand its identity to another.
    expect(
      /export\s+\*\s+from/.test(code),
      "draftStore.ts re-exports another module wholesale, so what it IS is " +
        "no longer what this rule read",
    ).toBe(false);

    // POSITIVE CONTROL on the SAME matcher: a scan that went blind would
    // report nothing here too, and would look identical from the outside.
    expect(
      offenders('const x = localStorage.getItem("drafts")'),
    ).toContain("localStorage");
    expect(
      offenders("export const s = create()(persist((set) => ({})))"),
    ).toContain("persist");
    // And it must not fire on the ordinary contents of the file, or the rule
    // would be unsatisfiable and would get deleted rather than obeyed.
    expect(offenders("const encoder = new TextEncoder();")).toEqual([]);

    // The control that made this rule drop its comment stripper. A write
    // hidden behind an unbalanced comment opener inside a string is invisible
    // to `stripComments` and must NOT be invisible to the scan.
    const smuggled = [
      'const OPEN = "/*";',
      'window.localStorage.setItem("elysium-drafts", text);',
      'const CLOSE = "*/";',
    ].join("\n");
    expect(
      offenders(smuggled),
      "a storage write hidden behind a fake comment opener escaped S-28",
    ).toContain("localStorage");
    expect(offenders(stripComments(smuggled))).toEqual([]);
  });
});
