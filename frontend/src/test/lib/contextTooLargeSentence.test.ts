/**
 * U-33 - the refusal sentence has to point at something that helps.
 *
 * `context_too_large` is raised when `min_required` exceeds `available`.
 * `min_required` is the system block, the persona, the post-history
 * instruction, the voice block, the notebook and limits, and the message
 * being sent. It does NOT include the history, and `available` is derived
 * from the context budget.
 *
 * So the old sentence gave two suggestions and both were wrong: clearing
 * messages cannot move `min_required` by one character, and reducing the
 * context budget shrinks `available` and makes the same refusal arrive
 * sooner. A refusal that sends somebody in the wrong direction costs them
 * more than one that says nothing.
 */
import { describe, it, expect } from "vitest";

import { getErrorMessage } from "@/lib/errors/errorMessages";

// Through the public reader, not the table: what the user sees is what
// this function returns.
const sentence = getErrorMessage("context_too_large");

describe("the context_too_large sentence", () => {
  it("does not tell anyone to clear messages", () => {
    // The history is not in the sum this refusal is about.
    expect(sentence).not.toMatch(/clear(ing)? some messages/i);
  });

  it("does not tell anyone to reduce the context budget", () => {
    // `available` is derived FROM the budget: lowering it makes the refusal
    // arrive sooner.
    expect(sentence).not.toMatch(/reduc\w* the context budget/i);
    expect(sentence).not.toMatch(/lower\w* the context budget(?!.{0,40}worse)/i);
  });

  it("names the things that are actually counted", () => {
    // GROUND CONTROL: a sentence emptied of both wrong suggestions would
    // satisfy the two assertions above and say nothing at all.
    for (const part of [/character/i, /persona/i, /voice/i, /notes/i,
                        /limits/i]) {
      expect(sentence).toMatch(part);
    }
  });

  it("offers something that moves the number", () => {
    // Raising the budget, or making the fixed part smaller, or a model with
    // more room. At least one has to be there or the message is a dead end.
    expect(sentence).toMatch(/raise the context budget|larger window/i);
  });
});
