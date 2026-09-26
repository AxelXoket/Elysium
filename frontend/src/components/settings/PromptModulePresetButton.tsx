/**
 * "Turn on entries enabled in SillyTavern" for one imported preset (H3).
 *
 * Every imported entry starts off; this is the one click that brings back
 * the preset's own on/off state. The server applies it whole: if the set
 * does not fit the H4 budget, nothing is turned on, and the refusal names
 * the modules and the set's cost, which is shown right here.
 */
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, ToggleRight } from "lucide-react";

import { isApiError } from "@/lib/api/client";
import { isOverBudgetError } from "@/lib/api/promptModules";
import type {
  PromptModuleList,
  PromptModulesOverBudget,
} from "@/lib/schemas/promptModules";
import { getErrorMessage } from "@/lib/errors";

import { promptModuleKeys, useEnablePresetDefaults } from "./promptModuleQueries";
import { OverBudgetNotice } from "./PromptModuleOverBudget";

const LEFT_OUT_REASONS: Record<string, string> = {
  never_triggers: "it only fired on a kind of reply this app does not have",
  over_char_cap: "longer than one module may be",
  empty_content: "it has no text",
};

export function PresetDefaultsButton({
  importId,
  count,
}: {
  importId: number;
  count: number;
}) {
  const enable = useEnablePresetDefaults();
  const [overBudget, setOverBudget] = useState<PromptModulesOverBudget | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [leftOut, setLeftOut] = useState<string[]>([]);
  const qc = useQueryClient();

  const run = () => {
    setOverBudget(null);
    setError(null);
    setLeftOut([]);
    enable.mutate(importId, {
      // Entries SillyTavern had on that cannot be switched on here are not
      // part of the set; the server lists them and so does this (Ek O 1).
      onSuccess: (result) => {
        const rows = qc.getQueryData<PromptModuleList>(promptModuleKeys.list())?.modules ?? [];
        const nameOf = (id: number) => rows.find((r) => r.id === id)?.name ?? `Module ${id}`;
        setLeftOut(
          result.left_out.map(
            (entry) => `${nameOf(entry.id)} - ${LEFT_OUT_REASONS[entry.reason] ?? entry.reason}`,
          ),
        );
      },
      onError: (err) => {
        if (isOverBudgetError(err)) setOverBudget(err.overBudget);
        else setError(getErrorMessage(isApiError(err) ? err.detail : null));
      },
    });
  };

  return (
    <>
      <button
        type="button"
        className="settings-voice-button"
        disabled={enable.isPending}
        onClick={run}
      >
        {enable.isPending ? (
          <Loader2 size={13} className="animate-spin" />
        ) : (
          <ToggleRight size={13} />
        )}
        Turn on entries enabled in SillyTavern ({count})
      </button>
      {overBudget && <OverBudgetNotice overBudget={overBudget} whole />}
      {leftOut.length > 0 && (
        <ul className="settings-voice-issues" aria-label="Left off">
          <li>Left off, because they cannot be turned on here:</li>
          {leftOut.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
      {error && (
        <p className="settings-error" role="alert">
          {error}
        </p>
      )}
    </>
  );
}
