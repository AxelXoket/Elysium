/**
 * CollapseFocusRing - an open section is not allowed to cut the ring off the
 * control a keyboard user is standing on.
 *
 * `.es-collapse-inner` clips, and it has to: the height animation is a grid
 * row growing from 0fr to 1fr, and without the clip the content just sits
 * there at full size while the row catches up. But the clip stayed on after
 * the section finished opening, and the global :focus-visible ring is drawn
 * OUTSIDE the box it clips - so every control inside a collapsible lost its
 * indicator. Two surfaces had already bought their way out of it one at a
 * time with inner padding; this is the same fix moved to the class that owns
 * the problem.
 *
 * The sheet is the COMPILED one, through the same helper the panel contrast
 * tests use, so what is measured is what the build emits rather than what the
 * source says. Every state below is rendered twice, open and closed, and the
 * two are each other's controls: a reader that returned the same answer for
 * both would prove nothing about either.
 */
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import { Collapse } from "@/components/motion/Collapse";
import { injectCss, loadGlassRightCss } from "@/test/helpers/glassSurfaceCss";

let sheet: HTMLStyleElement;
let css = "";

beforeAll(async () => {
  css = await loadGlassRightCss();
  sheet = injectCss(css);
}, 60_000);

afterAll(() => sheet.remove());

/** One real collapsible, wrapping the one surface that already carries a
 *  hand-rolled compensation for this defect, so the ground control below is
 *  measured on the element the compensation was written for. */
function section(open: boolean) {
  const { container } = render(
    <Collapse open={open}>
      <div className="settings-voice-params">
        <button type="button">Save</button>
      </div>
      <div>plain</div>
    </Collapse>,
  );
  return {
    outer: container.querySelector(".es-collapse") as HTMLElement,
    inner: container.querySelector(".es-collapse-inner") as HTMLElement,
    padded: container.querySelector(".settings-voice-params") as HTMLElement,
    plain: container.querySelector(
      ".es-collapse-inner > div:last-child",
    ) as HTMLElement,
    control: container.querySelector("button") as HTMLButtonElement,
  };
}

describe("a collapsible that is open", () => {
  it("compiled something worth reading", () => {
    // The watchman's watchman. Every assertion below is a computed value read
    // off this sheet; an empty build would make all of them vacuous.
    expect(css.length).toBeGreaterThan(1000);
  });

  it("is really open, and the closed twin is really closed", () => {
    // GROUND CONTROL for every state comparison in this file. If both renders
    // resolved to the same row template, "open" and "closed" would be two
    // names for one measurement and the overflow claims below would be empty.
    expect(getComputedStyle(section(true).outer).gridTemplateRows).toBe("1fr");
    expect(getComputedStyle(section(false).outer).gridTemplateRows).toBe("0fr");
  });

  it("stops clipping what is inside it", () => {
    // The defect. Read as the shorthand the stylesheet writes: jsdom does not
    // expand overflow into its longhands, so overflowX/overflowY answer for
    // the initial value and not for this rule.
    expect(getComputedStyle(section(true).inner).overflow).not.toBe("hidden");
    expect(getComputedStyle(section(true).inner).overflow).toBe("visible");
  });

  it("still clips while it is closed", () => {
    // The other half of the invariant, and the proof the height animation
    // survived: a fix that simply deleted the clip would pass the test above
    // and show the whole section flat on the screen behind a closed header.
    expect(getComputedStyle(section(false).inner).overflow).toBe("hidden");
  });

  it("holds the clip until the row has finished growing", () => {
    // Opening waits out the height animation before the flip, closing does
    // not wait at all. The duration is taken from the row's own transition
    // rather than retyped, so the two can never drift apart here.
    const rowTransition = getComputedStyle(section(true).outer).transition;
    const duration = rowTransition.split(/\s+/)[1];
    expect(duration, "the row transition no longer names a duration")
      .toMatch(/\S/);

    expect(getComputedStyle(section(true).inner).transition).toContain(
      duration,
    );
    expect(getComputedStyle(section(false).inner).transition).not.toContain(
      duration,
    );
  });
});

describe("the surfaces that had to compensate by hand", () => {
  it("keeps the inner room the voice params bought", () => {
    // GROUND CONTROL for the general fix: it is additive. The per-surface
    // patch is deliberately left in place, and if this edit had disturbed it
    // that surface would lose padding it has depended on since before the
    // shared class knew about focus rings.
    for (const open of [true, false]) {
      const { padded } = section(open);
      expect(getComputedStyle(padded).paddingBottom).not.toBe("");
      expect(getComputedStyle(padded).paddingBottom).not.toBe("0px");
    }
  });

  it("POSITIVE CONTROL: an unstyled sibling in the same section reads back differently", () => {
    // Without this the assertion above would pass on a reader that reports
    // the same padding for every element in the tree, which would say nothing
    // about the compensation surviving.
    const { padded, plain } = section(true);
    expect(getComputedStyle(padded).paddingBottom).not.toBe(
      getComputedStyle(plain).paddingBottom,
    );
  });
});

describe("the ring the clip was cutting", () => {
  it("POSITIVE CONTROL: the focus indicator is drawn outside the control's box", () => {
    // The whole claim rests on this. A ring painted INSIDE the border box
    // would never have been clipped by an ancestor, and "stop clipping" would
    // be a change with nothing behind it. The rule lives in @layer base,
    // which jsdom drops, so the compiled sheet is parsed the way
    // tabs-outline-utilities.test.tsx parses Tailwind's own utilities.
    // The lookbehind keeps this to the BARE selector: the sheet also carries
    // two dozen `&:focus-visible` and `.something:focus-visible` rules, and
    // the first of those answers a different question.
    const bodies = [
      ...css.matchAll(/(?<![-\w&.#[]):focus-visible\s*\{([^{}]*)\}/g),
    ].map((m) => m[1]);

    expect(bodies.length, "the compiled sheet has no global focus rule")
      .toBeGreaterThan(0);
    expect(
      bodies.some(
        (b) => /outline:/.test(b) && /outline-offset:\s*[1-9]/.test(b),
      ),
      "the global focus ring no longer sits outside the control's box",
    ).toBe(true);
  });

  it("puts a real, focusable control inside the section", () => {
    // POSITIVE CONTROL for the premise: the section under test actually
    // contains something that can take keyboard focus. A collapsible holding
    // only text has no ring to lose and could not have shown the defect.
    const { control } = section(true);
    control.focus();
    expect(document.activeElement).toBe(control);
  });
});
