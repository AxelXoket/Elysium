/**
 * tabs-outline-utilities.test.ts - why `focus-visible:outline-1` drew nothing
 * next to `outline-none`.
 *
 * U-71 removed that class from TabsContent. The removal is only defensible if
 * the reason is written down somewhere a future reader can check, because the
 * class LOOKS like a focus indicator and the obvious next move is to put it
 * back.
 *
 * TEXT, not getComputedStyle. jsdom applies no Tailwind utility rules at all -
 * helpers/glassSurfaceCss.ts records that measurement - so
 * `getComputedStyle(el).outlineStyle` reads exactly the same before and after
 * this change and could not have caught anything. The compiled stylesheet is
 * parsed instead, the same pattern right-panel-readability.test.ts uses.
 *
 * THE STYLESHEET HALF IS DOCUMENTATION, AND IT WAS ALL THIS FILE HAD. Those
 * assertions describe what Tailwind emits for its own utilities; they cannot
 * go red on anything in this repository. Re-adding the class to `TabsContent`
 * left every one of them green - measured. The last block is the one that
 * guards: it renders the components and asserts which of them may carry the
 * pairing, which is the claim the rest of the file only explains.
 */
import { beforeAll, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { loadGlassRightCss } from "@/test/helpers/glassSurfaceCss";

let css = "";

beforeAll(async () => {
  css = await loadGlassRightCss();
}, 60_000);

/** The declaration body of the first rule whose selector text contains
 *  `needle`. Parsed rather than pattern-matched across the whole file, so a
 *  declaration belonging to some other rule cannot answer for this one. */
function ruleBody(needle: string): string {
  const idx = css.indexOf(needle);
  expect(idx, `the stylesheet has no rule containing ${needle}`)
    .toBeGreaterThan(-1);
  const open = css.indexOf("{", idx);
  const close = css.indexOf("}", open);
  expect(open, `no block for ${needle}`).toBeGreaterThan(-1);
  expect(close, `unterminated block for ${needle}`).toBeGreaterThan(open);
  return css.slice(open + 1, close);
}

describe("the outline utilities Tailwind compiles", () => {
  it("compiled something worth reading", () => {
    // The watchman's watchman. Every assertion below is a search over this
    // string; an empty or failed build would make all of them vacuous.
    expect(css.length).toBeGreaterThan(1000);
  });

  it("outline-none turns the style variable off, unconditionally", () => {
    // Both halves matter. The plain `outline-style: none` is what stops the
    // browser drawing anything, and the custom property is what any later
    // width utility on the same element will read back.
    const body = ruleBody(".outline-none");

    expect(body).toMatch(/--tw-outline-style:\s*none/);
    expect(body).toMatch(/outline-style:\s*none/);
  });

  it("focus-visible:outline-1 only READS that variable", () => {
    // It sets a width and defers the style. On an element that also carries
    // `outline-none`, the variable it defers to is already `none`, so the
    // width applies to a line that is never drawn. That is the whole reason
    // the class was dead on TabsContent - not a missing colour, which would
    // have fallen back to currentColor.
    // Tailwind compiles the variant NESTED - `.focus-visible\:outline-1 {
    // &:focus-visible { ... } }` - so the selector to look for carries no
    // pseudo-class of its own, and the body read here is the inner block.
    const body = ruleBody(".focus-visible\\:outline-1");

    expect(body).toMatch(/outline-style:\s*var\(--tw-outline-style\)/);
    expect(body).not.toMatch(/outline-style:\s*solid/);
  });

  it("the variable's own initial value is solid, so the pairing is the cause",
    () => {
      // Without this, "the variable is none" could just be Tailwind's
      // default and the class would be dead everywhere - including on
      // TabsTrigger, where it demonstrably is not. It starts out `solid`;
      // only `outline-none` on the same element turns it off.
      const body = ruleBody("@property --tw-outline-style");

      expect(body).toMatch(/initial-value:\s*solid/);
    });
});

describe("who is allowed to carry the pairing", () => {
  // THE GUARD. Everything above explains why `outline-none` and
  // `focus-visible:outline-1` cancel each other; nothing above notices if
  // the pair comes back. These do, on the rendered className - the same
  // surface TabsFocusRing.test.tsx measures, and not the component's source
  // text.

  function renderTabs() {
    render(
      <Tabs defaultValue="a">
        <TabsList>
          <TabsTrigger value="a">A</TabsTrigger>
        </TabsList>
        <TabsContent value="a">panel</TabsContent>
      </Tabs>,
    );
  }

  it("no element pairs outline-none with an outline width", () => {
    renderTabs();
    render(<ScrollArea>scrolls</ScrollArea>);

    const suspects = [
      screen.getByRole("tabpanel"),
      screen.getAllByRole("tab")[0],
      ...Array.from(document.querySelectorAll<HTMLElement>(
        '[data-slot="scroll-area-viewport"]')),
    ];
    expect(suspects.length).toBeGreaterThanOrEqual(3);

    for (const el of suspects) {
      const dead = el.className.includes("outline-none")
        && /focus-visible:outline-\d/.test(el.className);
      expect(dead, `${el.dataset.slot ?? el.tagName} carries both`).toBe(false);
    }
  });

  it("and the trigger still has a real outline, because it may", () => {
    // POSITIVE CONTROL. The rule is not "never use the utility" - on the
    // trigger it pairs with `focus-visible:outline-ring` and NO
    // `outline-none`, so it draws. Without this, deleting the utility
    // everywhere would pass the test above.
    renderTabs();
    const trigger = screen.getAllByRole("tab")[0];

    expect(trigger.className).toMatch(/focus-visible:outline-1/);
    expect(trigger.className).not.toContain("outline-none");
  });
});

