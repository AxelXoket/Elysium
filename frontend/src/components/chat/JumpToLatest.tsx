import { ChevronDown } from "lucide-react";

/**
 * JumpToLatest - the down-arrow indicator (v1.1 A3 + I11).
 *
 * Rendering is a pure function of the canvas' single state machine:
 *   visible = !nearBottom && !programmaticScrollInProgress
 *   pulse   = one gentle restart per unseen-content revision (never a loop)
 *
 * The `key={pulseKey}` remount is what restarts the CSS animation - an
 * incrementing revision, not a boolean, so rapid content cannot get a pulse
 * "stuck" on. `dark` mirrors the chat-background darkness (H2: a
 * `.chat-bg-dark .jump-to-latest` descendant selector would be DEAD - this
 * button lives OUTSIDE the scroller that carries that class).
 */
export function JumpToLatest({
  visible,
  pulseKey,
  dark = false,
  onClick,
}: {
  visible: boolean;
  /** Increments once per new-unseen-content revision; restarts the pulse. */
  pulseKey: number;
  dark?: boolean;
  onClick: () => void;
}) {
  if (!visible) return null;
  return (
    <button
      key={pulseKey}
      type="button"
      aria-label="Jump to latest message"
      title="Jump to latest message"
      className={`jump-to-latest ${dark ? "is-dark" : ""}`}
      onClick={onClick}
    >
      <ChevronDown size={16} />
    </button>
  );
}
