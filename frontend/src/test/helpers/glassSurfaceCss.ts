/**
 * glassSurfaceCss.ts - measuring a colour against the REAL cascade.
 *
 * jsdom has no layout engine, and its CSS parser silently drops several
 * constructs Tailwind v4 actually emits (nested `@layer`, `@property`, the
 * generated utility rules themselves land as "Could not parse CSS
 * stylesheet" on stderr) - confirmed by hand before this module was written:
 * `.text-muted-foreground`'s own `color` rule never applies in jsdom, so
 * `getComputedStyle(el).color` comes back as the browser default black for a
 * class whose real, shipped rule is `color: var(--color-muted-foreground)`.
 *
 * What DOES survive, reliably and reproducibly, is CSS custom PROPERTY
 * inheritance: a value assigned to `--some-token` on an ancestor element is
 * visible via `getComputedStyle(el).getPropertyValue('--some-token')` on any
 * descendant, correctly preferring the nearest redefinition over an outer
 * one. That is the exact mechanism `.glass-right` uses to re-theme this
 * surface, so it is what these tests measure - not `.color`, which cannot be
 * trusted here for a class Tailwind generates rather than one written by
 * hand in index.css.
 *
 * One consequence worth being explicit about: `:root`'s own custom
 * properties do not reliably reach `getPropertyValue` in jsdom either (also
 * confirmed by hand - `--color-es-danger`, declared only at `:root`, reads
 * back empty here regardless of which class is on the element). Tests that
 * depend on a `:root`-only token therefore cannot use this technique and say
 * so at the point they fall back to something else.
 *
 * The stylesheet under test is not a hand-copied excerpt: it is index.css
 * run through the project's OWN Vite + Tailwind pipeline (the same one that
 * ships to the browser), so a real rename or a real colour change here is a
 * real red test, not a fixture that quietly stopped matching reality.
 */
import { createServer, type ViteDevServer } from "vite";
import path from "path";

let cached: Promise<string> | null = null;

/** Vite's dev transform of a CSS file is a JS module that hands the actual
 *  CSS to the browser as a string literal (`const __vite__css = "..."`) for
 *  hot-reload to inject. This pulls that literal back out, scanning for the
 *  matching unescaped quote rather than assuming no interior `"` - the real
 *  stylesheet has plenty (attribute selectors, font stacks). */
function extractCssLiteral(code: string): string {
  const marker = "const __vite__css = \"";
  const start = code.indexOf(marker);
  if (start < 0) {
    throw new Error(
      "index.css did not transform into the expected __vite__css literal - " +
        "has Vite's dev CSS injection format changed?",
    );
  }
  let i = start + marker.length;
  while (i < code.length) {
    if (code[i] === "\\") { i += 2; continue; }
    if (code[i] === "\"") break;
    i += 1;
  }
  return JSON.parse(code.slice(start + marker.length - 1, i + 1));
}

/**
 * Compiles index.css through the real dev pipeline and caches the result for
 * the life of the test process - a Vite dev server costs several hundred ms
 * to spin up, and every file that imports this helper needs the same output.
 */
export function loadGlassRightCss(): Promise<string> {
  if (!cached) {
    cached = (async () => {
      // helpers/ -> test -> src -> frontend (this file's own root).
      const root = path.resolve(__dirname, "..", "..", "..");
      let server: ViteDevServer | undefined;
      try {
        server = await createServer({
          root,
          server: { middlewareMode: true },
          logLevel: "error",
          configFile: path.resolve(root, "vite.config.ts"),
        });
        const result = await server.transformRequest("/src/index.css");
        if (!result) throw new Error("index.css did not transform");
        return extractCssLiteral(result.code);
      } finally {
        await server?.close();
      }
    })();
  }
  return cached;
}

/** Appends real CSS text as a stylesheet jsdom can match selectors against. */
export function injectCss(css: string): HTMLStyleElement {
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);
  return style;
}

/** A `.glass-right` island, attached to the document so the cascade (and
 *  jsdom's selector matching) can actually see it. Callers own detaching it. */
export function wrapInGlassRight(): HTMLDivElement {
  const wrap = document.createElement("div");
  wrap.className = "glass-right";
  document.body.appendChild(wrap);
  return wrap;
}

/** The value a custom property resolves to AT this element, following the
 *  real cascade - not a hand-copied hex the stylesheet could drift away
 *  from. Empty string means unset (see the module comment on `:root`). */
export function surfaceToken(el: Element, name: string): string {
  return getComputedStyle(el).getPropertyValue(name).trim();
}
