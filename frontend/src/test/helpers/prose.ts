/**
 * prose.ts - find an imported sentence on screen without retyping it.
 *
 * Several short sentences often share one paragraph, so an exact-string
 * query cannot reach one of them. This turns the constant itself into a
 * literal pattern: the words on screen and the words in the test come from
 * the same export, and nothing is copied by hand.
 */
export function says(sentence: string): RegExp {
  return new RegExp(sentence.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
}
