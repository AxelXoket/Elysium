/**
 * Badges on a module: where it behaves differently here than in the tool it
 * came from.
 *
 * The server sends stable keys (backend/prompt_modules/parsers.py BADGE_*);
 * this names the ones the page knows. A key it does not know is shown as the
 * key itself rather than hidden, because a badge exists to say "this is not
 * what it was", and dropping one says the opposite.
 */
import { Badge } from "@/components/ui/badge";

import { badgeLabel, type BadgePlacement } from "./promptModuleText";

export function ModuleBadges({
  badges,
  placement,
}: {
  badges: string[];
  placement?: BadgePlacement;
}) {
  if (badges.length === 0) return null;
  return (
    <span className="mt-1 flex flex-wrap gap-1">
      {badges.map((code, index) => (
        <Badge
          key={`${code}-${index}`}
          variant="outline"
          className="font-normal"
          data-badge={code}
          style={{
            borderColor: "var(--color-es-glass-border-dark)",
            color: "var(--color-es-text-muted)",
          }}
        >
          {badgeLabel(code, placement)}
        </Badge>
      ))}
    </span>
  );
}
