/**
 * Static safety tests — scan frontend source for privacy/security violations.
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

describe("Static safety tests", () => {
  const allSrcFiles = getSourceFiles("**/*.{ts,tsx,css}");
  const apiAndComponentFiles = getSourceFiles(
    "src/{lib/api,components}/**/*.{ts,tsx}",
  );

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

  // S-03: No console.log
  it("S-03: no console.log in source", () => {
    for (const file of allSrcFiles) {
      const content = readFile(file);
      expect(
        content.includes("console.log"),
        `Found "console.log" in ${path.relative(SRC_DIR, file)}`,
      ).toBe(false);
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

  // S-08: /complete only in approved completion API file
  it("S-08: /complete only in approved completion API file", () => {
    const approvedFiles = new Set([
      path.resolve(SRC_DIR, "src", "lib", "api", "completions.ts"),
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

  // S-12: Source file count guard (prevents vacuous pass if glob breaks)
  it("S-12: source file count above safe threshold", () => {
    expect(allSrcFiles.length).toBeGreaterThan(10);
  });

  // S-13: No indexedDB usage
  it("S-13: no indexedDB in source", () => {
    for (const file of allSrcFiles) {
      if (file.endsWith(".css")) continue;
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
});
