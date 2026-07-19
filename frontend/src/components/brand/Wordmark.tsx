/**
 * Wordmark - "Elysium" set in an elegant high-contrast serif, tinted with an
 * Azure gradient (no gold, per the theme). Rendered as REAL text so it stays
 * selectable, accessible, and findable by its name; only the styling is
 * bespoke. Letter-spacing is pulled in slightly (the source sample sat a
 * touch loose).
 *
 * tone: "onDark" for the sidebar/vault surfaces (light gradient), "onLight"
 * for the chat canvas (deep gradient).
 */
interface WordmarkProps {
  /** Font size in px. */
  size?: number;
  tone?: "onDark" | "onLight";
  className?: string;
}

export function Wordmark({ size = 22, tone = "onDark", className }: WordmarkProps) {
  return (
    <span
      className={`elysium-wordmark elysium-wordmark-${tone}${className ? ` ${className}` : ""}`}
      style={{ fontSize: `${size}px` }}
    >
      Elysium
    </span>
  );
}
