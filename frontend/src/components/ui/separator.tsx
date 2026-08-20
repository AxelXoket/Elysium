"use client"

import { Separator as SeparatorPrimitive } from "@base-ui/react/separator"

import { cn } from "@/lib/utils"

function Separator({
  className,
  orientation = "horizontal",
  ...props
}: SeparatorPrimitive.Props) {
  return (
    <SeparatorPrimitive
      data-slot="separator"
      /* Base UI hardcodes role="separator" and has no `decorative` prop, so
         every one of these announced as an unnamed "separator" and added a
         stop in browse mode. All ten in this app divide sections that already
         carry a heading, which is how a screen reader user actually navigates
         them, so the rule was pure noise. elementProps merges to the right of
         the primitive's own props, so a caller that genuinely needs the role
         can still pass aria-hidden={false}. */
      aria-hidden="true"
      orientation={orientation}
      className={cn(
        "shrink-0 bg-border data-horizontal:h-px data-horizontal:w-full data-vertical:w-px data-vertical:self-stretch",
        className
      )}
      {...props}
    />
  )
}

export { Separator }
