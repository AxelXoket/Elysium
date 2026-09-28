import type { ReactNode } from "react";

interface CollapseProps {
  /** When true the content animates open; when false it animates closed. */
  open: boolean;
  children: ReactNode;
  className?: string;
}

/**
 * Collapse - shared smooth open/close for every collapsible panel.
 *
 * Uses the grid-template-rows 0fr↔1fr technique (see .es-collapse in
 * index.css): the height animates without measuring, so content of any size
 * expands/collapses with one consistent short easing. Honors
 * prefers-reduced-motion (the transition is dropped there).
 *
 * Content stays mounted (so form state persists across toggles) but is
 * inert-height and clipped while closed.
 *
 * Clipped is not the same as gone, and that difference was a real bug (audit
 * KÖK 17): height and overflow say nothing to the tab order or to a screen
 * reader, so the two textareas in a CLOSED "Advanced" section of the character
 * dialogs still took focus. Somebody tabbing through the form typed into a
 * field they could not see, and that text was saved as the character's system
 * prompt. `inert` removes it from focus, hit-testing and the accessibility
 * tree in one attribute; `aria-hidden` covers browsers that do not have it
 * yet, which is the whole reason both are here.
 *
 * `className` lands on the INNER box, and that box is a grid item. A grid
 * item's automatic minimum width is its content's own width, so one child
 * that cannot wrap (a nowrap button, a select trigger) widens the whole
 * section past the card around it and everything in it lays out at that
 * width. `min-w-0` gives it a minimum of 0. Every Collapse in the right
 * panel's Notes tab passes it: that column is 250 px at the panel's 300 px
 * floor, and a button there once reached 274.
 */
export function Collapse({ open, children, className }: CollapseProps) {
  return (
    <div
      className="es-collapse"
      data-slot="collapse"
      data-open={open}
      inert={!open ? true : undefined}
      aria-hidden={!open || undefined}
    >
      <div className={`es-collapse-inner${className ? ` ${className}` : ""}`}>
        {children}
      </div>
    </div>
  );
}
