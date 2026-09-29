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

import { Collapse } from "@/components/motion/Collapse";

import { isApiError } from "@/lib/api/client";
import { isOverBudgetError } from "@/lib/api/promptModules";
import type {
  PromptModuleList,
  PromptModulesOverBudget,
} from "@/lib/schemas/promptModules";
import { getErrorMessage } from "@/lib/errors";

import {
  promptModuleKeys,
  useEnablePresetDefaults,
  useIsPromptModuleWriting,
  usePromptModuleWriting,
} from "./promptModuleQueries";
import { OverBudgetNotice } from "./PromptModuleOverBudget";
import { useHeldValue } from "./useHeldValue";

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
  const writing = usePromptModuleWriting();
  const writingNow = useIsPromptModuleWriting();

  const run = () => {
    if (writingNow()) return;
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
            (entry) => `${nameOf(entry.id)}: ${LEFT_OUT_REASONS[entry.reason] ?? entry.reason}`,
          ),
        );
      },
      onError: (err) => {
        if (isOverBudgetError(err)) setOverBudget(err.overBudget);
        else setError(getErrorMessage(isApiError(err) ? err.detail : null));
      },
    });
  };

  // Held so each notice can animate shut instead of vanishing in one frame.
  const shownOverBudget = useHeldValue(overBudget);
  const shownError = useHeldValue(error);
  const shownLeftOut = useHeldValue(leftOut.length > 0 ? leftOut : null);

  // One block: the button, then its notices under it. A closed Collapse
  // drops its stack margin (index.css .es-collapse), so it takes no room.
  return (
    <div className="space-y-2">
      {/* aria-disabled, not disabled, while a write settles: a disabled
          button under the keyboard drops the focus to the body. */}
      <button
        type="button"
        className="settings-voice-button"
        aria-disabled={writing || undefined}
        onClick={run}
      >
        {enable.isPending ? (
          <Loader2 size={12} className="animate-spin" />
        ) : (
          <ToggleRight size={12} />
        )}
        Turn on entries enabled in SillyTavern ({count})
      </button>
      <Collapse open={overBudget != null}>
        {shownOverBudget && <OverBudgetNotice overBudget={shownOverBudget} whole />}
      </Collapse>
      <Collapse open={leftOut.length > 0}>
        {shownLeftOut && (
          <ul className="settings-voice-issues" aria-label="Left off">
            <li>Left off, because they cannot be turned on here:</li>
            {shownLeftOut.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        )}
      </Collapse>
      <Collapse open={error != null}>
        {shownError && (
          <p className="settings-error" role="alert">
            {shownError}
          </p>
        )}
      </Collapse>
    </div>
  );
}
