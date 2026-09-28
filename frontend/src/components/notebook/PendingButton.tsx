/**
 * PendingButton - a Button that stays focusable while its request is out.
 *
 * `disabled` on the button the keyboard is standing on drops focus to the
 * page's body (measured in Edge on every row action of the Notes
 * tab). While `pending`, this one is `aria-disabled` instead: it keeps
 * focus, says it is unavailable, and ignores presses itself. A control that
 * is off for a REASON (an empty draft) still uses `disabled`.
 */
import type { ComponentProps } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function PendingButton({
  pending,
  onClick,
  className,
  ...rest
}: ComponentProps<typeof Button> & { pending?: boolean }) {
  return (
    <Button
      {...rest}
      aria-disabled={pending || rest["aria-disabled"] || undefined}
      data-pending={pending || undefined}
      className={cn(className, pending && "cursor-wait opacity-50")}
      onClick={(e) => {
        if (pending) {
          e.preventDefault();
          return;
        }
        onClick?.(e);
      }}
    />
  );
}

