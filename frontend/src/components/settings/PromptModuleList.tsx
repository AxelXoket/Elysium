/**
 * The module list: one switch per module, and rename, move and delete.
 *
 * Modules are global: a module that is on goes to every
 * character on every message, in the order shown here. Plain files go at the
 * top of the system message; a SillyTavern preset's entries go where the
 * preset puts them (before or after the chat, or inside it at a depth, each
 * with its own role). The entries of one SillyTavern preset stay together as a
 * group under the file's name, with the preset's placeholders (chat history
 * and the like) shown as plain lines so the "went after the chat history"
 * note has something to point at, and with the one button that turns on what
 * SillyTavern had on.
 *
 * Each row carries one visible action besides its switch, the same as a
 * Voice row: a quiet "More" button. Move, rename and delete are rare, so
 * they open under the row in a Collapse rather than sitting on every card as
 * four equal icons.
 *
 * Turning a module on can be refused (H4, `prompt_modules_over_budget`); the
 * refusal is shown above the list by module name, and the switch goes back
 * to what the vault holds because the list is refetched after every write.
 * Delete asks first, in place of the action buttons, with the focus on the
 * safe answer (the house destructive-confirm contract, NotebookPanel).
 */
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronDown,
  Pencil,
  Trash2,
  X,
} from "lucide-react";

import { Collapse } from "@/components/motion/Collapse";
import { FadeIn } from "@/components/motion/FadeIn";
import { isApiError } from "@/lib/api/client";
import { isOverBudgetError } from "@/lib/api/promptModules";
import { formatCount } from "@/lib/formatCount";
import type {
  PromptModule,
  PromptModuleImportRow,
  PromptModulesOverBudget,
} from "@/lib/schemas/promptModules";
import { getErrorMessage } from "@/lib/errors";

import { ModuleBadges } from "./PromptModuleBadges";
import { OverBudgetNotice } from "./PromptModuleOverBudget";
import { PresetDefaultsButton } from "./PromptModulePresetButton";
import { charsToTokens, moduleChars } from "./promptModuleCost";
import {
  CLEAR_VARIABLES_DONE,
  CLEAR_VARIABLES_HINT,
  CLEAR_VARIABLES_LABEL,
  HISTORY_CLEANUP_HINT,
  HISTORY_CLEANUP_LABEL,
  NOT_IN_ORDER_NOTE,
  PROMPT_MODULE_LIMITS,
  placeholderLine,
} from "./promptModuleText";
import { useUiStore } from "@/lib/store/uiStore";

import {
  useClearChatVariables,
  useDeletePromptModule,
  useLatestPromptModule,
  usePatchPromptModule,
  useIsPromptModuleWriting,
  usePromptModuleWriting,
  usePatchPromptModuleImport,
} from "./promptModuleQueries";
import { useHeldValue } from "./useHeldValue";

const BLOCKED: Record<string, string> = {
  never_triggers:
    "Cannot be turned on: in SillyTavern it only fired on a kind of reply this app does not have.",
  over_char_cap: "Cannot be turned on: longer than one module may be.",
  empty_content: "Cannot be turned on: it has no text.",
};
/** An in-chat entry with no depth or role: never sent, in either app. */
const BLOCKED_IN_CHAT = "Cannot be turned on: SillyTavern never sent it.";

function blockedLine(module: PromptModule): string | null {
  if (module.badges.includes("in_chat_not_sent")) return BLOCKED_IN_CHAT;
  return module.blocked_reason ? (BLOCKED[module.blocked_reason] ?? null) : null;
}

function stStateLine(module: PromptModule): string | null {
  if (!module.source_type.startsWith("st_")) return null;
  if (module.st_enabled === true) return "SillyTavern: on";
  if (module.st_enabled === false) return "SillyTavern: off";
  return NOT_IN_ORDER_NOTE;
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

/**
 * Forget the open chat's SillyTavern macro variables. The way out when a
 * preset's variables grew until its modules no longer fit; shown only while
 * a chat is open and a SillyTavern module is in the list.
 */
export function ChatVariablesRow({ modules }: { modules: PromptModule[] }) {
  const chatId = useUiStore((s) => s.selectedChatId);
  const clear = useClearChatVariables();
  const [error, setError] = useState<string | null>(null);
  const shownError = useHeldValue(error);
  const id = useId();
  const hasPreset = modules.some(
    (m) => m.kind === "module" && m.source_type.startsWith("st_"),
  );
  if (chatId == null || !hasPreset) return null;
  return (
    <div>
      <div className="settings-voice-row">
        <span className="min-w-0 flex-1 text-left">
          <span className="settings-label" id={`${id}-label`}>
            {CLEAR_VARIABLES_LABEL}
          </span>
          <span className="settings-category-desc" id={`${id}-hint`}>
            {clear.isSuccess ? CLEAR_VARIABLES_DONE : CLEAR_VARIABLES_HINT}
          </span>
        </span>
        <button
          type="button"
          className="settings-voice-button is-quiet"
          aria-labelledby={`${id}-label`}
          aria-describedby={`${id}-hint`}
          disabled={clear.isPending}
          onClick={() => {
            setError(null);
            clear.mutate(chatId, {
              onError: (err) =>
                setError(getErrorMessage(isApiError(err) ? err.detail : null)),
            });
          }}
        >
          <Trash2 size={13} />
          Clear
        </button>
      </div>
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

/**
 * History cleanup for one preset: its prompt-only regex scripts run on the
 * copy of the chat that is sent. Off until turned on; the saved messages
 * never change. The switch row is the Roleplay layer's.
 */
function HistoryCleanupSwitch({ imp }: { imp: PromptModuleImportRow }) {
  const save = usePatchPromptModuleImport();
  const [error, setError] = useState<string | null>(null);
  const shownError = useHeldValue(error);
  const id = useId();
  const on = imp.history_cleanup;
  return (
    <div>
      <button
        type="button"
        className="settings-toggle-row"
        role="switch"
        aria-checked={on}
        aria-labelledby={`${id}-label`}
        aria-describedby={`${id}-hint`}
        disabled={save.isPending}
        data-history-cleanup={imp.id}
        onClick={() => {
          setError(null);
          save.mutate(
            { id: imp.id, historyCleanup: !on },
            {
              onError: (err) =>
                setError(getErrorMessage(isApiError(err) ? err.detail : null)),
            },
          );
        }}
      >
        <span className="min-w-0 flex-1 text-left">
          <span className="settings-label" id={`${id}-label`}>
            {HISTORY_CLEANUP_LABEL}
          </span>
          <span className="settings-category-desc" id={`${id}-hint`}>
            {HISTORY_CLEANUP_HINT}
          </span>
        </span>
        <span className="settings-switch" data-on={on ? "true" : "false"}>
          <span className="settings-switch-thumb" />
        </span>
      </button>
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

export function PromptModuleList({
  modules,
  imports = [],
}: {
  modules: PromptModule[];
  imports?: PromptModuleImportRow[];
}) {
  const patch = usePatchPromptModule();
  const latest = useLatestPromptModule();
  const writing = usePromptModuleWriting();
  const writingNow = useIsPromptModuleWriting();
  const [overBudget, setOverBudget] = useState<PromptModulesOverBudget | null>(null);
  const [error, setError] = useState<string | null>(null);
  const shownOverBudget = useHeldValue(overBudget);
  const shownError = useHeldValue(error);

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

  // The next value comes from the cache, not from the row this render drew:
  // the two can differ for a moment after a write, and a click in that
  // moment must not send the same intent again. Nothing goes out while a
  // write is still settling, and nothing for a row the list no longer has.
  const toggle = (row: PromptModule) => {
    if (writingNow()) return;
    const current = latest(row.id);
    if (!current) return;
    write(row.id, { enabled: !current.enabled });
  };

  // A move is a list write like a toggle, with the same two checks.
  const move = (row: PromptModule, to: number) => {
    if (writingNow() || !latest(row.id)) return;
    write(row.id, { sort_order: to });
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
      <Collapse open={overBudget != null}>
        {shownOverBudget && <OverBudgetNotice overBudget={shownOverBudget} />}
      </Collapse>
      <Collapse open={error != null}>
        {shownError && (
          <p className="settings-error" role="alert">
            {shownError}
          </p>
        )}
      </Collapse>
      <ul className="space-y-3" aria-label="Prompt modules">
        {blocks(rows).map((block) => {
          const head = block[0];
          const preset = block.length > 1 || head.source_type.startsWith("st_");
          const stOn = block.filter(
            (m) => m.kind === "module" && m.st_enabled === true && m.can_enable,
          );
          const source = head.source_filename ?? "a SillyTavern file";
          const imp = imports.find((i) => i.id === head.import_id);
          return (
            <li
              key={`${head.import_id ?? "m"}-${head.id}`}
              className="space-y-1.5"
              role={preset ? "group" : undefined}
              aria-label={preset ? `From ${source}` : undefined}
            >
              {preset && (
                <div className="space-y-1.5">
                  <span className="settings-label">From {source}</span>
                  {head.import_id != null && stOn.length > 0 && (
                    <PresetDefaultsButton importId={head.import_id} count={stOn.length} />
                  )}
                  {imp && imp.cleanup_scripts > 0 && <HistoryCleanupSwitch imp={imp} />}
                </div>
              )}
              <ul className="space-y-1.5">
                {block.map((module) => (
                  <li key={module.id}>
                    {module.kind === "module" ? (
                      <ModuleRow
                        module={module}
                        position={rows.indexOf(module)}
                        last={rows.length - 1}
                        busy={writing}
                        onWrite={write}
                        onToggle={toggle}
                        onMove={move}
                      />
                    ) : (
                      <div className="settings-voice-row border-dashed">
                        <p className="settings-hint" data-testid="prompt-module-marker">
                          {placeholderLine(module.name)}
                        </p>
                      </div>
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

type Pane = "actions" | "rename" | "confirm";

function ModuleRow({
  module,
  position,
  last,
  busy,
  onWrite,
  onToggle,
  onMove,
}: {
  module: PromptModule;
  position: number;
  last: number;
  busy: boolean;
  onWrite: (id: number, body: { enabled?: boolean; sort_order?: number; name?: string }) => void;
  onToggle: (module: PromptModule) => void;
  onMove: (module: PromptModule, to: number) => void;
}) {
  const remove = useDeletePromptModule();
  const [open, setOpen] = useState(false);
  const [pane, setPane] = useState<Pane>("actions");
  const [draft, setDraft] = useState(module.name);
  const [rowError, setRowError] = useState<string | null>(null);
  const shownRowError = useHeldValue(rowError);
  const actionsId = useId();

  const moreRef = useRef<HTMLButtonElement>(null);
  const renameRef = useRef<HTMLButtonElement>(null);
  const deleteRef = useRef<HTMLButtonElement>(null);
  const keepRef = useRef<HTMLButtonElement>(null);
  // Where the focus goes back to when a pane closes. Null on first render,
  // so no row grabs the focus just by mounting.
  const returnTo = useRef<"rename" | "delete" | "more" | null>(null);

  useEffect(() => {
    if (pane === "confirm") {
      keepRef.current?.focus();
      return;
    }
    if (pane === "actions" && returnTo.current) {
      const target =
        returnTo.current === "rename"
          ? renameRef.current
          : returnTo.current === "delete"
            ? deleteRef.current
            : moreRef.current;
      returnTo.current = null;
      target?.focus();
    }
  }, [pane, open]);

  const renaming = pane === "rename";
  const tokens = charsToTokens(moduleChars(module.content));
  const stLine = stStateLine(module);
  const blocked = !module.can_enable && !module.enabled;
  const trimmed = draft.trim();
  const draftValid =
    trimmed.length > 0 &&
    draft.length <= PROMPT_MODULE_LIMITS.module_name_max_chars;

  const back = (to: "rename" | "delete") => {
    returnTo.current = to;
    setPane("actions");
  };

  const startRename = () => {
    setDraft(module.name);
    setPane("rename");
  };

  const cancelRename = () => {
    setDraft(module.name);
    back("rename");
  };

  const saveName = () => {
    if (!draftValid) return;
    if (draft !== module.name) onWrite(module.id, { name: draft });
    back("rename");
  };

  const rowRef = useRef<HTMLDivElement>(null);

  const runDelete = () => {
    setRowError(null);
    remove.mutate(module.id, {
      // This row is about to leave the list, and the focus with it. It goes
      // to the next module's switch, else the one before, else the import
      // button, so a keyboard user is never dropped to the top of the page.
      onSuccess: () => {
        const page = rowRef.current?.closest("[data-prompt-modules-page]");
        const switches = Array.from(
          page?.querySelectorAll<HTMLElement>("[data-module-switch]") ?? [],
        );
        const here = switches.findIndex((el) => rowRef.current?.contains(el));
        const target =
          switches[here + 1] ??
          switches[here - 1] ??
          page?.querySelector<HTMLElement>("[data-prompt-module-choose]");
        target?.focus();
      },
      onError: (err) => {
        setRowError(getErrorMessage(isApiError(err) ? err.detail : null));
        back("delete");
      },
    });
  };

  const toggleOpen = () => {
    if (open) {
      // Closing the actions also drops a half-done rename or delete.
      setDraft(module.name);
      setPane("actions");
    }
    setOpen(!open);
  };

  // Escape answers the innermost open thing and stops there (KURAL 8.5): the
  // delete question, then the rename, then the action row itself. Stopped so
  // it does not also close the Settings dialog, or reach the Composer's
  // window-level "Escape stops generating".
  const onActionsKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Escape") return;
    event.stopPropagation();
    event.preventDefault();
    if (pane === "confirm") back("delete");
    else if (pane === "rename") cancelRename();
    else {
      returnTo.current = "more";
      setOpen(false);
    }
  };

  return (
    <div
      ref={rowRef}
      className="settings-voice-model"
      data-testid={`prompt-module-${module.id}`}
      data-selected={module.enabled ? "true" : undefined}
    >
      {/* The row and what opens under it share one box with no gap, so a
          closed Collapse leaves no stray space inside the card. */}
      <div>
        <div className="settings-voice-row">
          <span className="min-w-0 flex-1 text-left">
            {pane === "rename" ? (
              <FadeIn key="edit">
                <input
                  className="settings-voice-input"
                  aria-label={`New name for ${module.name}`}
                  value={draft}
                  maxLength={PROMPT_MODULE_LIMITS.module_name_max_chars}
                  autoFocus
                  onChange={(event) => setDraft(event.currentTarget.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      // The keydown closes the box and hands the focus back to
                      // Rename; without this the same Enter's keypress then
                      // lands on Rename and opens the box again.
                      event.preventDefault();
                      saveName();
                    }
                    if (event.key === "Escape") {
                      event.stopPropagation();
                      event.preventDefault();
                      cancelRename();
                    }
                  }}
                />
              </FadeIn>
            ) : (
              <span className="settings-label break-words">{module.name}</span>
            )}
            <span className="settings-category-desc">
              About {formatCount(tokens)} tokens
            </span>
            {stLine && <span className="settings-category-desc">{stLine}</span>}
          </span>

          {/* The switch ends the row, as every other switch on this page and on
              the Voice page does. While the name is being edited it and the
              disclosure step out of the tab order, so the first Tab after
              typing reaches Save name, not a control that drops the edit. */}
          {/* While a write settles the switch is aria-disabled, not
              disabled: a disabled button under the keyboard drops the focus
              to the body, and the next Space would go nowhere. */}
          <button
            type="button"
            role="switch"
            aria-checked={module.enabled}
            aria-label={module.name}
            aria-describedby={blocked ? `${actionsId}-blocked` : undefined}
            disabled={blocked || renaming}
            aria-disabled={busy || undefined}
            data-module-switch=""
            className="shrink-0 disabled:cursor-default aria-disabled:cursor-default"
            onClick={() => onToggle(module)}
          >
            {/* A switch that cannot be turned on is dimmed the way a disabled
                section is, and its reason sits under the row. */}
            <span
              className={`settings-switch${blocked ? " settings-section-disabled" : ""}`}
              data-on={module.enabled ? "true" : "false"}
            >
              <span className="settings-switch-thumb" />
            </span>
          </button>

          <button
            ref={moreRef}
            type="button"
            className="settings-voice-disclosure"
            aria-label={`More actions for ${module.name}`}
            aria-expanded={open}
            aria-controls={actionsId}
            disabled={renaming}
            onClick={toggleOpen}
          >
            <ChevronDown
              size={14}
              style={{ transform: open ? "rotate(180deg)" : undefined }}
            />
          </button>
        </div>

        <Collapse open={open}>
          <div
            id={actionsId}
            role="group"
            aria-label={`Actions for ${module.name}`}
            className="pt-2"
            onKeyDown={onActionsKeyDown}
          >
            {pane === "actions" && (
              <FadeIn key="actions" className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  className="settings-voice-button is-quiet"
                  aria-label={`Move ${module.name} up`}
                  disabled={position <= 0}
                  aria-disabled={busy || undefined}
                  onClick={() => onMove(module, position - 1)}
                >
                  <ArrowUp size={12} />
                  Up
                </button>
                <button
                  type="button"
                  className="settings-voice-button is-quiet"
                  aria-label={`Move ${module.name} down`}
                  disabled={position >= last}
                  aria-disabled={busy || undefined}
                  onClick={() => onMove(module, position + 1)}
                >
                  <ArrowDown size={12} />
                  Down
                </button>
                <button
                  ref={renameRef}
                  type="button"
                  className="settings-voice-button is-quiet"
                  aria-label={`Rename ${module.name}`}
                  onClick={startRename}
                >
                  <Pencil size={12} />
                  Rename
                </button>
                <button
                  ref={deleteRef}
                  type="button"
                  className="settings-voice-button is-quiet"
                  aria-label={`Delete ${module.name}`}
                  disabled={remove.isPending}
                  onClick={() => setPane("confirm")}
                >
                  <Trash2 size={12} />
                  Delete
                </button>
              </FadeIn>
            )}
            {pane === "rename" && (
              <FadeIn key="rename" className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  className="settings-voice-button"
                  disabled={!draftValid}
                  onClick={saveName}
                >
                  <Check size={12} />
                  Save name
                </button>
                <button
                  type="button"
                  className="settings-voice-button is-quiet"
                  onClick={cancelRename}
                >
                  <X size={12} />
                  Cancel
                </button>
              </FadeIn>
            )}
            {pane === "confirm" && (
              <FadeIn key="confirm">
                <div
                  className="flex flex-wrap items-center gap-2"
                  role="group"
                  aria-label="Confirm delete"
                >
                  <span className="settings-hint">
                    Delete this module? Its text leaves the vault.
                  </span>
                  <button
                    ref={keepRef}
                    type="button"
                    className="settings-voice-button is-quiet"
                    disabled={remove.isPending}
                    onClick={() => back("delete")}
                  >
                    Keep
                  </button>
                  <button
                    type="button"
                    className="settings-voice-button is-danger"
                    disabled={remove.isPending}
                    onClick={runDelete}
                  >
                    {remove.isPending ? "Deleting…" : "Delete"}
                  </button>
                </div>
              </FadeIn>
            )}
          </div>
        </Collapse>

        <Collapse open={rowError != null}>
          <div className="pt-2">
            {shownRowError && (
              <p className="settings-error" role="alert">
                {shownRowError}
              </p>
            )}
          </div>
        </Collapse>
      </div>

      {module.note && <span className="settings-hint">{module.note}</span>}

      <ModuleBadges badges={module.badges} placement={module} />

      {blocked && blockedLine(module) && (
        <span className="settings-hint" id={`${actionsId}-blocked`}>
          {blockedLine(module)}
        </span>
      )}
    </div>
  );
}
