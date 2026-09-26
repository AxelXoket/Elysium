/**
 * The `prompt_modules_over_budget` refusal, by module name (H4).
 *
 * The context meter's general "too large" sentence never names modules, and
 * modules go to every chat, so a user told only "too large" has nowhere to
 * start. This names each module and what it costs, and says the two things
 * that fix it.
 *
 * The alert role sits on a wrapper, not on the list: a role on the <ul>
 * replaces its list role and leaves the items with no list around them.
 */
import { OVER_BUDGET_FIX } from "@/lib/api/promptModules";
import { formatCount } from "@/lib/formatCount";
import type { PromptModulesOverBudget } from "@/lib/schemas/promptModules";

export function OverBudgetNotice({
  overBudget,
  whole = false,
}: {
  overBudget: PromptModulesOverBudget;
  /** A set refused whole: say that nothing was turned on. */
  whole?: boolean;
}) {
  return (
    <div role="alert" data-testid="prompt-modules-over-budget">
      <ul className="settings-voice-issues" aria-label="Modules over Context budget">
        <li>
          {whole
            ? "Nothing was turned on. Together these modules do not fit Context budget:"
            : "These prompt modules do not fit Context budget:"}
        </li>
        {overBudget.modules.map((module, index) => (
          <li key={`${module.id ?? "m"}-${index}`}>
            {module.name}, about {formatCount(module.tokens)} tokens
          </li>
        ))}
        {overBudget.total_tokens != null && (
          <li>About {formatCount(overBudget.total_tokens)} tokens in all.</li>
        )}
        <li>{OVER_BUDGET_FIX}</li>
      </ul>
    </div>
  );
}
