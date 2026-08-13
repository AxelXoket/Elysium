/**
 * css-contract.test.ts - rules that only exist if something can match them.
 *
 * Three audit findings shared one shape: CSS that reads as intentional, is
 * documented in a comment right above it, and cannot fire. jsdom has no layout
 * and no `prefers-reduced-transparency`, so these are checked statically -
 * against the selectors the components actually render.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync, statSync } from "fs";
import path from "path";

const SRC = path.resolve(__dirname, "..");
const CSS_RAW = readFileSync(path.join(SRC, "index.css"), "utf-8");
/** Comments explain the rules; they are not the rules. Stripped before every
 *  selector check, or the explanation of a removed class would keep matching. */
const CSS = CSS_RAW.replace(/\/\*[\s\S]*?\*\//g, "");

function allComponentSource(): string {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = path.join(dir, entry);
      if (statSync(full).isDirectory()) {
        if (entry === "test" || entry === "node_modules") continue;
        walk(full);
        continue;
      }
      if (/\.tsx?$/.test(entry)) out.push(readFileSync(full, "utf-8"));
    }
  };
  walk(SRC);
  return out.join("\n");
}

const COMPONENTS = allComponentSource();

/** The first balanced `{...}` block inside `text`. */
function blockAfter(text: string): string {
  let depth = 0;
  for (let i = text.indexOf("{"); i >= 0 && i < text.length; i += 1) {
    if (text[i] === "{") depth += 1;
    else if (text[i] === "}") {
      depth -= 1;
      if (depth === 0) return text.slice(0, i + 1);
    }
  }
  throw new Error("unterminated block");
}

const REDUCED = "@media (prefers-reduced-transparency: reduce)";

/** Every reduced-transparency block joined - there is more than one, and the
 *  surface-finish one is separate on purpose. */
function reducedBlocks(): string {
  const out: string[] = [];
  let from = 0;
  for (;;) {
    const at = CSS.indexOf(REDUCED, from);
    if (at < 0) break;
    out.push(blockAfter(CSS.slice(at, at + 4000)));
    from = at + REDUCED.length;
  }
  expect(out.length, "the reduced-transparency block is gone").toBeGreaterThan(0);
  return out.join("\n");
}

describe("reduced-transparency block", () => {
  const block = reducedBlocks();

  it("names the fog classes MistCanvas actually renders", () => {
    // .ambient-fog-canvas / .ambient-mist / .chat-fog appeared exactly once in
    // the whole repo - inside this selector list - so the animated WebGL fog
    // kept running for users who had turned transparency effects off.
    for (const real of [".mist-canvas", ".canvas-mist", ".panel-mist"]) {
      expect(block).toContain(real);
    }
    for (const dead of [".ambient-fog-canvas", ".ambient-mist", ".chat-fog"]) {
      expect(block).not.toContain(dead);
    }
  });

  it("covers every blurred surface in the stylesheet", () => {
    // The list used to cover only [class*="glass"] and .message-bubble-shell in
    // practice; five blurred, text-bearing surfaces kept a live backdrop-filter
    // - which is exactly the readability cost the block was written to remove.
    const blurred = new Set<string>();
    const lines = CSS.split("\n");
    lines.forEach((line, i) => {
      if (!/^\s*backdrop-filter:/.test(line)) return;
      for (let j = i - 1; j >= 0; j -= 1) {
        const candidate = lines[j].trim();
        if (candidate.endsWith("{")) {
          blurred.add(candidate.slice(0, -1).trim());
          return;
        }
      }
    });

    // Floor. The set is built by matching a property name in the stylesheet;
    // a typo in that pattern empties it and this whole rule then iterates
    // nothing and passes. Five surfaces today.
    expect(
      blurred.size,
      "found no blurred surface in the stylesheet - did the scan break?",
    ).toBeGreaterThan(3);

    for (const selector of blurred) {
      if (selector.startsWith("@")) continue;
      const covered =
        selector.includes("glass") ||
        selector
          .split(/[\s:>]/)
          .filter((part) => part.startsWith("."))
          .some((part) => block.includes(part));
      expect(covered, `${selector} keeps its blur with transparency reduced`)
        .toBe(true);
    }
  });

  it("lists no class that no component renders", () => {
    const classes = [...block.matchAll(/\.([a-z][a-z0-9-]*)/g)].map((m) => m[1]);
    // Floor. The block being non-empty is checked where it is built; that
    // says nothing about this regex still finding class names inside it.
    expect(
      classes.length,
      "no class names parsed out of the reduced-transparency block",
    ).toBeGreaterThan(3);
    for (const cls of new Set(classes)) {
      // A dash-prefix counts too: some class names are composed at render time
      // (`surface-${surfaceFinish}`), so the full string never appears in the
      // source. The rule being tested is "something can produce this", not
      // "this literal is typed somewhere".
      const prefix = cls.includes("-")
        ? `${cls.slice(0, cls.lastIndexOf("-"))}-`
        : null;
      const used =
        COMPONENTS.includes(cls) ||
        (prefix != null && COMPONENTS.includes(prefix)) ||
        CSS.includes(`.${cls} {`) ||
        CSS.includes(`.${cls}:`) ||
        CSS.includes(`.${cls}::`);
      expect(used, `.${cls} is listed here but nothing renders or defines it`)
        .toBe(true);
    }
  });
});

describe("dark-wallpaper scrollbar", () => {
  it("writes the dark-wallpaper thumb as a two-class rule and drops the one-class version", () => {
    // Both match the SAME element: the scroller carries Tailwind's
    // overflow-y-auto and sits inside .elysium-page. A single-class selector
    // (0-1-1) lost to the page-wide two-class rule (0-2-1), so the thumb stayed
    // dark-on-dark over a dark wallpaper.
    expect(CSS).toContain(
      ".elysium-page .chat-bg-dark::-webkit-scrollbar-thumb",
    );
    const bare = CSS.match(/(^|\n)\.chat-bg-dark::-webkit-scrollbar-thumb\s*\{/);
    expect(bare, "the outranked single-class rule is still there").toBeNull();
  });
});

describe("message-ink preview", () => {
  it("has something that can put the class on a preview", () => {
    // `.settings-preview.msg-ink-custom` existed but no component ever added
    // the class or the variable to a preview, so the picker showed a contrast
    // ratio and no colour at all.
    expect(CSS).toContain(".settings-preview.msg-ink-custom");
    expect(COMPONENTS).toMatch(/settings-preview[\s\S]{0,400}msg-ink-custom/);
    expect(COMPONENTS).toContain("--msg-ink-custom");
  });
});
