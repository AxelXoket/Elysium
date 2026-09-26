/**
 * Bringing a module file in, and the report of what came out of it.
 *
 * The import button stays off until two things are true: a file is chosen,
 * and the consent box under the owner's warning is ticked (9A.4 warning 1,
 * MT-10). The box clears after every import, because the warning is about
 * THIS file going to every character on every message.
 *
 * A SillyTavern preset gets the third warning twice: before the import, as
 * soon as the chosen file reads as one, and at the top of its report. The
 * report lists everything that did not come across as it was (Ek O 1:
 * nothing is dropped in silence): skipped entries with the reason, badge
 * counts, macros left as they are, the order list used, preset fields that
 * are stored but not applied, and secrets removed before saving.
 */
import { useRef, useState, type DragEvent } from "react";
import { Loader2, Upload } from "lucide-react";

import { getErrorMessage } from "@/lib/errors";
import { isApiError } from "@/lib/api/client";
import type {
  PresetField,
  PromptModuleImportResponse,
} from "@/lib/schemas/promptModules";

import { useImportPromptModule } from "./promptModuleQueries";
import {
  PROMPT_MODULE_ACCEPT,
  PROMPT_MODULE_LIMITS,
  PROMPT_MODULE_WARNINGS,
  ST_WARNING_KEY,
  badgeLabel,
  byteCapFor,
  isSillyTavernKind,
} from "./promptModuleText";
import { ModuleBadges } from "./PromptModuleBadges";
import { PresetDefaultsButton } from "./PromptModulePresetButton";

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
    return `This file is ${Math.ceil(file.size / 1024)} KB, over the ${cap / 1024} KB limit for this type.`;
  }
  return null;
}

export function PromptModuleImport() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [stFile, setStFile] = useState(false);
  const [consent, setConsent] = useState(false);
  const [report, setReport] = useState<PromptModuleImportResponse | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const importFile = useImportPromptModule();
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
        <div className="settings-voice-row">
          <span className="min-w-0 flex-1 text-left">
            <span className="settings-label">
              {file ? file.name : "Import a module file"}
            </span>
            <span className="settings-category-desc">
              .txt, .md, a SillyTavern preset, or this app's module .json - pick or drop it here
            </span>
          </span>
          <button
            type="button"
            className="settings-voice-button is-quiet"
            onClick={() => inputRef.current?.click()}
          >
            {file ? "Change file" : "Choose file"}
          </button>
        </div>

        {fileError && <p className="settings-error">{fileError}</p>}

        {stFile && (
          <p className="settings-voice-warning" data-testid="prompt-module-st-warning">
            {PROMPT_MODULE_WARNINGS.st_preset}
          </p>
        )}

        <label className="flex items-start gap-2">
          <input
            type="checkbox"
            className="mt-0.5 shrink-0"
            checked={consent}
            onChange={(event) => setConsent(event.currentTarget.checked)}
          />
          <span className="settings-hint" data-testid="prompt-module-consent-text">
            {PROMPT_MODULE_WARNINGS.import_consent}
          </span>
        </label>

        <div className="flex items-center gap-2">
          <button
            type="button"
            className="settings-voice-button"
            disabled={!canImport}
            onClick={runImport}
          >
            {importFile.isPending ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <Upload size={13} />
            )}
            Import
          </button>
          <span className="settings-hint">Every imported module starts off.</span>
        </div>

        {importError && (
          <p className="settings-error" role="alert">
            {importError}
          </p>
        )}
      </div>

      {report && (
        <ImportReport result={report} onClose={() => setReport(null)} />
      )}
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
  if (field.removed) return `${field.key} - removed before saving`;
  if (field.value_hidden) return `${field.key} - stored, value not shown, never applied`;
  if (field.regex_scripts != null) {
    const names = field.regex_names?.length ? ` (${field.regex_names.join(", ")})` : "";
    return `${field.key} - ${field.regex_scripts} regex scripts${names}, stored, never run`;
  }
  if (field.value !== undefined) {
    return `${field.key} = ${String(field.value)} - stored, not applied`;
  }
  if (field.chars != null) {
    return `${field.key} - ${field.chars} characters of text, stored, not applied`;
  }
  return `${field.key} - stored, not applied`;
}

/** One report section: a heading and its lines, left out when empty. */
function ReportList({
  title,
  label,
  lines,
}: {
  title: string;
  label: string;
  lines: string[];
}) {
  if (lines.length === 0) return null;
  return (
    <div>
      <span className="settings-label">{title}</span>
      <ul className="mt-1 space-y-1" aria-label={label}>
        {lines.map((line, index) => (
          <li key={`${index}-${line}`} className="settings-hint">
            {line}
          </li>
        ))}
      </ul>
    </div>
  );
}

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
  const stOn = created.filter((m) => m.st_enabled === true && m.can_enable);

  const notSent = [
    ...report.markers.map(
      (id) => `${id} - a SillyTavern placeholder with no text, kept as structure`,
    ),
    ...report.never_triggered.map(
      (entry) =>
        `${entry.identifier} - kept, but it only fires on ${entry.triggers.join(", ") || "generations"} this app does not have`,
    ),
    ...report.order_ids_missing_from_prompts.map(
      (id) => `${id} - named in the order list, missing from the preset`,
    ),
    ...report.invalid_entries.map(
      (index) => `Entry ${index + 1} - not a prompt entry, left out`,
    ),
    ...report.duplicate_identifiers.map(
      (id) => `${id} - appears twice, both kept`,
    ),
  ];
  const badgeLines = Object.entries(report.badges).map(
    ([code, count]) => `${badgeLabel(code)}: ${count}`,
  );
  const macroLines = Object.entries(report.unprocessed_macros).map(
    ([macro, count]) => `${macro}: ${count}`,
  );
  const unknownLines = [
    ...report.unknown_entry_keys.map(
      (entry) => `${entry.identifier}: ${entry.keys.join(", ")}`,
    ),
    ...report.unknown_keys,
    ...report.sysprompt_unknown_keys.map((key) => `sysprompt.${key}`),
    ...(report.export_envelope?.unknown_keys ?? []),
  ];

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

      <div>
        <span className="settings-label" data-testid="prompt-module-report-created">
          {report.modules_created === 1
            ? "1 module created, off"
            : `${report.modules_created} modules created, all off`}
        </span>
        {created.length > 0 && (
          <ul className="mt-1 space-y-1" aria-label="Created modules">
            {created.map((module) => (
              <li key={module.id} className="settings-hint">
                {module.name}
                <ModuleBadges badges={module.badges} placement={module} />
              </li>
            ))}
          </ul>
        )}
      </div>

      <ReportList title="Not made into modules" label="Entries not made into modules" lines={notSent} />
      <ReportList title="Marked entries" label="Badge counts" lines={badgeLines} />
      <ReportList title="Macros left as they are" label="Unprocessed macros" lines={macroLines} />

      {report.order_source != null && (
        <p className="settings-hint" data-testid="prompt-module-order-source">
          Order taken from {ORDER_SOURCES[report.order_source] ?? report.order_source}.
          {report.per_character_orders_unused > 0 &&
            ` ${report.per_character_orders_unused} per-character order lists were not used: modules are the same for every character.`}
        </p>
      )}

      <ReportList
        title="Stored, not applied here"
        label="Preset fields"
        lines={[
          ...report.unsupported_sections.map(
            (section) => `${section.name} - stored, not supported`,
          ),
          ...report.preset_fields.map(presetFieldLine),
        ]}
      />
      <ReportList title="Fields this app does not know" label="Unknown fields" lines={unknownLines} />
      <ReportList
        title="Removed before saving"
        label="Removed secrets"
        lines={report.secrets_removed}
      />

      <div className="flex flex-wrap items-center gap-2">
        {st && result.import_id != null && stOn.length > 0 && (
          <PresetDefaultsButton importId={result.import_id} count={stOn.length} />
        )}
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
