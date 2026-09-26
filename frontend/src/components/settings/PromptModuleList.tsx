/**
 * The module list: one switch per module, and rename, move and delete.
 *
 * Modules are global (owner's rule): a module that is on goes to every
 * character on every message, in the order shown here, at the very top of the
 * system message. The entries of one SillyTavern preset stay together as a
 * block, with the preset's placeholders (chat history and the like) shown as
 * plain lines so the "went after the chat history" badge has something to
 * point at, and with the one button that turns on what SillyTavern had on.
 *
 * Turning a module on can be refused (H4, `prompt_modules_over_budget`); the
 * refusal is shown above the list by module name, and the switch goes back
 * to what the vault holds because the list is refetched after every write.
 * Delete asks first, in the row itself.
 */
import { useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  Check,
  Loader2,
  Pencil,
  Trash2,
  X,
} from "lucide-react";

import { isApiError } from "@/lib/api/client";
import { isOverBudgetError } from "@/lib/api/promptModules";
import type {
  PromptModule,
  PromptModulesOverBudget,
} from "@/lib/schemas/promptModules";
import { getErrorMessage } from "@/lib/errors";

import { ModuleBadges } from "./PromptModuleBadges";
import { OverBudgetNotice } from "./PromptModuleOverBudget";
import { PresetDefaultsButton } from "./PromptModulePresetButton";
import { charsToTokens, moduleChars } from "./promptModuleCost";
import { PROMPT_MODULE_LIMITS } from "./promptModuleText";
import {
  useDeletePromptModule,
  usePatchPromptModule,
} from "./promptModuleQueries";

const BLOCKED: Record<string, string> = {
  never_triggers:
    "Cannot be turned on: in SillyTavern it only fired on a kind of reply this app does not have.",
  over_char_cap: "Cannot be turned on: longer than one module may be.",
  empty_content: "Cannot be turned on: it has no text.",
};

function stStateLine(module: PromptModule): string | null {
  if (!module.source_type.startsWith("st_")) return null;
  if (module.st_enabled === true) return "On in SillyTavern";
  if (module.st_enabled === false) return "Off in SillyTavern";
  return "Not in the SillyTavern order";
}

/** Consecutive rows of one import form a block; everything else is alone. */
function blocks(rows: PromptModule[]): PromptModule[][] {
  const out: PromptModule[][] = [];
  for (const row of rows) {
    const last = out[out.length - 1];
    if (
      last &&
      row.import_id != null &&
      last[0].import_id === row.import_id &&
      row.source_type.startsWith("st_")
    ) {
      last.push(row);
    } else {
      out.push([row]);
    }
  }
  return out;
}

export function PromptModuleList({ modules }: { modules: PromptModule[] }) {
  const patch = usePatchPromptModule();
  const [overBudget, setOverBudget] = useState<PromptModulesOverBudget | null>(null);
  const [error, setError] = useState<string | null>(null);

  const rows = [...modules].sort((a, b) => a.sort_order - b.sort_order || a.id - b.id);

  const write = (id: number, body: Parameters<typeof patch.mutate>[0]["patch"]) => {
    setOverBudget(null);
    setError(null);
    patch.mutate(
      { id, patch: body },
      {
        onError: (err) => {
          if (isOverBudgetError(err)) setOverBudget(err.overBudget);
          else setError(getErrorMessage(isApiError(err) ? err.detail : null));
        },
      },
    );
  };

  if (rows.every((row) => row.kind !== "module")) {
    return (
      <p className="settings-hint" data-testid="prompt-module-empty">
        No modules yet. Import a file above; nothing is sent until you turn a
        module on.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {overBudget && <OverBudgetNotice overBudget={overBudget} />}
      {error && (
        <p className="settings-error" role="alert">
          {error}
        </p>
      )}
      <ul className="space-y-2" aria-label="Prompt modules">
        {blocks(rows).map((block) => {
          const head = block[0];
          const preset = block.length > 1 || head.source_type.startsWith("st_");
          const stOn = block.filter(
            (m) => m.kind === "module" && m.st_enabled === true && m.can_enable,
          );
          return (
            <li key={`${head.import_id ?? "m"}-${head.id}`} className="space-y-1">
              {preset && (
                <div className="flex flex-wrap items-center gap-2">
                  <span className="settings-hint">
                    From {head.source_filename ?? "a SillyTavern file"}
                  </span>
                  {head.import_id != null && stOn.length > 0 && (
                    <PresetDefaultsButton importId={head.import_id} count={stOn.length} />
                  )}
                </div>
              )}
              <ul className="space-y-1">
                {block.map((module) => (
                  <li key={module.id}>
                    {module.kind === "module" ? (
                      <ModuleRow
                        module={module}
                        position={rows.indexOf(module)}
                        last={rows.length - 1}
                        busy={patch.isPending}
                        onWrite={write}
                      />
                    ) : (
                      <span className="settings-hint" data-testid="prompt-module-marker">
                        Placeholder: {module.name}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ModuleRow({
  module,
  position,
  last,
  busy,
  onWrite,
}: {
  module: PromptModule;
  position: number;
  last: number;
  busy: boolean;
  onWrite: (id: number, body: { enabled?: boolean; sort_order?: number; name?: string }) => void;
}) {
  const remove = useDeletePromptModule();
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(module.name);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [rowError, setRowError] = useState<string | null>(null);

  const tokens = charsToTokens(moduleChars(module.content));
  const stLine = stStateLine(module);
  const blocked = !module.can_enable && !module.enabled;
  const trimmed = draft.trim();
  const draftValid =
    trimmed.length > 0 &&
    draft.length <= PROMPT_MODULE_LIMITS.module_name_max_chars;

  const saveName = () => {
    if (!draftValid) return;
    if (draft !== module.name) onWrite(module.id, { name: draft });
    setRenaming(false);
  };

  const runDelete = () => {
    setRowError(null);
    remove.mutate(module.id, {
      onError: (err) =>
        setRowError(getErrorMessage(isApiError(err) ? err.detail : null)),
    });
  };

  return (
    <div
      className="settings-voice-model"
      data-testid={`prompt-module-${module.id}`}
      data-selected={module.enabled ? "true" : undefined}
    >
      <div className="settings-voice-row">
        <button
          type="button"
          role="switch"
          aria-checked={module.enabled}
          aria-label={module.name}
          disabled={busy || blocked}
          className="shrink-0"
          onClick={() => onWrite(module.id, { enabled: !module.enabled })}
        >
          <span className="settings-switch" data-on={module.enabled ? "true" : "false"}>
            <span className="settings-switch-thumb" />
          </span>
        </button>

        <span className="min-w-0 flex-1 text-left">
          {renaming ? (
            <input
              className="settings-voice-input"
              aria-label={`New name for ${module.name}`}
              value={draft}
              maxLength={PROMPT_MODULE_LIMITS.module_name_max_chars}
              autoFocus
              onChange={(event) => setDraft(event.currentTarget.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") saveName();
                if (event.key === "Escape") {
                  setDraft(module.name);
                  setRenaming(false);
                }
              }}
            />
          ) : (
            <span className="settings-label break-words">
              {module.name}
            </span>
          )}
          <span className="settings-category-desc">
            {`About ${tokens} tokens`}
            {stLine ? ` - ${stLine}` : ""}
          </span>
        </span>

        {renaming ? (
          <>
            <button
              type="button"
              className="settings-voice-button is-quiet"
              aria-label="Save name"
              disabled={!draftValid}
              onClick={saveName}
            >
              <Check size={13} />
            </button>
            <button
              type="button"
              className="settings-voice-button is-quiet"
              aria-label="Cancel rename"
              onClick={() => {
                setDraft(module.name);
                setRenaming(false);
              }}
            >
              <X size={13} />
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className="settings-voice-button is-quiet"
              aria-label={`Move ${module.name} up`}
              disabled={busy || position <= 0}
              onClick={() => onWrite(module.id, { sort_order: position - 1 })}
            >
              <ArrowUp size={13} />
            </button>
            <button
              type="button"
              className="settings-voice-button is-quiet"
              aria-label={`Move ${module.name} down`}
              disabled={busy || position >= last}
              onClick={() => onWrite(module.id, { sort_order: position + 1 })}
            >
              <ArrowDown size={13} />
            </button>
            <button
              type="button"
              className="settings-voice-button is-quiet"
              aria-label={`Rename ${module.name}`}
              onClick={() => {
                setDraft(module.name);
                setRenaming(true);
              }}
            >
              <Pencil size={13} />
            </button>
            <button
              type="button"
              className="settings-voice-button is-quiet"
              aria-label={`Delete ${module.name}`}
              disabled={remove.isPending}
              onClick={() => setConfirmDelete(true)}
              style={{ color: "var(--color-es-danger)" }}
            >
              <Trash2 size={13} />
            </button>
          </>
        )}
      </div>

      {module.note && <span className="settings-hint">{module.note}</span>}

      <ModuleBadges badges={module.badges} placement={module} />

      {blocked && module.blocked_reason && BLOCKED[module.blocked_reason] && (
        <span className="settings-hint">{BLOCKED[module.blocked_reason]}</span>
      )}
      {confirmDelete && (
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Confirm delete">
          <span className="settings-hint">
            Delete this module? Its text leaves the vault.
          </span>
          <button
            type="button"
            className="settings-voice-button is-quiet"
            disabled={remove.isPending}
            onClick={runDelete}
            style={{ color: "var(--color-es-danger)" }}
          >
            {remove.isPending ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <Trash2 size={13} />
            )}
            Delete
          </button>
          <button
            type="button"
            className="settings-voice-button is-quiet"
            disabled={remove.isPending}
            onClick={() => setConfirmDelete(false)}
          >
            Cancel
          </button>
        </div>
      )}
      {rowError && (
        <p className="settings-error" role="alert">
          {rowError}
        </p>
      )}
    </div>
  );
}
