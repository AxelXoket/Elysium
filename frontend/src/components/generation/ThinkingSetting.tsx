import type { ReasoningMode } from "@/lib/models/modelHelpers";
import { Button } from "../ui/button";

/**
 * Why the switch cannot be used with the selected model. Exported so the
 * tests import the sentences instead of retyping them.
 */
export const THINKING_MANDATORY_NOTE =
  "The selected model always thinks. Elysium asks it for the lowest effort and does not show the thinking, so this switch has nothing to turn on.";
export const THINKING_UNKNOWN_NOTE =
  "The model list does not say whether the selected model's thinking can be turned off, so Elysium leaves it to the provider and this switch has no effect.";
export const THINKING_ON_NOTE =
  "The model may think before it answers. Thinking uses reply tokens and is billed, even when the reply comes back short.";
export const THINKING_OFF_NOTE =
  "Off. Models that can skip thinking are asked to skip it.";

/** What the button says when the switch cannot change anything. Exported
 *  for the tests. */
export const THINKING_MANDATORY_LABEL = "Always on";
export const THINKING_UNKNOWN_LABEL = "Provider decides";

/** The switch's own label. The section around it is titled "Thinking", so
 *  the label says what the switch does instead of repeating the title, the
 *  way "Let replies include pictures" sits under "Pictures in replies". */
export const THINKING_SWITCH_LABEL = "Let the model think first";

/**
 * The Thinking switch (D3). Off by default: a model whose reasoning is
 * optional is asked to skip it, because reasoning tokens count against the
 * reply length and are billed even when the reply is empty. On, that model
 * is sent no reasoning field and the provider's default applies.
 *
 * Hidden when the selected model lists no reasoning at all, and disabled with
 * the reason when its reasoning is mandatory or its rules are unknown: in
 * both cases the request is the same whatever the switch says (the server
 * applies it only to optional reasoning). The disabled button says what
 * actually happens ("Always on", "Provider decides"), never "Off" beside a
 * sentence saying the model always thinks.
 */
export function ThinkingSetting({
  enabled,
  mode,
  onChange,
}: {
  enabled: boolean;
  mode: ReasoningMode;
  onChange: (enabled: boolean) => void;
}) {
  if (mode === "none") return null;
  const usable = mode === "optional";
  const on = usable ? enabled : mode === "mandatory";
  let note = enabled ? THINKING_ON_NOTE : THINKING_OFF_NOTE;
  let label = enabled ? "On" : "Off";
  if (mode === "mandatory") {
    note = THINKING_MANDATORY_NOTE;
    label = THINKING_MANDATORY_LABEL;
  }
  if (mode === "unknown") {
    note = THINKING_UNKNOWN_NOTE;
    label = THINKING_UNKNOWN_LABEL;
  }
  return (
    <div className="space-y-2" data-testid="thinking-setting">
      <div className="flex items-center justify-between gap-3">
        <label className="text-label font-semibold" htmlFor="thinking-toggle">
          {THINKING_SWITCH_LABEL}
        </label>
        <Button
          id="thinking-toggle"
          type="button"
          role="switch"
          aria-checked={on}
          size="sm"
          variant={usable && on ? "default" : "ghost"}
          // Not usable: the word is a state, not a greyed-out action, so it
          // keeps the full ink "Off" has in the Pictures row next to it.
          className={usable ? "text-label" : "text-label disabled:cursor-default disabled:opacity-100"}
          disabled={!usable}
          onClick={() => onChange(!enabled)}
        >
          {label}
        </Button>
      </div>
      <p className="generation-helper">{note}</p>
    </div>
  );
}
