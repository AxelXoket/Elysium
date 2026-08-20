import { ArrowDown, ArrowUp, X } from "lucide-react";

import type { Model } from "@/lib/schemas/models";
import {
  EMPTY_FILTER,
  availableModalities,
  isFilterActive,
  type ModalityFilter,
  type SortMode,
} from "./modelFilterLogic";

const labelMap: Record<string, string> = {
  image: "Image",
  audio: "Audio",
  video: "Video",
};

function Chip({
  label,
  count,
  active,
  direction,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  direction: "in" | "out";
  onClick: () => void;
}) {
  const Arrow = direction === "in" ? ArrowDown : ArrowUp;
  // The visible content is an arrow, a word and a number, which reads out as
  // "Image 23" and says nothing about direction. The label carries the whole
  // sentence so the control means the same thing to a screen reader as it does
  // on screen - which is the same gap the badge arrow closes on the cards.
  const described =
    direction === "in"
      ? `Only models that read ${label.toLowerCase()} (${count})`
      : `Only models that make ${label.toLowerCase()} (${count})`;
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={active}
      aria-label={described}
      onClick={onClick}
      title={described}
      className="flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] leading-none transition-colors"
      style={{
        backgroundColor: active ? "rgba(62, 114, 176, 0.16)" : "transparent",
        border: `1px solid ${
          active ? "rgba(62, 114, 176, 0.38)" : "var(--color-es-glass-border-dark)"
        }`,
        color: active
          ? "var(--color-es-primary-sage)"
          : "var(--color-es-text-muted)",
      }}
    >
      <Arrow size={10} style={{ opacity: 0.8 }} />
      {label}
      <span style={{ opacity: 0.6 }}>{count}</span>
    </button>
  );
}

/**
 * Narrow the catalogue by what a model can actually do.
 *
 * Sits under the search box and speaks the same language: search is "what is it
 * called", this is "what can it do". Both are client-side over the already
 * fetched list - no request, and no new backend surface: input_modalities and
 * output_modalities have been parsed and stored per model all along.
 *
 * The whole row hides itself when the catalogue offers no choices, so an
 * account that only sees plain text models is not given a control that cannot
 * do anything.
 */
export function ModelFilters({
  models,
  filter,
  onFilterChange,
  sort,
  onSortChange,
  matchCount,
}: {
  models: Model[] | undefined;
  filter: ModalityFilter;
  onFilterChange: (next: ModalityFilter) => void;
  sort: SortMode;
  onSortChange: (next: SortMode) => void;
  matchCount: number;
}) {
  const { input, output } = availableModalities(models);
  if (input.length === 0 && output.length === 0) return null;

  const toggle = (side: "input" | "output", key: string) => {
    const current = filter[side];
    onFilterChange({
      ...filter,
      [side]: current.includes(key)
        ? current.filter((k) => k !== key)
        : [...current, key],
    });
  };

  const active = isFilterActive(filter);

  return (
    <div
      className="flex flex-col gap-2 rounded-xl px-3 py-2"
      style={{
        backgroundColor: "rgba(255,255,255,0.05)",
        border: "1px solid var(--color-es-glass-border-dark)",
      }}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <span
          /* No opacity multiplier. The sidebar's heading idiom dampens this
             token to 0.75, and copying that here landed the label at 2.8 to
             3.2:1, under the 4.5:1 floor that 11px text needs (the bold
             exemption starts at 18.66px, so semibold buys nothing). At full
             strength the token reads about 4.9 to 5.05:1 where this row
             sits. The sidebar instances have the same defect on their own
             surface and are NOT fixed here; that is a separate change. */
          className="text-[11px] font-semibold uppercase tracking-widest"
          style={{ color: "var(--color-es-text-muted)" }}
        >
          Can
        </span>
        {input.map(([key, count]) => (
          <Chip
            key={`in-${key}`}
            label={labelMap[key] ?? key}
            count={count}
            active={filter.input.includes(key)}
            direction="in"
            onClick={() => toggle("input", key)}
          />
        ))}
        {output.map(([key, count]) => (
          <Chip
            key={`out-${key}`}
            label={labelMap[key] ?? key}
            count={count}
            active={filter.output.includes(key)}
            direction="out"
            onClick={() => toggle("output", key)}
          />
        ))}
        {active && (
          <button
            type="button"
            onClick={() => onFilterChange(EMPTY_FILTER)}
            aria-label="Clear capability filters"
            className="ml-auto flex shrink-0 items-center gap-1 text-[10px] opacity-60 transition-opacity hover:opacity-100"
            style={{ color: "var(--color-es-text-muted)" }}
          >
            <X size={10} />
            Clear
          </button>
        )}
      </div>

      <div className="flex items-center justify-between gap-2">
        <span
          className="text-[10px]"
          style={{ color: "var(--color-es-text-muted)" }}
        >
          {active
            ? `${matchCount} ${matchCount === 1 ? "model" : "models"}`
            : " "}
        </span>
        <button
          type="button"
          role="switch"
          aria-checked={sort === "capable"}
          onClick={() =>
            onSortChange(sort === "capable" ? "catalogue" : "capable")
          }
          title={
            sort === "capable"
              ? "Showing the most capable models first"
              : "Showing the catalogue's own order"
          }
          className="rounded-lg px-2 py-1 text-[10px] leading-none transition-colors"
          style={{
            backgroundColor:
              sort === "capable" ? "rgba(62, 114, 176, 0.16)" : "transparent",
            border: `1px solid ${
              sort === "capable"
                ? "rgba(62, 114, 176, 0.38)"
                : "var(--color-es-glass-border-dark)"
            }`,
            color:
              sort === "capable"
                ? "var(--color-es-primary-sage)"
                : "var(--color-es-text-muted)",
          }}
        >
          Most capable first
        </button>
      </div>
    </div>
  );
}
