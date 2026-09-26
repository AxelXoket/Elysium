/**
 * The `prompt_modules_over_budget` refusal, by module name (H4).
 *
 * The context meter's general "too large" sentence never names modules, and
 * modules go to every chat, so a user told only "too large" has nowhere to
 * start. This names each module and what it costs, and says the two things
 * that fix it.
 */
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
    <ul
      className="settings-voice-issues"
      role="alert"
      data-testid="prompt-modules-over-budget"
    >
      <li>
        {whole
          ? "Nothing was turned on. Together these modules do not fit the context window:"
          : "These prompt modules do not fit the context window:"}
      </li>
      {overBudget.modules.map((module, index) => (
        <li key={`${module.id ?? "m"}-${index}`}>
          {module.name}, about {module.tokens} tokens
        </li>
      ))}
      {overBudget.total_tokens != null && (
        <li>About {overBudget.total_tokens} tokens in all.</li>
      )}
      <li>Turn a module off, or make the context window bigger.</li>
    </ul>
  );
}
