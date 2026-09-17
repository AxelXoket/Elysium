/**
 * accessibility.ts - "gone for every reader", stated once.
 *
 * A block wrapped in `Collapse` stays in the DOM while it is closed (that is
 * how its height can animate shut), and is removed from the accessibility
 * tree instead: `inert` plus `aria-hidden` on the wrapper. A block wrapped in
 * `FadeIn` is simply not rendered. Both are "not there" to a person, and an
 * assertion written as `not.toBeInTheDocument()` can only see the second.
 * This is the one predicate that sees both, so the notebook tests ask their
 * absence question in the words the reader would: is it reachable, not is
 * it mounted.
 */
import { isInaccessible } from "@testing-library/react";

/** True when nothing is rendered, or what is rendered is hidden from the
 *  accessibility tree (`aria-hidden`, `hidden`, display or visibility). */
export function hiddenFromReaders(node: Element | null): boolean {
  return node === null || isInaccessible(node);
}

/** The text a reader can reach under `node`: `textContent` with every
 *  `aria-hidden="true"` subtree left out. A closed Collapse keeps its words
 *  in the DOM, so `textContent` alone reports sentences nobody is shown. */
export function readableText(node: Element): string {
  if (node.getAttribute("aria-hidden") === "true") return "";
  let out = "";
  for (const child of node.childNodes) {
    if (child.nodeType === Node.TEXT_NODE) out += child.textContent ?? "";
    else if (child.nodeType === Node.ELEMENT_NODE) out += readableText(child as Element);
  }
  return out;
}
