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
import { readFileSync, existsSync } from "fs";
import { globSync } from "glob";
import path from "path";
import catalogue from "../../../shared/error_catalogue.json";
// @ts-expect-error - plain JS, shared with eslint.config.js so the two gates
// answer "is this scaffolding" the same way. No types, and none wanted: a .d.ts
// beside it would be a second declaration of the same thing.
import { SUPPORT_FILES, isScaffolding } from "../../privacy-scope.js";

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
    const uiStorePath = path.resolve(SRC_DIR, "src", "lib", "store", "uiStore.ts");
    const content = readFile(uiStorePath);

    // v1.1 audit L8: extract the partialize object body by BRACE-BALANCING
    // from `=> ({` to its matching `})`. The old non-greedy regex stopped at
    // the FIRST `})`, so a value literal containing `})` (e.g. Object.assign({}))
    // would truncate the scanned region and let every later key escape.
    const head = /partialize:\s*\([^)]*\)\s*=>\s*\(\s*\{/.exec(content);
    expect(head, "Could not locate partialize(...) in uiStore.ts").not.toBeNull();
    const openBrace = head!.index + head![0].length - 1; // index of the '{'
    let depth = 0;
    let closeBrace = -1;
    for (let i = openBrace; i < content.length; i++) {
      const ch = content[i];
      if (ch === "{") depth++;
      else if (ch === "}") {
        depth--;
        if (depth === 0) {
          closeBrace = i;
          break;
        }
      }
    }
    expect(closeBrace, "Unbalanced partialize object braces").toBeGreaterThan(
      openBrace,
    );
    const body = content.slice(openBrace + 1, closeBrace);

    const ALLOWED_PERSISTED_KEYS = new Set([
      "selectedCharacterId",
      "selectedChatId",
      "selectedModelId",
      "activeRightPanelTab",
      "sidebarCollapsed",
      // Appearance preferences (Settings panel) - harmless reader/display
      // numbers and flags; never content, drafts, or secrets.
      "msgFontPx",
      "msgLineHeight",
      // Message contrast preset (Soft/Default/High) - a display preference.
      "msgContrast",
      "narrationEnabled",
      "quoteTintEnabled",
      // Continuous voice (V9-1) - one boolean saying "speak replies aloud".
      // No content, no id, nothing about WHAT was said. Persisted because
      // somebody who turned it on meant to leave it on; it defaults to false
      // so a fresh profile is silent.
      "continuousVoice",
      // "The device-local narration mode has been moved into the vault." One
      // boolean, and the reason the old `narrationVoice` key is gone from this
      // list: a setting the server also reads cannot have a second home here.
      "narrationMigrated",
      // "I closed the 'voice is set up but nothing is chosen' hint." One
      // boolean about a piece of UI chrome - no content, no id. Persisted
      // because a hint that returns every launch is a nag; the dialog's own
      // open state is deliberately NOT persisted.
      "voiceHintDismissed",
      // Appearance only (V11): a hex string for message ink and one of three
      // finish names. Neither is content, an id, or anything about a person.
      "msgInk",
      "surfaceFinish",
      // How solid a message bubble's fill is, 0.35..1. A number about paint,
      // in the same family as msgInk and surfaceFinish above it.
      "msgOpacity",
      // Chat background scalars - the image itself lives as a Blob in the
      // approved appearance store (see S-13), never in localStorage.
      "chatBgOn",
      "chatBgLum",
      "chatBgContrast",
      "chatBgTint",
      // Framing: which part of that Blob to show, as percentages, plus how
      // far in it is cropped and the picture's own width-to-height ratio.
      // Four numbers describing a rectangle over an image the user chose -
      // no filename, no path, nothing about the picture's contents. The LIVE
      // chat-area ratio (chatAreaAspect) is deliberately absent: it measures
      // this window rather than stating a preference, so it is session-only.
      "chatBgFocusX",
      "chatBgFocusY",
      "chatBgZoom",
      "chatBgAspect",
      "ambientFogOn",
      // Generation sampling scalars (v1.1 FF7) - neutral names, no user
      // content. stopSequences (character names) are deliberately NOT here:
      // they stay in-memory in the GenerationSettingsProvider.
      "genTemperature",
      "genTopP",
      "genTopK",
      "genRepetitionPenalty",
      "genMaxOutput",
      "genSeed",
      "genContextBudget",
    ]);

    // v1.1 audit L8 (value provenance): every persisted entry must be exactly
    // `key: state.<sameKey>,` - a direct mirror of the store field, with the
    // SAME name on both sides. This closes the gap where the old name-only
    // check let `genSeed: state.vaultKey` through (an allowlisted key aliasing
    // a secret). Anything derived, renamed, or content-bearing fails here.
    const entryRe = /([A-Za-z_$][\w$]*)\s*:\s*state\.([A-Za-z_$][\w$]*)\s*,/g;
    const entries = [...body.matchAll(entryRe)];
    expect(
      entries.length,
      "no `key: state.key` entries parsed from partialize (broken scan guard)",
    ).toBeGreaterThan(0);

    // No line in the body may be anything OTHER than a `key: state.key` entry
    // (or a comment/blank). A value like `x: someFn(state.y)` would not match
    // and would leave a residue - catch it.
    const lineRe = /^[A-Za-z_$][\w$]*\s*:\s*state\.[A-Za-z_$][\w$]*\s*,?$/;
    const residue = body
      .split("\n")
      // The `\r` strip is not cosmetic: `.` does not match a carriage return
      // and `$` (no /m) only matches end-of-string, so on a CRLF checkout the
      // comment strip below silently fails and every comment inside partialize
      // is reported as a violation. A privacy guard must not go red for a
      // reason that has nothing to do with privacy.
      .map((l) => l.replace(/\r$/, "").replace(/\/\/.*$/, "").trim())
      .filter((l) => l.length > 0)
      .filter((l) => !lineRe.test(l));
    expect(
      residue,
      `partialize contains non-\`key: state.key\` lines: ${JSON.stringify(residue)}`,
    ).toEqual([]);

    for (const [, key, source] of entries) {
      expect(
        ALLOWED_PERSISTED_KEYS.has(key),
        `Non-allowlisted key "${key}" persisted by uiStore partialize`,
      ).toBe(true);
      // Provenance: the value must read the field of the SAME name.
      expect(
        source,
        `partialize key "${key}" reads state.${source} (must mirror its own name)`,
      ).toBe(key);
    }

    // Belt-and-suspenders: no draft/message/persona/attachment/secret field may
    // appear anywhere in the persisted region, whatever the key is spelled.
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
    const lowerBody = body.toLowerCase();
    for (const needle of forbiddenSubstrings) {
      expect(
        lowerBody.includes(needle),
        `Forbidden field "${needle}" found in uiStore partialize`,
      ).toBe(false);
    }

    // POSITIVE CONTROL. The three checks above all pass by finding nothing
    // wrong with today's partialize. That is also what they would do if the
    // allowlist, the provenance rule or the substring sweep had quietly
    // stopped discriminating. Each is handed a body it MUST reject, using the
    // same regexes and the same constants the real scan just used.
    const rejects = (synthetic: string) => {
      const found = [...synthetic.matchAll(entryRe)];
      const lower = synthetic.toLowerCase();
      return (
        found.length === 0 ||
        found.some(([, key]) => !ALLOWED_PERSISTED_KEYS.has(key)) ||
        found.some(([, key, source]) => source !== key) ||
        forbiddenSubstrings.some((needle) => lower.includes(needle)) ||
        synthetic
          .split("\n")
          .map((l) => l.replace(/\r$/, "").replace(/\/\/.*$/, "").trim())
          .filter((l) => l.length > 0)
          .some((l) => !lineRe.test(l))
      );
    };
    // A key nobody allowlisted.
    expect(rejects("newThing: state.newThing,"), "allowlist stopped biting").toBe(true);
    // The exact aliasing bug the provenance rule was written for: an
    // allowlisted name reading a field that is not its own.
    expect(rejects("msgFontPx: state.vaultKey,"), "provenance stopped biting").toBe(true);
    // A forbidden word smuggled in under a harmless key.
    expect(rejects("msgFontPx: state.msgFontPx, // password"),
      "substring sweep stopped biting").toBe(true);
    // Anything that is not a plain mirror at all. Named for what actually
    // fires: `derive(state.x)` never matches the entry pattern, so it is the
    // parse that rejects this, not the residue sweep further down.
    expect(rejects("msgFontPx: derive(state.msgFontPx),"),
      "entry parsing stopped discriminating").toBe(true);
    // ...and the shape it is supposed to accept still gets through, so the
    // control is discriminating and not just always-true.
    expect(rejects("msgFontPx: state.msgFontPx,")).toBe(false);
  });

  // S-10 was deleted in KADEME 19a. It banned "openrouter.ai" from
  // src/lib/api/completions.ts alone; S-01 above bans the same string from
  // every .ts/.tsx/.css file under src, and completions.ts is in that list
  // (measured, not assumed). A weaker copy of an existing gate only dilutes
  // the count of what is actually guarded.

  // S-11: No Authorization header in any source file
  // KEPT in KADEME 20b, against section 4's list. The listed condition was
  // explicit: delete only after the PC-02 prefix collision is fixed. It is
  // not fixed - the privacy-contract registry still resolves a rule by raw
  // substring, and `"S-11` is a prefix of `"S-11b`, so deleting this rule
  // would leave the registry still reporting it as present.
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
});
