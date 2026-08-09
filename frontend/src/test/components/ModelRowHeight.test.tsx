/**
 * Every model row keeps the same shape, whatever badges it happens to carry.
 *
 * `.model-row-lazy` skips layout for off-screen rows and tells the browser to
 * assume `contain-intrinsic-size: auto 78px` until it can measure one. That
 * number was calibrated when every card carried at least a "Text" badge, so the
 * details row always took its line.
 *
 * Dropping the text badge (it said nothing - every model has text) let that row
 * collapse on plain models. Rows were then SHORTER than the estimate, so the
 * scrollbar promised more list than existed: scrolling down re-measured the
 * content shorter, scrollTop was clamped back, and the panel could not be
 * scrolled at all.
 *
 * jsdom has no layout, so height itself is unmeasurable here. What IS
 * checkable, and what actually broke, is that the row is always PRESENT with a
 * reserved minimum - the property the intrinsic-size estimate depends on.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { ModelCard } from "@/components/models/ModelCard";
import { modelFixture } from "@/test/mocks/fixtures";
import type { Model } from "@/lib/schemas/models";

function card(overrides: Partial<Model>) {
  const model = { ...modelFixture, ...overrides } as Model;
  render(<ModelCard model={model} />);
  const button = screen.getByRole("button", { name: /select model/i });
  const details = button.querySelector(".flex-wrap");
  expect(details, "the details row is gone entirely").not.toBeNull();
  return details as HTMLElement;
}

describe("model row geometry", () => {
  it("reserves the details line for a model with nothing to show", () => {
    const details = card({
      id: "v/plain",
      name: "Plain",
      context_length: null,
      max_completion_tokens: null,
      input_modalities: ["text"],
      output_modalities: ["text"],
    });
    expect(details.className).toContain("min-h-");
    expect(details.children.length).toBe(0);
  });

  it("keeps the same row when there is plenty to show", () => {
    const details = card({
      id: "v/rich",
      name: "Rich",
      context_length: 128000,
      max_completion_tokens: 4096,
      input_modalities: ["text", "image"],
      output_modalities: ["text", "image"],
    });
    expect(details.className).toContain("min-h-");
    // ctx, max, one input badge, one output badge.
    expect(details.children.length).toBe(4);
  });

  it("still says nothing about text, which is what freed the space", () => {
    const details = card({
      id: "v/vision",
      name: "Vision",
      context_length: null,
      max_completion_tokens: null,
      input_modalities: ["text", "image"],
      output_modalities: ["text"],
    });
    expect(details.textContent).not.toContain("Text");
    expect(details.textContent).toContain("Image");
  });
});
