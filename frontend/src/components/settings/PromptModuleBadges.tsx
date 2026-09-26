/**
 * Notes on a module: where it behaves differently here than in the tool it
 * came from.
 *
 * The server sends stable keys (backend/prompt_modules/parsers.py BADGE_*);
 * this names the ones the page knows. A key it does not know is shown as the
 * key itself rather than hidden, because a note exists to say "this is not
 * what it was", and dropping one says the opposite.
 *
 * Plain hint lines, not pills: in this dialog a rounded chip is something to
 * click (the Delivery presets), and these are sentences to read. As text they
 * also wrap instead of being cut off at the dialog's edge.
 */
import { badgeLabel, type BadgePlacement } from "./promptModuleText";

export function ModuleBadges({
  badges,
  placement,
  className = "",
}: {
  badges: string[];
  placement?: BadgePlacement;
  /** Extra layout, e.g. an indent where the notes sit under a name in a list. */
  className?: string;
}) {
  if (badges.length === 0) return null;
  return (
    <ul className={`mt-0.5 ${className}`.trim()} aria-label="Differences from SillyTavern">
      {badges.map((code, index) => (
        <li key={`${code}-${index}`} className="settings-hint" data-badge={code}>
          {badgeLabel(code, placement)}
        </li>
      ))}
    </ul>
  );
}
