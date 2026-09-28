/**
 * popupPositioning.ts - the distances every anchored popup keeps.
 *
 * They lived in `components/ui/select.tsx` and had to move: a file that
 * exports a component may export plain literals but not functions, or React
 * Fast Refresh loses the ability to hot-swap it, which the lint gate treats
 * as an error. Being out here is better anyway - the tooltip needs the same
 * numbers and was importing them from the select, so moving the select would
 * have broken the tooltip.
 */
import type { Select as SelectPrimitive } from "@base-ui/react/select";

/**
 * The gap a popup keeps from the edge of its collision boundary, in px.
 *
 * Base UI's own default is 5 and it is not enough inside the 300px right
 * panel: a centred popup wider than its trigger runs into the panel edge and
 * gets clamped against it. 8 is twice the popup's own step away from its
 * trigger, so the two distances read as one scale rather than two numbers.
 */
export const POPUP_COLLISION_PADDING = 8;

/**
 * Base UI's own collision padding, for comparison and nothing else.
 *
 * Written down because the number above is only defensible against it: "8"
 * on its own is a number somebody liked, "twice the side offset and more
 * than the library's default" is a decision. BoundaryPanel.test.tsx pins
 * the padding to T-75's 8 in one row and holds the side offset to it by
 * that relation, and it measures this one off Base UI's own positioner,
 * handed no padding, rather than taking the library's documentation for it.
 * The placement check every popup row runs refuses a popup placed with this
 * number: at it, a prop that arrived and a prop that was lost read the same.
 */
export const BASE_UI_COLLISION_PADDING = 5;

/** The popup's own step away from its trigger. The padding is twice this. */
export const POPUP_SIDE_OFFSET = 4;

/**
 * The positioning props that carry the collision padding, and the attribute
 * that lets a test see it, as ONE value.
 *
 * They were two JSX attributes written from the same local, on two different
 * elements: the real prop on the positioner, the attribute on the popup. A
 * test that read the attribute could not tell whether the positioner still
 * got the prop - deleting that one line left the attribute in place and the
 * assertion green, comparing a variable with itself. Returned together, the
 * prop cannot be dropped while the attribute goes on reporting it except by
 * changing this function. A change here is caught by a unit row that asserts
 * both keys come back. A positioner that stops spreading what it returns, or
 * writes its own padding after the spread, keeps the attribute and is
 * caught by the placement check instead, which reads the padding Base UI
 * really placed the popup with and does not depend on the attribute at all.
 * Every Select popup row runs that check, and so does a row that opens the
 * tooltip, which spreads the same value.
 *
 * That second reading is possible because of what Base UI does and does not
 * write. The padding itself goes to the positioning middleware and is never
 * written to the DOM; what comes back is the room the middleware left,
 * `--available-width` on the positioner, which is the width it clips against
 * with the padding taken off both sides. The tests recover the padding from
 * that, so the number a popup was placed with is read from the library's own
 * output rather than from anything this module says about it.
 *
 * The `data-` attribute SHIPS anyway, and that is a decision rather than an
 * oversight: it names the number the positioner was given without anybody
 * redoing that arithmetic, and the tests hold it to the recovered number so
 * it cannot report a padding the popup was not placed with. An attribute
 * added only under test would make the shipped path a different path from
 * the measured one, which is worth less than this costs - one data
 * attribute, no behaviour, no style, nothing to paint.
 */
export function popupCollisionProps(
  padding: SelectPrimitive.Positioner.Props["collisionPadding"] =
    POPUP_COLLISION_PADDING,
) {
  return {
    collisionPadding: padding,
    "data-collision-padding": padding,
  } as const;
}
