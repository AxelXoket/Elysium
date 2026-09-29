/**
 * Bringing a module file in, and the report of what came out of it.
 *
 * The import button stays off until two things are true: a file is chosen,
 * and the consent switch under the owner's warning is on (9A.4 warning 1,
 * MT-10). The switch goes back off after every import, because the warning is about
 * THIS file going to every character on every message.
 *
 * A SillyTavern preset gets the third warning twice: before the import, as
 * soon as the chosen file reads as one, and at the top of its report. The
 * report lists everything that did not come across as it was (Ek O 1:
 * nothing is dropped in silence): skipped entries with the reason, badge
 * counts, macros left as they are, the order list used, preset fields that
 * are stored but not applied, and secrets removed before saving.
 */
import { useId, useRef, useState, type DragEvent, type ReactNode } from "react";
import { ChevronDown, Loader2, Upload } from "lucide-react";

import { getErrorMessage } from "@/lib/errors";
import { isApiError } from "@/lib/api/client";
import type {
  PresetField,
  PromptModuleImportResponse,
} from "@/lib/schemas/promptModules";

import { Collapse } from "@/components/motion/Collapse";
import { formatCount } from "@/lib/formatCount";

import { useImportPromptModule } from "./promptModuleQueries";
import {
  IMPORT_HINTS,
  PROMPT_MODULE_ACCEPT,
  PROMPT_MODULE_LIMITS,
  PROMPT_MODULE_WARNINGS,
  REGEX_UNSUPPORTED,
  ST_WARNING_KEY,
  badgeLabel,
  byteCapFor,
  isSillyTavernKind,
  placeholderName,
  presetFieldName,
  secretName,
  triggerList,
} from "./promptModuleText";
import { ModuleBadges } from "./PromptModuleBadges";
import { useHeldValue } from "./useHeldValue";

/**
 * Whether a chosen .json reads as a SillyTavern file rather than the app's
 * own plain format. Read in memory, never written anywhere. A file that is
 * not JSON at all gets no warning here; the server names that failure.
 */
async function looksLikeSillyTavern(file: File): Promise<boolean> {
  if (!file.name.toLowerCase().endsWith(".json")) return false;
  try {
    const parsed: unknown = JSON.parse(await file.text());
    if (typeof parsed !== "object" || parsed === null) return false;
    return (
      (parsed as { format?: unknown }).format !==
      PROMPT_MODULE_LIMITS.plain_json_format
    );
  } catch {
    return false;
  }
}

function sizeError(file: File): string | null {
  const cap = byteCapFor(file.name);
  if (cap == null) return "Only .txt, .md and .json files can be imported.";
  if (file.size > cap) {
    return `This file is ${formatCount(Math.ceil(file.size / 1024))} KB, over the ${formatCount(cap / 1024)} KB limit for this type.`;
  }
  return null;
}

/** The consent switch's own name; the owner's warning is its description. */
export const IMPORT_CONSENT_LABEL = "I understand";

export function PromptModuleImport() {
  const inputRef = useRef<HTMLInputElement>(null);
  const chooseRef = useRef<HTMLButtonElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [stFile, setStFile] = useState(false);
  const [consent, setConsent] = useState(false);
  const [report, setReport] = useState<PromptModuleImportResponse | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const importFile = useImportPromptModule();
  const hintId = useId();
  // A later pick can finish sniffing before an earlier one; only the newest
  // pick may set the warning.
  const pickSeq = useRef(0);

  const choose = (next: File | undefined) => {
    if (!next) return;
    const seq = ++pickSeq.current;
    setFile(next);
    setImportError(null);
    setFileError(sizeError(next));
    setStFile(false);
    void looksLikeSillyTavern(next).then((st) => {
      if (seq === pickSeq.current) setStFile(st);
    });
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    choose(event.dataTransfer.files?.[0]);
  };

  const canImport =
    file != null && fileError == null && consent && !importFile.isPending;

  // A disabled button never stays silent (KURAL 15.11): the line beside it
  // says what is missing, in the order the user meets the steps.
  const importHint =
    file == null
      ? IMPORT_HINTS.noFile
      : fileError != null
        ? IMPORT_HINTS.badFile
        : !consent
          ? IMPORT_HINTS.noConsent
          : IMPORT_HINTS.ready;

  const shownFileError = useHeldValue(fileError);
  const shownImportError = useHeldValue(importError);
  const shownReport = useHeldValue(report);

  const runImport = () => {
    if (!canImport || file == null) return;
    setImportError(null);
    importFile.mutate(file, {
      onSuccess: (result) => {
        setReport(result);
        setFile(null);
        setStFile(false);
        setConsent(false);
      },
      onError: (err) =>
        setImportError(getErrorMessage(isApiError(err) ? err.detail : null)),
    });
  };

  return (
    <div className="space-y-2">
      <input
        ref={inputRef}
        type="file"
        accept={PROMPT_MODULE_ACCEPT}
        className="hidden"
        aria-label="Choose a prompt module file"
        onChange={(event) => {
          choose(event.target.files?.[0]);
          event.target.value = "";
        }}
      />
      <div
        className="settings-voice-model"
        data-testid="prompt-module-drop"
        onDragOver={(event) => event.preventDefault()}
        onDrop={onDrop}
      >
        {/* The row and the two notices that open under it share one box, so
            a closed Collapse adds no gap inside the card. */}
        <div>
          <div className="settings-voice-row">
            <span className="min-w-0 flex-1 text-left">
              <span className="settings-label">
                {file ? file.name : "Import a module file"}
              </span>
              <span className="settings-category-desc">
                .txt, .md, a SillyTavern preset, or this app's module .json. Pick one or drop it here.
              </span>
            </span>
            <button
              ref={chooseRef}
              type="button"
              className="settings-voice-button is-quiet"
              data-prompt-module-choose=""
              onClick={() => inputRef.current?.click()}
            >
              {file ? "Change file" : "Choose file"}
            </button>
          </div>
          <Collapse open={fileError != null}>
            <div className="pt-2">
              {shownFileError && <p className="settings-error">{shownFileError}</p>}
            </div>
          </Collapse>
          <Collapse open={stFile}>
            <div className="pt-2">
              <p className="settings-voice-warning" data-testid="prompt-module-st-warning">
                {PROMPT_MODULE_WARNINGS.st_preset}
              </p>
            </div>
          </Collapse>
        </div>

        {/* The consent and the button it unlocks sit in the card's own
            params block, the way a Voice model card holds its switches: one
            hairline, flattened rows, no box inside the box.

            The app's own switch row, not an OS checkbox: the owner's warning
            is the row's text, and the switch beside it is the consent. */}
        <div className="settings-voice-params">
          <button
            type="button"
            role="switch"
            aria-checked={consent}
            aria-labelledby={`${hintId}-consent-label`}
            aria-describedby={`${hintId}-consent-text`}
            className="settings-toggle-row"
            onClick={() => setConsent(!consent)}
          >
            <span className="min-w-0 flex-1 text-left">
              <span className="settings-label" id={`${hintId}-consent-label`}>
                {IMPORT_CONSENT_LABEL}
              </span>
              <span
                className="settings-category-desc"
                id={`${hintId}-consent-text`}
                data-testid="prompt-module-consent-text"
              >
                {PROMPT_MODULE_WARNINGS.import_consent}
              </span>
            </span>
            <span className="settings-switch" data-on={consent ? "true" : "false"}>
              <span className="settings-switch-thumb" />
            </span>
          </button>

          <div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="settings-voice-button"
                disabled={!canImport}
                aria-describedby={hintId}
                onClick={runImport}
              >
                {importFile.isPending ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : (
                  <Upload size={12} />
                )}
                Import
              </button>
              <span className="settings-hint" id={hintId}>
                {importHint}
              </span>
            </div>
            <Collapse open={importError != null}>
              <div className="pt-2">
                {shownImportError && (
                  <p className="settings-error" role="alert">
                    {shownImportError}
                  </p>
                )}
              </div>
            </Collapse>
          </div>
        </div>
      </div>

      <Collapse open={report != null}>
        {shownReport && (
          <ImportReport
            result={shownReport}
            onClose={() => {
              setReport(null);
              // The report's own button is leaving; the focus goes to the
              // start of the next import rather than to the page.
              chooseRef.current?.focus();
            }}
          />
        )}
      </Collapse>
    </div>
  );
}

const ORDER_SOURCES: Record<string, string> = {
  "100001": "SillyTavern's global order (100001)",
  "100000": "the Prompt Manager default order (100000)",
  flat: "the export's own order list",
  prompts_array: "the order of the prompts list",
};

/** How a preset field was handled, in words. */
function presetFieldLine(field: PresetField): string {
  const name = presetFieldName(field.key);
  if (field.removed) return `${name}: removed before saving`;
  if (field.value_hidden) return `${name}: stored, value not shown, never applied`;
  if (field.regex_scripts != null) {
    const names = field.regex_names?.length ? ` (${field.regex_names.join(", ")})` : "";
    const scripts = field.regex_scripts === 1 ? "1 script" : `${formatCount(field.regex_scripts)} scripts`;
    return `${name}: ${scripts}${names}, stored, never run`;
  }
  if (field.value !== undefined) {
    return `${name} ${String(field.value)}: stored, not applied`;
  }
  if (field.chars != null) {
    return `${name}: ${formatCount(field.chars)} characters of text, stored, not applied`;
  }
  return `${name}: stored, not applied`;
}

/**
 * One report group, folded by default the way a Voice model's settings are:
 * a row with the heading and its count, the house disclosure chevron, and
 * the lines in a Collapse. A report of a big preset used to be one tall
 * card of undifferentiated lines; folded, it reads as a summary first.
 * Left out when empty.
 */
function ReportGroup({
  title,
  label,
  lines,
  explain,
  children,
}: {
  title: string;
  label: string;
  lines: string[];
  explain?: string;
  children?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  if (lines.length === 0) return null;
  const count = lines.length === 1 ? "1 entry" : `${formatCount(lines.length)} entries`;
  return (
    <div>
      <div className="settings-voice-row">
        <span className="min-w-0 flex-1 text-left">
          <span className="settings-label">{title}</span>
          <span className="settings-category-desc">{count}</span>
        </span>
        <button
          type="button"
          className="settings-voice-disclosure"
          aria-expanded={open}
          aria-controls={bodyId}
          aria-label={`${title}, ${count}`}
          onClick={() => setOpen(!open)}
        >
          <ChevronDown
            size={14}
            style={{ transform: open ? "rotate(180deg)" : undefined }}
          />
        </button>
      </div>
      {/* The report itself sits in an open Collapse, and the house rule that
          unclips an open Collapse (.es-collapse[data-open="true"]
          .es-collapse-inner) reaches every Collapse nested inside it too. A
          folded group would then paint its lines over the rows below it, so
          while folded this one clips itself. */}
      <Collapse open={open} className={open ? undefined : "overflow-hidden!"}>
        <div id={bodyId} className="space-y-1 pt-1">
          {explain && <p className="settings-hint">{explain}</p>}
          {children ?? (
            <ul className="space-y-1" aria-label={label}>
              {lines.map((line, index) => (
                <li key={`${index}-${line}`} className="settings-hint">
                  {line}
                </li>
              ))}
            </ul>
          )}
        </div>
      </Collapse>
    </div>
  );
}

/** Why an in-chat entry was never sent, by the server's key. */
const IN_CHAT_REASONS: Record<string, string> = {
  no_depth: "no depth",
  bad_role: "no known role",
  too_deep: "deeper than 10000",
};

function ImportReport({
  result,
  onClose,
}: {
  result: PromptModuleImportResponse;
  onClose: () => void;
}) {
  const { report } = result;
  const st =
    report.warnings.includes(ST_WARNING_KEY) ||
    isSillyTavernKind(report.source_type);
  const created = result.modules.filter((m) => m.kind === "module");

  const notSent = [
    ...report.markers.map(
      (id) => `${placeholderName(id)}: a SillyTavern placeholder with no text, kept to show where it went`,
    ),
    ...report.never_triggered.map(
      (entry) =>
        `${entry.identifier}: kept, but it only fires on ${triggerList(entry.triggers)}, which this app does not have`,
    ),
    ...report.order_ids_missing_from_prompts.map(
      (id) => `${id}: named in the order list, but not in the preset`,
    ),
    ...report.invalid_entries.map(
      (index) => `Entry ${formatCount(index + 1)}: not a prompt entry, left out`,
    ),
    ...report.duplicate_identifiers.map(
      (id) => `${id}: appears twice, both kept`,
    ),
    ...report.in_chat_not_sent.map(
      (entry) =>
        `${entry.identifier}: ${IN_CHAT_REASONS[entry.reason] ?? entry.reason}, so SillyTavern never sent it`,
    ),
  ];
  // What the preset asks for that this app does differently or not at all.
  const regex = report.regex_scripts;
  const differentLines = [
    ...(report.no_chat_history_marker
      ? ["No chat history placeholder: the chat is still sent here"]
      : []),
    ...report.role_sent_as_system.map(
      (id) => `${id}: unknown role, sent as a system message`,
    ),
    ...(report.global_variables_per_chat
      ? ["Global variables: kept per chat here, not across chats"]
      : []),
    ...report.trailing_assistant.map(
      (id) => `${id}: sent as a user message to models that refuse it`,
    ),
    ...(regex?.prompt_only ?? []).map(
      (name) => `${name}: runs on the sent chat when history cleanup is on`,
    ),
    ...(regex?.edits_stored_message ?? []).map(
      (name) => `${name}: would change saved messages, not applied`,
    ),
    ...(regex?.display_only ?? []).map(
      (name) => `${name}: display only, not applied`,
    ),
    ...(regex?.unsupported ?? []).map(
      (entry) => `${entry.name}: ${REGEX_UNSUPPORTED[entry.reason] ?? entry.reason}, skipped`,
    ),
  ];
  const badgeLines = Object.entries(report.badges).map(
    ([code, count]) => `${badgeLabel(code)}: ${formatCount(count)}`,
  );
  const macroLines = Object.entries(report.unprocessed_macros).map(
    ([macro, count]) => `${macro}: ${formatCount(count)}`,
  );
  const unknownLines = [
    ...report.unknown_entry_keys.map(
      (entry) => `${entry.identifier}: ${entry.keys.join(", ")}`,
    ),
    ...report.unknown_keys,
    ...report.sysprompt_unknown_keys.map((key) => `sysprompt.${key}`),
    ...(report.export_envelope?.unknown_keys ?? []),
  ];

  // The SillyTavern button lives on the preset's group in the list, and only
  // there: two copies at once each kept their own result, and the one the
  // user did not press could still say "nothing was turned on".
  return (
    <section
      className="settings-voice-model"
      aria-label="Import report"
      data-testid="prompt-module-report"
    >
      {st && (
        <p
          className="settings-voice-warning"
          data-testid="prompt-module-report-st-warning"
        >
          {PROMPT_MODULE_WARNINGS.st_preset}
        </p>
      )}

      {/* The summary, always open: what was made, and in what order. */}
      <div>
        <span className="settings-label" data-testid="prompt-module-report-created">
          {report.modules_created === 1
            ? "1 module created, off"
            : `${formatCount(report.modules_created)} modules created, all off`}
        </span>
        {report.order_source != null && (
          <span className="settings-hint" data-testid="prompt-module-order-source">
            Order taken from {ORDER_SOURCES[report.order_source] ?? report.order_source}.
            {report.per_character_orders_unused > 0 &&
              ` ${formatCount(report.per_character_orders_unused)} per-character order ${report.per_character_orders_unused === 1 ? "list was" : "lists were"} not used: modules are the same for every character.`}
          </span>
        )}
      </div>

      <ReportGroup
        title="Created modules"
        label="Created modules"
        lines={created.map((m) => m.name)}
      >
        <ul className="space-y-1.5" aria-label="Created modules">
          {created.map((module) => (
            <li key={module.id}>
              <span className="settings-hint font-semibold">{module.name}</span>
              <ModuleBadges badges={module.badges} placement={module} />
            </li>
          ))}
        </ul>
      </ReportGroup>
      <ReportGroup title="Not made into modules" label="Entries not made into modules" lines={notSent} />
      <ReportGroup title="Marked entries" label="Badge counts" lines={badgeLines} />
      <ReportGroup
        title="Macros left as they are"
        label="Unprocessed macros"
        explain="This app does not run these, so they go to the model as written."
        lines={macroLines}
      />

      <ReportGroup
        title="Handled differently here"
        label="Handled differently"
        explain="Preset parts this app runs differently, or skips."
        lines={differentLines}
      />

      <ReportGroup
        title="Stored, not applied here"
        label="Preset fields"
        explain="Settings from the preset that this app keeps with the import but does not use."
        lines={[
          ...report.unsupported_sections.map(
            (section) => `${section.name}: stored, not supported`,
          ),
          ...report.preset_fields.map(presetFieldLine),
        ]}
      />
      <ReportGroup
        title="Fields this app does not know"
        label="Unknown fields"
        explain="Kept with the import as they were. Nothing in this app reads them."
        lines={unknownLines}
      />
      <ReportGroup
        title="Removed before saving"
        label="Removed secrets"
        explain="Passwords, keys and server addresses are never stored."
        lines={report.secrets_removed.map(secretName)}
      />

      <div>
        <button
          type="button"
          className="settings-voice-button is-quiet"
          onClick={onClose}
        >
          Close report
        </button>
      </div>
    </section>
  );
}
