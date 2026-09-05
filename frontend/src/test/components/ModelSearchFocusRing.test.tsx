/**
 * ModelSearchFocusRing.test.tsx - what the shipped stylesheet draws around the
 * model search box when a keyboard lands on it.
 *
 * The box carried `outline-none` and nothing in its place. The app's own ring
 * lives in `@layer base` and Tailwind emits its utilities into
 * `@layer utilities`; at equal specificity the later layer wins, so one
 * utility class deleted the only indicator a keyboard user had for where they
 * were in a list of hundreds of models.
 *
 * getComputedStyle cannot answer this. jsdom applies no Tailwind rule at all
 * and drops the nested `@layer` blocks the real sheet is made of -
 * helpers/glassSurfaceCss.ts records that measurement. So the sheet is
 * compiled through the project's OWN Vite pipeline, flattened into rules with
 * their layer, and the cascade is settled here against the REAL rendered
 * element via `Element.matches`. Nothing below reads a component's source
 * text, and no class name is copied out of one: every selector comes from the
 * compiled stylesheet, and every element comes from a render.
 *
 * One limit, stated rather than discovered later: the walker keeps a rule's
 * selector but not the media condition it sits under, so an outline declared
 * only inside an `@media` block would be counted as if it always applied. No
 * outline rule in this sheet is conditional today. If one appears, this is
 * the sentence that says where to look.
 */
import { beforeAll, describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { ModelPanel } from "@/components/models/ModelPanel";
import { TooltipProvider } from "@/components/ui/tooltip";
import { GenerationSettingsProvider } from "@/components/generation/GenerationSettingsContext";
import { renderWithQueryClient } from "@/test/helpers/renderWithQueryClient";
import { loadGlassRightCss } from "@/test/helpers/glassSurfaceCss";
import { mockFetch } from "@/test/mocks/api";
import { modelListFixture } from "@/test/mocks/fixtures";

// ── The sheet, flattened ────────────────────────────────────────────────────

interface FlatRule {
  /** The @layer this rule was emitted into, "" when it is outside all of them. */
  layer: string;
  /** Nesting resolved, so `&:focus-visible` reads as the selector it becomes. */
  selector: string;
  /** Declaration text of this rule's own block, children excluded. */
  decls: string;
  /** Position in the file, the tie-break the cascade uses within one layer. */
  order: number;
}

/** A small CSS walker. It exists because the parser that ships with jsdom
 *  refuses the constructs this stylesheet is built from, and because a regex
 *  over the whole file cannot say which block a declaration belongs to - the
 *  same reason tabs-outline-utilities.test.tsx parses rather than greps. */
function flattenRules(css: string): FlatRule[] {
  const rules: FlatRule[] = [];
  const stack: string[] = [];
  let prelude = "";
  let decls = "";
  const pending: string[] = [];
  let order = 0;

  const layerOf = (): string => {
    for (let i = stack.length - 1; i >= 0; i--) {
      const m = /^@layer\s+([\w-]+)$/.exec(stack[i].trim());
      if (m) return m[1];
    }
    return "";
  };

  const selectorOf = (): string => {
    let resolved = "";
    for (const raw of stack) {
      const part = raw.trim();
      if (part.startsWith("@")) continue;
      if (part.includes("&")) {
        resolved = resolved
          ? part.replace(/&/g, resolved)
          : part.replace(/&/g, "");
      } else {
        resolved = resolved ? `${resolved} ${part}` : part;
      }
    }
    return resolved.trim();
  };

  for (let i = 0; i < css.length; i++) {
    const ch = css[i];
    if (ch === "{") {
      stack.push(prelude);
      pending.push(decls);
      prelude = "";
      decls = "";
    } else if (ch === "}") {
      const selector = selectorOf();
      const layer = layerOf();
      if (selector && decls.trim()) {
        rules.push({ layer, selector, decls, order: order++ });
      }
      stack.pop();
      decls = pending.pop() ?? "";
      prelude = "";
    } else if (ch === ";") {
      decls += `${prelude};`;
      prelude = "";
    } else {
      prelude += ch;
    }
  }
  return rules;
}

/** Layer precedence, read from the sheet's own `@layer a, b, c;` statement
 *  rather than assumed. Getting this from anywhere else would make the test
 *  agree with a guess instead of with what ships. */
function layerOrder(css: string): string[] {
  // The sheet opens with more than one such statement - a bare `@layer
  // properties;` reserves a single name before the real ordering line - so
  // the longest list is the one that ranks the layers against each other.
  const found = [...css.matchAll(/@layer\s+([\w\s,-]+);/g)].map((m) =>
    m[1].split(",").map((s) => s.trim()),
  );
  expect(
    found.length,
    "the sheet no longer declares its layer order up front",
  ).toBeGreaterThan(0);
  return found.reduce((best, next) => (next.length > best.length ? next : best));
}

const OUTLINE_OFF =
  /(?:^|;)\s*outline(?:-style)?\s*:\s*none\s*(?:;|$)|(?:^|;)\s*outline(?:-width)?\s*:\s*0(?:px)?\s*(?:;|$)/;
const OUTLINE_ON =
  /(?:^|;)\s*outline\s*:\s*[^;]*\b[1-9]\d*px|(?:^|;)\s*outline-width\s*:\s*[1-9]/;

/** Every rule in the sheet that has an opinion about this element's outline
 *  while it holds focus, in the order the cascade settles them. */
function outlineRulesFor(
  el: Element,
  rules: FlatRule[],
  order: string[],
): FlatRule[] {
  const rank = (layer: string) => {
    const i = order.indexOf(layer);
    // A rule outside every layer beats all of them, which is what the cascade
    // does and what the unlayered half of this stylesheet relies on.
    return i === -1 ? order.length : i;
  };

  return rules
    .filter((r) => OUTLINE_OFF.test(r.decls) || OUTLINE_ON.test(r.decls))
    .filter((r) => {
      // The focused state is the question, so the pseudo-class that encodes
      // it is taken as satisfied and the rest of the selector is matched for
      // real. A bare `:focus-visible` addresses every element.
      const base = r.selector.replace(/:focus-visible/g, "").trim() || "*";
      try {
        return el.matches(base);
      } catch {
        return false;
      }
    })
    .sort((a, b) => rank(a.layer) - rank(b.layer) || a.order - b.order);
}

/** The verdict: what a browser would actually paint on this element when the
 *  keyboard puts focus on it. */
function drawsAFocusOutline(
  el: Element,
  rules: FlatRule[],
  order: string[],
): boolean {
  const applicable = outlineRulesFor(el, rules, order);
  if (applicable.length === 0) return false;
  const last = applicable[applicable.length - 1];
  return OUTLINE_ON.test(last.decls) && !OUTLINE_OFF.test(last.decls);
}

let css = "";
let rules: FlatRule[] = [];
let order: string[] = [];

beforeAll(async () => {
  css = await loadGlassRightCss();
  rules = flattenRules(css);
  order = layerOrder(css);
}, 60_000);

afterEach(() => {
  vi.restoreAllMocks();
});

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <TooltipProvider>
      <GenerationSettingsProvider>{children}</GenerationSettingsProvider>
    </TooltipProvider>
  );
}

async function renderPanel() {
  mockFetch({ "/models/openrouter": { body: modelListFixture } });
  renderWithQueryClient(<ModelPanel />, { wrapper });
  await waitFor(() => {
    expect(screen.getByLabelText("Search models")).toBeInTheDocument();
  });
  return screen.getByLabelText("Search models");
}

describe("the walker that settles the cascade", () => {
  it("read a real stylesheet and found its layers", () => {
    // The watchman's watchman. Every verdict below is a query over these two
    // values; an empty compile or a failed parse would make all of them
    // vacuously true.
    expect(css.length).toBeGreaterThan(1000);
    expect(rules.length).toBeGreaterThan(100);
    expect(order).toContain("base");
    expect(order).toContain("utilities");
  });

  it("puts utilities after base, which is why one class could win", () => {
    // The mechanism the defect ran on, taken from the sheet rather than
    // asserted from memory. If this ever flips, the fix below stops being
    // necessary and this file should say so out loud.
    expect(order.indexOf("utilities")).toBeGreaterThan(order.indexOf("base"));
  });

  it("sees an outline being switched off when one really is", () => {
    // POSITIVE CONTROL. The claim further down is an ABSENCE - that nothing
    // in the sheet turns this element's outline off - and an absence proves
    // nothing until the sweep has been caught finding one. This specimen is
    // an element the sheet does suppress.
    const { container } = render(<div className="outline-none" />);
    const specimen = container.firstElementChild!;

    const applicable = outlineRulesFor(specimen, rules, order);
    expect(applicable.length).toBeGreaterThan(0);
    expect(drawsAFocusOutline(specimen, rules, order)).toBe(false);
  });
});

describe("the model search box under a keyboard", () => {
  it("is a real tab stop", async () => {
    // The rest of this file is about what gets painted on a focused element.
    // If focus cannot land here at all, those assertions are about nothing.
    const input = await renderPanel();
    input.focus();

    expect(document.activeElement).toBe(input);
  });

  it("keeps a visible focus outline", async () => {
    // RED BEFORE THE FIX: `outline-none` sat in @layer utilities and settled
    // last, so the winning rule declared `outline-style: none` and a keyboard
    // user searching 200-odd models saw nothing at all.
    const input = await renderPanel();

    const applicable = outlineRulesFor(input, rules, order);
    expect(
      applicable.length,
      "no rule in the shipped sheet has any opinion about this outline",
    ).toBeGreaterThan(0);
    expect(
      drawsAFocusOutline(input, rules, order),
      "the last rule to win on the focused search box paints no outline",
    ).toBe(true);
  });

  it("draws the same indicator the panel's other controls get", async () => {
    // GROUND CONTROL, and it is the reason the fix adds no new class. A
    // control one section above already gets the house ring from the same
    // base rule; the search box was the only thing in this panel opting out
    // of it. Measuring both in one pass is what shows the sweep can tell the
    // two states apart on real elements from the same render.
    const input = await renderPanel();
    const neighbour = screen.getByRole("button", { name: /Model Source/i });

    expect(drawsAFocusOutline(neighbour, rules, order)).toBe(true);
    expect(drawsAFocusOutline(input, rules, order)).toBe(true);
  });
});
