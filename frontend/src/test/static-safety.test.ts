/**
 * Static safety tests - scan frontend source for privacy/security violations.
 * These tests read source files on disk and check for forbidden patterns.
 * They exclude themselves, node_modules, dist, and test fixture files.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { globSync } from "glob";
import path from "path";

const SRC_DIR = path.resolve(__dirname, "../../");
const THIS_FILE = path.resolve(__filename);

/** Get all source files matching a glob pattern, excluding this test and non-source dirs. */
function getSourceFiles(pattern: string): string[] {
  const files = globSync(pattern, {
    cwd: SRC_DIR,
    absolute: true,
    ignore: [
      "**/node_modules/**",
      "**/dist/**",
      "**/.vite/**",
      "**/test/mocks/**",
    ],
  });
  return files.filter((f) => f !== THIS_FILE);
}

function readFile(filePath: string): string {
  return readFileSync(filePath, "utf-8");
}

/** True for files that are part of the test suite (not shipped app source). */
function isTestFile(filePath: string): boolean {
  return (
    filePath.includes(`${path.sep}test${path.sep}`) ||
    /\.test\.(ts|tsx)$/.test(filePath)
  );
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
    for (const file of cssFiles) {
      const content = readFile(file);
      expect(
        content.includes("url(http"),
        `Found "url(http" in ${path.relative(SRC_DIR, file)}`,
      ).toBe(false);
    }
  });

  // S-06: No @import url(http
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
    const nonStoreFiles = allSrcFiles.filter(
      (f) => !f.includes(path.join("lib", "store")),
    );
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
  });

  // S-10: No openrouter.ai in completions API file
  it("S-10: no openrouter.ai in completions API", () => {
    const file = path.resolve(SRC_DIR, "src", "lib", "api", "completions.ts");

    const content = readFile(file);
    expect(
      content.includes("openrouter.ai"),
      "Found openrouter.ai in completions API",
    ).toBe(false);
  });

  // S-11: No Authorization header in any source file
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
  });

  // S-12: Source file count guard (prevents vacuous pass if glob breaks)
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
    for (const file of genFiles) {
      const content = stripComments(readFile(file));
      for (const field of fields) {
        const asKey = new RegExp("(?<![\\w\"'`])" + field + "\\s*:");
        const asMember = new RegExp("\\." + field + "\\b");
        expect(
          asKey.test(content) || asMember.test(content),
          `Provider field "${field}" injected in ${path.relative(SRC_DIR, file)}`,
        ).toBe(false);
      }
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
    for (const file of htmlFiles) {
      // Protocol-relative `//host` counts: it inherits the page scheme and
      // still leaves the machine.
      const remote = readFile(file).match(
        /\b(?:href|src|imagesrcset|srcset)\s*=\s*["'](?:https?:)?\/\//gi,
      );
      expect(
        remote,
        `Remote reference in ${path.relative(SRC_DIR, file)}: ${remote?.join(", ")}`,
      ).toBeNull();
    }
  });
});
