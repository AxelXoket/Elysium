/**
 * css-contract.test.ts - rules that only exist if something can match them.
 *
 * Three audit findings shared one shape: CSS that reads as intentional, is
 * documented in a comment right above it, and cannot fire. jsdom has no layout
 * and no `prefers-reduced-transparency`, so these are checked statically -
 * against the selectors the components actually render. The blur coverage
 * case is the exception to "statically": it parses the COMPILED sheet with
 * postcss, because reading selectors out of raw lines is how it went blind.
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

  it("names the fog classes MistCanvas actually renders", async () => {
    // .ambient-fog-canvas / .ambient-mist / .chat-fog appeared exactly once in
    // the whole repo - inside this selector list - so the animated WebGL fog
    // kept running for users who had turned transparency effects off.
    //
    // The live half of that list used to be typed out here, which is the same
    // hole one step removed: a hand-typed name survives a rename in the
    // component, so the block would go on hiding a class nothing renders and
    // this test would stay green. The names come from rendering the fog
    // layers now. The import is deferred so only this one rule pays for React
    // and jsdom and the rest of the file stays fs-only.
    const { fogRootClassNames } = await import("./helpers/fogClassNames");
    const roots = fogRootClassNames();

    // Positive control, and the reason this rule was rewritten: an empty
    // render is covered by any selector list at all, including an empty one.
    expect(roots, "no fog layer rendered").toHaveLength(4);

    /** The block NAMES a class, rather than merely containing its text -
     *  `.mist` must not pass because `.mist-canvas` is written here. */
    const names = (block: string, cls: string) =>
      new RegExp(`\\.${cls.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![\\w-])`)
        .test(block);

    for (const { component, classes } of roots) {
      expect(classes, `${component} rendered no element to hide`)
        .not.toHaveLength(0);
      // display:none on the root takes the layer's whole subtree with it, so
      // one named class per layer is the requirement - not every descendant.
      expect(
        classes.some((cls) => names(block, cls)),
        `${component} renders "${classes.join(" ")}" and the ` +
          "reduced-transparency block names none of those classes",
      ).toBe(true);
    }

    for (const dead of [".ambient-fog-canvas", ".ambient-mist", ".chat-fog"]) {
      expect(block).not.toContain(dead);
    }
  });

  it("covers every blurred surface in the stylesheet", async () => {
    // The list used to cover only [class*="glass"] and .message-bubble-shell in
    // practice; five blurred, text-bearing surfaces kept a live backdrop-filter
    // - which is exactly the readability cost the block was written to remove.
    //
    // This case used to walk the raw text upwards from each `backdrop-filter:`
    // line to the first line ending in `{`, which in a multi-line selector
    // list is the LAST selector of the list. Measured: `.chat-action-menu`
    // shares one rule with `.chat-action-confirm`, so the scan saw only the
    // confirm, found it covered, and the menu kept its live blur with
    // transparency reduced while this test stayed green. And
    // `selector.includes("glass")` let three surfaces certify themselves by
    // their own spelling without the reduced-transparency block naming them.
    // Both holes are the same mistake, reading CSS as lines, so the sheet is
    // parsed now: the COMPILED sheet, through postcss, every selector of every
    // blurred rule with nesting folded in, and `[class*="glass"]` is applied
    // as the attribute substring it is rather than as a keyword. The imports
    // are deferred so this one case pays for the Vite pipeline and the rest
    // of the file stays fs-only.
    const { loadGlassRightCss } = await import("./helpers/glassSurfaceCss");
    const {
      parseSheet,
      resolvedSelectors,
      inMedia,
      REDUCED_TRANSPARENCY,
      switchOffReaches,
    } = await import("./helpers/compiledSheet");
    const root = parseSheet(await loadGlassRightCss());

    const blurred = new Set<string>();
    let blurRules = 0;
    root.walkDecls(/^(-webkit-)?backdrop-filter$/, (decl) => {
      // `none !important` is the switch-off, not a surface.
      if (!/blur\(/.test(decl.value)) return;
      const rule = decl.parent;
      if (!rule || rule.type !== "rule") return;
      blurRules += 1;
      // No per-rule comparison against `rule.selectors` here. For a
      // top-level rule that is `resolvedSelectors` measured against its own
      // first line, which cannot go red; for a NESTED blurred rule it is
      // wrong, because the folded selector deliberately no longer contains
      // the raw `&`. The detector for a narrowed collector is the named
      // surface below, which a narrowing really does drop.
      for (const selector of resolvedSelectors(rule)) blurred.add(selector);
    });

    const switchedOff = new Set<string>();
    root.walkDecls(/^(-webkit-)?backdrop-filter$/, (decl) => {
      if (!/^none\b/.test(decl.value.trim())) return;
      if (!inMedia(decl, REDUCED_TRANSPARENCY)) return;
      const rule = decl.parent;
      if (!rule || rule.type !== "rule") return;
      for (const selector of resolvedSelectors(rule)) switchedOff.add(selector);
    });

    // Floors. Both sets are built by matching a property name in the sheet;
    // a typo in either pattern empties it and the loop below then iterates
    // nothing, or covers nothing, and passes.
    expect(
      blurred.size,
      "found no blurred surface in the stylesheet - did the scan break?",
    ).toBeGreaterThan(3);
    expect(
      blurRules,
      "no rule carries a blur; the surfaces below came from nowhere",
    ).toBeGreaterThan(0);
    expect(
      switchedOff.size,
      "the reduced-transparency block switches nothing off",
    ).toBeGreaterThan(0);
    // The scanner's own positive control, and the reason this case exists.
    // `.chat-action-menu` shares its rule with `.chat-action-confirm`, which
    // is exactly the shape the old line-walking scan dropped; the floors
    // above cannot see that, because a scan narrowed to one selector per
    // rule still finds plenty of surfaces and still covers all of them. So
    // the surface the hole hid is named here: if it is not in the set, the
    // set is not the sheet's blurred surfaces, whatever its size.
    expect(
      blurred,
      "the blur scan no longer reaches .chat-action-menu; a rule's selector " +
        "list is being read as fewer selectors than it has",
    ).toContain(".chat-action-menu");

    for (const selector of blurred) {
      const covered = [...switchedOff].some((entry) =>
        switchOffReaches(entry, selector),
      );
      expect(covered, `${selector} keeps its blur with transparency reduced`)
        .toBe(true);
    }
  }, 60_000);

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

/**
 * The handle's conditional yield to a delete confirmation.
 *
 * Same shape as the rest of this file: three numbers in three separate rules
 * have to stay ordered, and nothing about that ordering is observable in jsdom
 * (index.css is never loaded into the test DOM, and the outcome is a paint
 * order, not a value any node carries). So the numbers are read back out of
 * the stylesheet and compared. Every one is READ, never retyped - hardcoding
 * "5" here would let the rule and its test drift apart in lockstep.
 */
describe("panel handle yields to a delete confirmation", () => {
  function zIndexOf(selector: string): number {
    const at = CSS.indexOf(selector + " {");
    expect(at, `no rule for "${selector}"`).toBeGreaterThan(-1);
    const block = CSS.slice(at, CSS.indexOf("}", at));
    const m = block.match(/z-index:\s*(-?\d+)\s*;/);
    expect(m, `"${selector}" declares no z-index`).not.toBeNull();
    return Number(m![1]);
  }

  const YIELD = ".warm-canvas:has(.message-action-confirm) .panel-toggle";

  it("clears the confirm panel while it is open", () => {
    expect(
      zIndexOf(YIELD),
      "the handle still paints over an irreversible choice",
    ).toBeLessThan(zIndexOf(".message-action-confirm"));
  });

  it("yields no further than it has to", () => {
    // The minimal-yield half. Dropping under the hover action row as well
    // would be a second, unasked-for concession: that row is not irreversible
    // and the handle is the only way back into a collapsed panel.
    expect(
      zIndexOf(YIELD),
      "the handle gave up more ground than the confirm needed",
    ).toBeGreaterThan(zIndexOf(".message-actions"));
  });

  it("stays on top when no confirm is open", () => {
    // GROUND CONTROL: the yield is conditional. If the base rule had simply
    // been lowered, both assertions above would still pass while the handle
    // sat under every bubble all the time.
    expect(zIndexOf(".panel-toggle")).toBeGreaterThan(
      zIndexOf(".message-action-confirm"),
    );
  });

  it("depends only on selectors components actually render", () => {
    const src = allComponentSource();
    for (const cls of ["warm-canvas", "message-action-confirm", "panel-toggle"]) {
      expect(src.includes(cls), `nothing renders .${cls}`).toBe(true);
    }
  });
});
