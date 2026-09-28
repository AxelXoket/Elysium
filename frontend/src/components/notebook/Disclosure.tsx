/**
 * Disclosure - a one-line door to the longer half of an explanation.
 *
 * The house disclosure shape (ActiveContextPreviewCard, ModelPanel's source,
 * VoiceSettingsPage's ParamGroup): a button that carries `aria-expanded` and
 * `aria-controls`, a chevron that turns, and `Collapse` doing the height.
 * Collapse is what makes the closed half `inert` and `aria-hidden`, so a
 * reader who has not opened it does not tab into it or hear it. The button
 * is the Notes tab's own ghost action at its 12 px icon size, and it stays
 * mounted whatever it shows, so the keyboard never loses it.
 *
 * Closed on every mount and never remembered: the short line above it is
 * the whole answer for most readers, and the rest is one press away.
 */
import { useId, useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Collapse } from "@/components/motion/Collapse";

export function Disclosure({
  label,
  children,
  testId,
  className,
}: {
  /** The button's words, which are also its accessible name. */
  label: string;
  children: ReactNode;
  testId?: string;
  /** Spacing from the line above, where the parent gives none (the door
   *  under a line in the same Collapse takes the tab's standard 4 px). */
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  return (
    <div className={`min-w-0 space-y-1${className ? ` ${className}` : ""}`} data-testid={testId}>
      {/* Its own block: the button's words are one line of their own, never
          run together with the first sentence of what it opens. */}
      <div>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((was) => !was)}
        className="persona-ghost-action h-7 gap-1.5 px-2 text-ui"
      >
        <ChevronDown
          size={12}
          aria-hidden
          className={`size-3 shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
        />
        {label}
      </Button>
      </div>
      <Collapse open={open} className="min-w-0">
        <div id={bodyId} className="space-y-1">
          {children}
        </div>
      </Collapse>
    </div>
  );
}
