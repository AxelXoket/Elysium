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

const RING_CLASSES = [
  "focus-visible:ring-[3px]",
  "focus-visible:ring-ring/50",
  "focus-visible:outline-1",
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
    for (const cls of RING_CLASSES) {
      expect(panel.className).toContain(cls);
    }
    // Negative control: the tab strip sits right beside the panel and is not
    // the thing this defect is about. If the assertion above were vacuously
    // true for anything in the tree, this would also pass - it must not.
    const list = screen.getByRole("tablist");
    expect(list.className).not.toContain("focus-visible:ring-[3px]");
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
