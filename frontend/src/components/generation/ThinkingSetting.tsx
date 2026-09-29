import { useId, useState } from "react";
import { ChevronDown } from "lucide-react";
import type { Model } from "@/lib/schemas/models";
import { getReasoningMode } from "@/lib/models/modelHelpers";
import {
  THINKING_CHOICE_LABELS,
  THINKING_DEFAULT,
  THINKING_OFF,
  providerDefaultIsOff,
  resolveThinkingChoice,
  untouchedThinkingChoice,
  thinkingBudgetTooSmall,
  thinkingOptions,
  type ThinkingChoice,
} from "@/lib/models/thinking";
import { Button } from "@/components/ui/button";
import {
  MAX_NEW_TOKENS_LABEL,
  THINKING_EFFORT_LABEL,
} from "@/lib/generation/labels";
import { Collapse } from "@/components/motion/Collapse";

/** The row's label (the section is titled "Thinking", so the label says
 *  what is being picked) and the max output row's label, which the warning
 *  below names. Both live in lib/generation/labels.ts, so the error
 *  sentences name the same rows with the same words. */
export { MAX_NEW_TOKENS_LABEL, THINKING_EFFORT_LABEL };

/** What the row shows, as plain text, when there is nothing to pick. */
export const THINKING_MANDATORY_LABEL = "Always on";
export const THINKING_UNKNOWN_LABEL = "Provider decides";

/** One line under the row, per state. Exported so the tests import the
 *  sentences instead of retyping them. */
export const THINKING_OFF_NOTE =
  "Choose another option to let the model think first.";
export const THINKING_DEFAULT_NOTE =
  "The provider decides how much the model thinks.";
export const THINKING_DEFAULT_IS_OFF_NOTE =
  "For this model, Provider default means no thinking.";
export const THINKING_LEVEL_NOTE =
  "Higher levels think longer, and thinking is billed.";
export const THINKING_MANDATORY_NOTE =
  "This model always thinks and lists no levels to choose.";
export const THINKING_UNKNOWN_NOTE =
  "The model list does not say, so the provider decides.";
/** Budget-based models only: the thinking budget is taken out of max output. */
export const THINKING_BUDGET_WARNING = `Raise ${MAX_NEW_TOKENS_LABEL}: thinking may use the whole reply.`;

/** The details door and what it opens. */
export const THINKING_DETAILS_LABEL = "Details";
export const THINKING_DETAILS_SAVED = "Saved per model.";
/** Shown only where it is true: a model that always thinks and whose
 *  untouched choice is its lowest listed level. */
export const THINKING_DETAILS_UNTOUCHED =
  "Leave it untouched to use this model's lowest level.";
/** Changing effort drops the provider's cached prompt on some models, so
 *  the next reply pays for the whole prompt again. */
export const THINKING_DETAILS_CACHE =
  "Changing effort can make the next reply cost more on some models.";

/**
 * The thinking effort row. Offers only what the selected model lists, read
 * the way the server reads it: Off only where reasoning is optional,
 * Provider default on every model with a choice, then each listed level in
 * order. Untouched it shows what the request carries today, and a stored
 * word the model no longer lists shows that same value, which is what the
 * server sends.
 *
 * Hidden when the model lists no reasoning. With nothing to pick it shows
 * the state as plain text at full ink, still a tab stop, instead of a
 * dimmed disabled select a keyboard would skip.
 */
export function ThinkingSetting({
  model,
  stored,
  maxTokens,
  onChange,
}: {
  model: Model | undefined;
  stored: string | null;
  maxTokens: number;
  onChange: (choice: ThinkingChoice) => void;
}) {
  const noteId = useId();
  const warnId = useId();
  const detailsId = useId();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const mode = getReasoningMode(model);
  if (mode === "none") return null;
  const options = thinkingOptions(model);
  const current = resolveThinkingChoice(model, stored);
  const usable = options.length > 0 && current != null;

  let note = THINKING_LEVEL_NOTE;
  if (!usable) {
    note = mode === "mandatory" ? THINKING_MANDATORY_NOTE : THINKING_UNKNOWN_NOTE;
  } else if (current === THINKING_OFF) {
    note = THINKING_OFF_NOTE;
  } else if (current === THINKING_DEFAULT) {
    note = providerDefaultIsOff(model)
      ? THINKING_DEFAULT_IS_OFF_NOTE
      : THINKING_DEFAULT_NOTE;
  }
  const warn = usable && thinkingBudgetTooSmall(model, current, maxTokens);
  const describedBy = warn ? `${noteId} ${warnId}` : noteId;
  const untouched = untouchedThinkingChoice(model);
  const showUntouched =
    mode === "mandatory" && untouched != null && untouched !== THINKING_DEFAULT;

  return (
    <div className="generation-control" data-testid="thinking-setting">
      <div className="flex items-center justify-between gap-3">
        <label className="text-label font-semibold" htmlFor="thinking-effort">
          {THINKING_EFFORT_LABEL}
        </label>
        {usable ? (
          <select
            id="thinking-effort"
            // The Input component's own field classes, so the select reads
            // as one more box in the column of number boxes above it.
            className="sidebar-dialog-field generation-select rounded-lg border px-2 text-control outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
            value={current}
            aria-describedby={describedBy}
            onChange={(event) =>
              onChange(event.currentTarget.value as ThinkingChoice)
            }
          >
            {options.map((option) => (
              <option key={option} value={option}>
                {THINKING_CHOICE_LABELS[option]}
              </option>
            ))}
          </select>
        ) : (
          <output
            id="thinking-effort"
            tabIndex={0}
            aria-describedby={noteId}
            className="text-label font-semibold"
          >
            {mode === "mandatory" ? THINKING_MANDATORY_LABEL : THINKING_UNKNOWN_LABEL}
          </output>
        )}
      </div>
      <p id={noteId} className="generation-helper">
        {note}
      </p>
      {/* Mounted whatever it shows, so the line is announced when it
          appears rather than only found by a reader who goes looking. */}
      <div role="status">
        {warn && (
          <p id={warnId} className="generation-helper" data-testid="thinking-budget-warning">
            {THINKING_BUDGET_WARNING}
          </p>
        )}
      </div>
      {/* The house disclosure (a button with aria-expanded and
          aria-controls, a turning chevron, Collapse for the height), drawn
          with the dialog's own ghost button so it reads on this surface. */}
      <div className="mt-1 min-w-0" data-testid="thinking-details">
        <Button
          type="button"
          size="sm"
          variant="ghost"
          aria-expanded={detailsOpen}
          aria-controls={detailsId}
          onClick={() => setDetailsOpen((was) => !was)}
          className="sidebar-dialog-cancel h-7 gap-1.5 px-2 text-label"
        >
          <ChevronDown
            size={13}
            aria-hidden
            className={`shrink-0 transition-transform ${detailsOpen ? "rotate-180" : ""}`}
          />
          {THINKING_DETAILS_LABEL}
        </Button>
        <Collapse open={detailsOpen} className="min-w-0">
          <div id={detailsId}>
            <p className="generation-helper">{THINKING_DETAILS_SAVED}</p>
            {showUntouched && (
              <p className="generation-helper">{THINKING_DETAILS_UNTOUCHED}</p>
            )}
            <p className="generation-helper">{THINKING_DETAILS_CACHE}</p>
          </div>
        </Collapse>
      </div>
    </div>
  );
}
