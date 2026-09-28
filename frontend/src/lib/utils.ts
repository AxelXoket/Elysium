import { clsx, type ClassValue } from "clsx"
import { extendTailwindMerge } from "tailwind-merge"

import { TYPE_SCALE_STEPS } from "@/lib/theme/tokens"

/**
 * tailwind-merge with the app's type scale taught to it.
 *
 * `text-label`, `text-ui` and the other scale utilities are not in
 * tailwind-merge's stock vocabulary, and an unknown `text-*` value is filed
 * under text COLOUR there. Measured: `cn("text-label text-muted-foreground")`
 * came back as `text-muted-foreground` alone, the size silently dropped as
 * a "conflicting colour", and `cn("text-control", "text-label")` kept both,
 * so a caller could never override a primitive's base size (KURAL 3.6's
 * trap, one layer down). The scale steps are registered as font sizes so a
 * later size wins over an earlier one and a colour never displaces a size.
 */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [{ text: [...TYPE_SCALE_STEPS] }],
    },
  },
})

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
