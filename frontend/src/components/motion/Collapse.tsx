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
 */
export function Collapse({ open, children, className }: CollapseProps) {
  return (
    <div className="es-collapse" data-open={open}>
      <div className={`es-collapse-inner${className ? ` ${className}` : ""}`}>
        {children}
      </div>
    </div>
  );
}
