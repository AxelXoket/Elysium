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

  // S-07: No @fontsource
  it("S-07: no @fontsource in source", () => {
    for (const file of allSrcFiles) {
      const content = readFile(file);
      expect(
        content.includes("@fontsource"),
        `Found "@fontsource" in ${path.relative(SRC_DIR, file)}`,
      ).toBe(false);
    }
  });

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

    const match = /partialize:\s*\([^)]*\)\s*=>\s*\(\{([\s\S]*?)\}\)/.exec(content);
    // Vacuous-pass guard: the partialize block MUST be found, else the check is
    // meaningless (mirrors the S-12 file-count guard).
    expect(match, "Could not locate partialize(...) in uiStore.ts").not.toBeNull();

    const body = match![1];

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
      "narrationEnabled",
      "quoteTintEnabled",
      // Chat background scalars - the image itself lives as a Blob in the
      // approved appearance store (see S-13), never in localStorage.
      "chatBgOn",
      "chatBgLum",
      "chatBgContrast",
      "chatBgTint",
      "ambientFogOn",
    ]);

    const keys = [...body.matchAll(/([A-Za-z_$][\w$]*)\s*:/g)].map((m) => m[1]);
    // Guard: at least one persisted key must be parsed, else an empty capture
    // would let the allowlist assertion pass vacuously.
    expect(
      keys.length,
      "no keys parsed from partialize body (guard against a broken scan)",
    ).toBeGreaterThan(0);

    for (const key of keys) {
      expect(
        ALLOWED_PERSISTED_KEYS.has(key),
        `Non-allowlisted key "${key}" persisted by uiStore partialize`,
      ).toBe(true);
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
});
