/**
 * DialogSlotDuration.test.tsx - the dialog's two slots price their motion
 * with the token, not with a number.
 *
 * ui/dialog.tsx carried a bare numeric duration class on both the overlay
 * and the popup, a third duration regime beside the sheet's 90ms cap and the
 * hand-written menus' 180ms. The slots read `duration-(--duration-fast)`
 * now, which is Tailwind's spelling of `var(--duration-fast)`, so the class
 * and the sheet cannot disagree. Read off the rendered element rather than
 * the source text; the slot attribute is queried directly because the slot
 * IS the contract the stylesheet keys its entry/exit pair on.
 *
 * ui/select.tsx's popup reads the same token now (BoundaryPanel.test.tsx
 * holds it to that, T-86). The sheet-wide scan for numeric duration classes
 * is in test/lib/motionTokens.test.ts, which is also why this comment does
 * not spell one out: Tailwind reads every source file as text and would emit
 * the utility from the comment itself.
 */
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { tokens } from "@/lib/theme/tokens";
import { POPUP_SLOTS } from "@/test/helpers/compiledSheet";

/** `var(--duration-fast)` as the Tailwind utility spells it. */
function durationUtility(field: string): string {
  const m = /^var\((--[\w-]+)\)$/.exec(field);
  if (!m) throw new Error(`not a var() field: ${field}`);
  return `duration-(${m[1]})`;
}

describe("the dialog slots read --duration-fast", () => {
  it("neither the overlay nor the popup carries a numeric duration class", () => {
    render(
      <Dialog open>
        <DialogContent>
          <DialogTitle>Hello</DialogTitle>
        </DialogContent>
      </Dialog>,
    );
    const rendered = [...document.querySelectorAll("[data-slot]")]
      .map((el) => el.getAttribute("data-slot") ?? "")
      .filter((slot) => (POPUP_SLOTS as readonly string[]).includes(slot));

    // GROUND: the two slots the sheet prices for a dialog are on screen.
    expect(rendered.sort()).toEqual(["dialog-content", "dialog-overlay"]);

    for (const slot of rendered) {
      const el = document.querySelector(`[data-slot="${slot}"]`) as HTMLElement;
      const classes = [...el.classList];
      expect(
        classes.filter((c) => /^duration-\d+$/.test(c)),
        `${slot} still prices its motion with a number`,
      ).toEqual([]);
      expect(classes, `${slot} does not read the token`).toContain(
        durationUtility(tokens.duration.fast),
      );
    }
  });
});
