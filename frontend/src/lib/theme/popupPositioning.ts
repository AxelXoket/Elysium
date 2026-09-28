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
 * than the library's default" is a decision. The guard holds the three to
 * each other rather than retyping any of them.
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
 * only way to stop the positioner receiving the padding is to change this
 * function, which its own assertion catches.
 *
 * The `data-` attribute SHIPS, and that is a decision rather than an
 * oversight. Base UI keeps `collisionPadding` to itself: it goes to the
 * positioning middleware and is never written to the DOM, so the number a
 * popup was actually placed with is otherwise unreadable from outside the
 * library. An attribute added only under test would make the shipped path a
 * different path from the measured one, which is worth less than this costs -
 * one data attribute, no behaviour, no style, nothing to paint.
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
