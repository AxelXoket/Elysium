/**
 * TabsFocusRing.test.tsx - the right-panel tabpanel that outline-none left
 * silent.
 *
 * base-ui hands the open TabsContent a real tab stop: role="tabpanel",
 * tabIndex 0. tabs.tsx paired that with a bare `outline-none` and no
 * `focus-visible:ring` companion, so a keyboard user tabbing into Models,
 * Security, Persona or Notes landed on a focusable element with zero visible
 * indicator - a WCAG 2.4.7 failure. scroll-area.tsx already carries the
 * house pattern for exactly this shape; the fix copies it rather than
 * inventing a new ring.
 *
 * jsdom applies no stylesheet (see ContrastOrthogonality.test.tsx), so the
 * honest assertion here is the same one the rest of this suite already makes
 * for Tailwind-driven behaviour: the rendered className, and real DOM focus
 * via document.activeElement. Neither reads the component's source text.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";

/** The classes that actually draw the ring.
 *
 * `focus-visible:outline-1` used to be in this list, and requiring it locked
 * the defect in place: the class draws NOTHING on this element, so the fix
 * for it would have turned this suite red. See the outline-utilities test
 * next door for why it is dead - `outline-none` on the same element declares
 * `--tw-outline-style: none`, and `focus-visible:outline-1` only READS that
 * variable.
 */
const RING_CLASSES = [
  "focus-visible:ring-[3px]",
  "focus-visible:ring-ring/50",
];

function Basic({ contentClassName }: { contentClassName?: string }) {
  return (
    <Tabs defaultValue="a">
      <TabsList>
        <TabsTrigger value="a">A</TabsTrigger>
        <TabsTrigger value="b">B</TabsTrigger>
      </TabsList>
      <TabsContent value="a" className={contentClassName}>panel a</TabsContent>
      <TabsContent value="b">panel b</TabsContent>
    </Tabs>
  );
}

describe("the open tabpanel's focus ring", () => {
  it("pairs outline-none with a visible focus-visible ring", () => {
    render(<Basic />);
    const panel = screen.getByRole("tabpanel");

    expect(panel.className).toContain("outline-none");
    // A `for` over an empty array asserts nothing and passes. This is the
    // guard against the loop below going vacuous - measured, because the
    // obvious alternative does not work: naming one class outside the loop
    // stays green when the array is emptied, since that assertion never
    // reads the array.
    expect(RING_CLASSES.length).toBeGreaterThan(0);
    for (const cls of RING_CLASSES) {
      expect(panel.className).toContain(cls);
    }
    // And one of them again by name, so replacing the array's contents with
    // something that is merely non-empty is caught too.
    expect(panel.className).toContain("focus-visible:ring-[3px]");
    // Negative control: the tab strip sits right beside the panel and is not
    // the thing this defect is about. If the assertion above were vacuously
    // true for anything in the tree, this would also pass - it must not.
    const list = screen.getByRole("tablist");
    expect(list.className).not.toContain("focus-visible:ring-[3px]");
  });

  it("carries no dead outline utility", () => {
    // `outline-none` and `focus-visible:outline-1` on the SAME element are a
    // contradiction that resolves silently in favour of nothing: the first
    // declares `--tw-outline-style: none` unconditionally, the second only
    // reads that variable back. The panel's visible indicator is the ring,
    // and the outline utility was decoration that could be mistaken for one.
    render(<Basic />);
    const panel = screen.getByRole("tabpanel");

    expect(panel.className).toContain("outline-none");
    expect(panel.className).not.toMatch(/focus-visible:outline-\d/);

    // POSITIVE CONTROL: the TRIGGER really does carry it, and there it is
    // alive - it pairs with `focus-visible:outline-ring` and no
    // `outline-none`. Without this, deleting the utility everywhere would
    // pass the assertion above.
    expect(screen.getAllByRole("tab")[0].className)
      .toMatch(/focus-visible:outline-1/);
  });

  it("is a real tab stop that can actually hold focus", () => {
    render(<Basic />);
    const panel = screen.getByRole("tabpanel");

    expect(panel).toHaveAttribute("tabindex", "0");
    panel.focus();
    expect(document.activeElement).toBe(panel);
    // Ground: focus landing somewhere is not the same as focus landing HERE.
    expect(document.activeElement).not.toBe(document.body);
  });

  it("keeps the ring when a caller supplies its own className", () => {
    // The exact shapes RightPanel.tsx passes for Models/Security ("flex-1
    // overflow-hidden") and Notes ("flex-1 overflow-y-auto"). Appending
    // className after the base string (rather than the base losing to a
    // caller's classes under tailwind-merge) is what a bare `outline-none`
    // with no ring companion would silently survive without anyone noticing,
    // since none of these callers pass their own outline/ring utilities.
    for (const contentClassName of ["flex-1 overflow-hidden", "flex-1 overflow-y-auto"]) {
      const { unmount } = render(<Basic contentClassName={contentClassName} />);
      const panel = screen.getByRole("tabpanel");

      expect(panel.className).toContain(contentClassName);
      for (const cls of RING_CLASSES) {
        expect(panel.className).toContain(cls);
      }
      unmount();
    }
  });
});
