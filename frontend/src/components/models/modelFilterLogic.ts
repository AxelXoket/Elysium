/**
 * The capability filter's logic, with no view attached.
 *
 * Split out of ModelFilters.tsx in K-38. These five exports say nothing about
 * rendering - they answer "which capabilities are worth offering", "does this
 * model satisfy the selection" and "how capable is it" - and a module that
 * exports both a component and unrelated values gives up Vite's fast refresh
 * for the whole file. The repository's standing rule after K-38: a value that
 * is part of a component's own API stays beside it (a cva variant table, a
 * context's own hook - all four of those carry a written eslint-disable);
 * logic that merely happens to be used by a component moves here.
 */
import type { Model } from "@/lib/schemas/models";

export type SortMode = "catalogue" | "capable";

export interface ModalityFilter {
  /** Modalities the model must READ. Empty means "do not care". */
  input: string[];
  /** Modalities the model must PRODUCE. Empty means "do not care". */
  output: string[];
}

export const EMPTY_FILTER: ModalityFilter = { input: [], output: [] };

/** Stable display order, so the row does not reshuffle between refreshes. */
const ORDER = ["image", "audio", "video"];

/**
 * Which capabilities are worth OFFERING, with how many models have each.
 *
 * Derived from the catalogue rather than hardcoded: an option nobody can
 * satisfy is a dead end, and the count is what turns the row from a guess into
 * a decision. "text" is excluded on both sides for the same reason the badge
 * drops it - every model has it, so filtering on it filters nothing.
 */
export function availableModalities(models: Model[] | undefined) {
  const input = new Map<string, number>();
  const output = new Map<string, number>();
  for (const m of models ?? []) {
    for (const k of new Set(m.input_modalities)) {
      if (k !== "text") input.set(k, (input.get(k) ?? 0) + 1);
    }
    for (const k of new Set(m.output_modalities)) {
      if (k !== "text") output.set(k, (output.get(k) ?? 0) + 1);
    }
  }
  const sort = (m: Map<string, number>) =>
    [...m.entries()].sort(
      (a, b) => ORDER.indexOf(a[0]) - ORDER.indexOf(b[0]) || a[0].localeCompare(b[0]),
    );
  return { input: sort(input), output: sort(output) };
}

/** Does this model satisfy every selected capability? */
export function matchesFilter(model: Model, filter: ModalityFilter): boolean {
  // AND across selections, on purpose: picking two capabilities means "I need
  // both", which is the question somebody looking for a model actually has.
  return (
    filter.input.every((k) => model.input_modalities.includes(k)) &&
    filter.output.every((k) => model.output_modalities.includes(k))
  );
}

/** How many non-text capabilities a model has, either direction. */
export function capabilityScore(model: Model): number {
  const inputs = model.input_modalities.filter((m) => m !== "text").length;
  const outputs = model.output_modalities.filter((m) => m !== "text").length;
  // Producing is scarcer than reading, so it breaks ties upward.
  return inputs + outputs * 2;
}

export function isFilterActive(filter: ModalityFilter): boolean {
  return filter.input.length > 0 || filter.output.length > 0;
}
