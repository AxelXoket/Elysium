import {
  ChevronLeft,
  ChevronRight,
  MessagesSquare,
  SlidersHorizontal,
} from "lucide-react";

const LABELS = {
  left: { open: "Collapse sidebar", closed: "Expand sidebar" },
  right: { open: "Collapse right panel", closed: "Expand right panel" },
} as const;

/**
 * What the panel BEHIND this handle holds. Shown only while it is closed,
 * where a bare arrow would say "something opens here" and nothing about what.
 * The sidebar is chats and characters; the right panel is models, security,
 * persona and notes - one dial standing for the whole settings surface.
 */
const CONTENT_ICONS = {
  left: MessagesSquare,
  right: SlidersHorizontal,
} as const;

/**
 * PanelToggleButton - focus mode's collapse/expand handle for one side panel.
 *
 * Sits at the chat canvas' own edge, vertically centred. That strip is the one
 * part of the canvas nothing else claims: the jump indicator is bottom-centre,
 * the toast stack is top-centre, and message bubbles cap at 75% width.
 *
 * TWO SHAPES, TWO JOBS. While the panel is OPEN the handle is a quiet rounded
 * chevron pointing the way the panel will go - an affordance, not a
 * destination. Once the panel is CLOSED it is the only way back, so it becomes
 * a hard-edged square carrying the panel's own subject: an arrow at that point
 * would only say "something opens", never what.
 */
export function PanelToggleButton({
  side,
  collapsed,
  onToggle,
}: {
  side: "left" | "right";
  collapsed: boolean;
  onToggle: () => void;
}) {
  const label = collapsed ? LABELS[side].closed : LABELS[side].open;
  // Open: point toward the panel's own side, the way it will travel to close.
  const Icon = collapsed
    ? CONTENT_ICONS[side]
    : side === "left"
      ? ChevronLeft
      : ChevronRight;

  return (
    <button
      type="button"
      aria-label={label}
      aria-expanded={!collapsed}
      aria-controls={side === "left" ? "es-sidebar" : "es-right-panel"}
      title={label}
      onClick={onToggle}
      data-collapsed={collapsed}
      className={`panel-toggle panel-toggle-${side}`}
    >
      <Icon size={collapsed ? 18 : 16} strokeWidth={2} aria-hidden="true" />
    </button>
  );
}
