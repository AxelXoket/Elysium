/**
 * SwitchRow - a switch and the words that name it, as one control.
 *
 * The same row was written four times, two ways. Three wrapped the switch in
 * a `label` so the words on screen named it and clicking them flipped it; the
 * fourth wrapped it in a `div`, named it with an `aria-label` that said
 * something other than the words beside it, and clicking those words did
 * nothing. Voice control could not reach that one by what it says, and a
 * reader who clicked the sentence got no answer.
 *
 * So the wrapper is always a `label`, and the name always comes from the
 * text in it. `aria-label` is accepted only for a row that shows no words at
 * all; given alongside visible text it would OVERRIDE that text, which is the
 * defect this file exists to end, so the prop has no default and the four
 * call sites do not pass it.
 */
import type { ReactNode } from "react";

import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";

export interface SwitchRowProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
  /** The words that name the switch. Rendered inside the label. */
  children: ReactNode;
  /** Only for a row with no visible words. Never beside `children` text. */
  "aria-label"?: string;
  /** Extra classes on the wrapping label. */
  className?: string;
  /** Extra classes on the text span. */
  labelClassName?: string;
  /** An id for the control. Base UI puts it on the switch's hidden
   *  checkbox, not on the role="switch" element the reader sees: to hand
   *  the switch focus, find that element next to it (WorkerPanel does). */
  id?: string;
}

export function SwitchRow({
  checked,
  onCheckedChange,
  disabled,
  children,
  "aria-label": ariaLabel,
  className,
  labelClassName,
  id,
}: SwitchRowProps) {
  return (
    <label className={cn("flex items-center gap-2", className)}>
      <Switch
        checked={checked}
        disabled={disabled}
        onCheckedChange={onCheckedChange}
        aria-label={ariaLabel}
        id={id}
        // The primitive's own focus ring is a 50% halo that measured under
        // 2:1 on the light panels; this adds the house outline.
        className="persona-switch-ring"
      />
      <span
        className={cn(
          // The words name a control, so they take the chrome step (13)
          // rather than the label step: the role rule is above the
          // nearest-step rule, and the qualifier labels beside these
          // rows are on 13 for the same reason.
          "text-ui leading-relaxed text-muted-foreground",
          labelClassName,
        )}
      >
        {children}
      </span>
    </label>
  );
}
