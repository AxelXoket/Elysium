import { useMemo, useState, type ReactNode } from "react";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  CONTEXT_BUDGET_MIN,
  MAX_STOP_SEQUENCES,
  SEED_MAX,
  SEED_MIN,
  clampNumber,
  getContextBudgetUiMax,
  getMaxTokensUiMax,
  getModelAwareGenerationDefaults,
  useGenerationSettings,
  type GenerationSettingsValues,
} from "./GenerationSettingsContext";
import { isParamSupportedByModel } from "@/lib/generation";
import type { Model } from "@/lib/schemas/models";
import { SlidersHorizontal, X } from "lucide-react";

type NumericSettingKey = Exclude<keyof GenerationSettingsValues, "seed">;
type SupportedGenerationKey =
  | "temperature"
  | "top_p"
  | "top_k"
  | "repetition_penalty"
  | "max_tokens"
  | "seed"
  | "stop";

interface GenerationSettingsDialogProps {
  selectedModel: Model | undefined;
}

const SUPPORT_KEYS: SupportedGenerationKey[] = [
  "temperature",
  "top_p",
  "top_k",
  "repetition_penalty",
  "max_tokens",
  "seed",
  "stop",
];

export function GenerationSettingsDialog({
  selectedModel,
}: GenerationSettingsDialogProps) {
  const [open, setOpen] = useState(false);
  const { settings, setSetting, stopSequences, setStopSequences, resetAll } =
    useGenerationSettings();
  const maxTokensMax = getMaxTokensUiMax(selectedModel);
  const contextBudgetMax = getContextBudgetUiMax(selectedModel);
  const supportKnown = (selectedModel?.supported_parameters.length ?? 0) > 0;

  const displayedSettings = useMemo(
    () => ({
      ...settings,
      max_tokens: clampNumber(settings.max_tokens, 1, maxTokensMax),
      context_budget_tokens: clampNumber(
        settings.context_budget_tokens,
        CONTEXT_BUDGET_MIN,
        contextBudgetMax,
      ),
    }),
    [contextBudgetMax, maxTokensMax, settings],
  );

  const defaults = useMemo(
    () => getModelAwareGenerationDefaults(selectedModel),
    [selectedModel],
  );

  const isCustom =
    displayedSettings.temperature !== defaults.temperature ||
    displayedSettings.top_p !== defaults.top_p ||
    displayedSettings.top_k !== defaults.top_k ||
    displayedSettings.repetition_penalty !== defaults.repetition_penalty ||
    displayedSettings.max_tokens !== defaults.max_tokens ||
    displayedSettings.context_budget_tokens !== defaults.context_budget_tokens ||
    displayedSettings.seed.trim().length > 0 ||
    stopSequences.length > 0;

  const supportedCount = supportKnown
    ? SUPPORT_KEYS.filter((key) => isParamSupportedByModel(key, selectedModel))
        .length
    : null;

  const setNumeric = (key: NumericSettingKey, value: number) => {
    setSetting(key, value);
  };

  const setSeed = (rawValue: string) => {
    if (rawValue === "" || /^-?\d*$/.test(rawValue)) {
      setSetting("seed", rawValue);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <button
            type="button"
            className="generation-trigger w-full rounded-xl px-3 py-3 text-left"
            data-testid="generation-settings-trigger"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <span className="flex items-center gap-2 text-xs font-semibold">
                  <SlidersHorizontal size={13} />
                  Generation Settings
                </span>
                <span className="mt-1 block truncate text-[11px]">
                  {isCustom ? "Custom sampling and context" : "Default sampling and context"}
                </span>
              </div>
              <span className="generation-trigger-pill">
                {supportKnown ? `${supportedCount}/${SUPPORT_KEYS.length}` : "unknown"}
              </span>
            </div>
            {!supportKnown && (
              <p className="generation-support-note mt-2">
                Parameter support is unknown for this model.
              </p>
            )}
          </button>
        }
      />

      <DialogContent className="glass-dialog sidebar-dialog generation-dialog max-h-[88vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle
            className="flex items-center gap-2 text-base font-semibold"
            style={{ color: "var(--color-es-text-light)" }}
          >
            <SlidersHorizontal size={15} />
            Generation Settings
          </DialogTitle>
          <DialogDescription
            className="truncate text-xs"
            style={{ color: "var(--color-es-text-muted)" }}
          >
            {selectedModel?.id ?? "No model selected"}
          </DialogDescription>
        </DialogHeader>

        <div className="generation-summary-grid">
          <SummaryTile label="State" value={isCustom ? "Custom" : "Defaults"} />
          <SummaryTile
            label="Parameters"
            value={
              supportKnown
                ? `${supportedCount}/${SUPPORT_KEYS.length} supported`
                : "Support unknown"
            }
          />
          <SummaryTile
            label="Context limit"
            value={formatTokens(contextBudgetMax)}
          />
        </div>

        <div className="space-y-5">
          <GenerationSection title="Sampling">
            <RangeSetting
              label="Temperature"
              value={displayedSettings.temperature}
              min={0}
              max={2}
              step={0.05}
              helper="Controls randomness. Lower is more focused, higher is more creative."
              disabled={!isParamSupportedByModel("temperature", selectedModel)}
              onChange={(value) => setNumeric("temperature", value)}
            />
            <RangeSetting
              label="Top P"
              value={displayedSettings.top_p}
              min={0}
              max={1}
              step={0.01}
              helper="Limits sampling to tokens whose probabilities add up to this value."
              disabled={!isParamSupportedByModel("top_p", selectedModel)}
              onChange={(value) => setNumeric("top_p", value)}
            />
            <RangeSetting
              label="Top K"
              value={displayedSettings.top_k}
              min={0}
              max={200}
              step={1}
              integer
              helper="Limits sampling to the top K candidate tokens. Lower values are more focused."
              disabled={!isParamSupportedByModel("top_k", selectedModel)}
              onChange={(value) => setNumeric("top_k", value)}
            />
          </GenerationSection>

          <GenerationSection title="Repetition">
            <RangeSetting
              label="Repetition penalty"
              value={displayedSettings.repetition_penalty}
              min={0.8}
              max={1.5}
              step={0.01}
              helper="1.0 is neutral. Higher values reduce repetition."
              disabled={!isParamSupportedByModel("repetition_penalty", selectedModel)}
              onChange={(value) => setNumeric("repetition_penalty", value)}
            />
          </GenerationSection>

          <GenerationSection title="Output / Context">
            <RangeSetting
              label="Max new tokens"
              value={displayedSettings.max_tokens}
              min={1}
              max={maxTokensMax}
              step={1}
              integer
              helper="Maximum tokens the model can generate in the response."
              disabled={!isParamSupportedByModel("max_tokens", selectedModel)}
              onChange={(value) => setNumeric("max_tokens", value)}
            />
            <SeedSetting
              value={settings.seed}
              disabled={!isParamSupportedByModel("seed", selectedModel)}
              onChange={setSeed}
            />
            <RangeSetting
              label="Context budget"
              value={displayedSettings.context_budget_tokens}
              min={CONTEXT_BUDGET_MIN}
              max={contextBudgetMax}
              step={1}
              integer
              helper="Controls how much chat history Elysium includes in the next request. This is not sent as an OpenRouter parameter."
              onChange={(value) => setNumeric("context_budget_tokens", value)}
            />
          </GenerationSection>

          <GenerationSection title="Stop sequences">
            <StopSequencesSetting
              sequences={stopSequences}
              disabled={!isParamSupportedByModel("stop", selectedModel)}
              onChange={setStopSequences}
            />
          </GenerationSection>
        </div>

        <div className="flex justify-end gap-2 pt-1">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="sidebar-dialog-cancel text-xs"
            onClick={() => resetAll(selectedModel)}
          >
            Reset all
          </Button>
          <DialogClose render={<Button type="button" size="sm" className="sidebar-dialog-action text-xs" />}>
            Close
          </DialogClose>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function formatTokens(value: number): string {
  if (value >= 1000) return `${Math.round(value / 1000)}K`;
  return String(value);
}

function GenerationSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="generation-section">
      <h3>{title}</h3>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function SummaryTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="generation-summary-tile">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function RangeSetting({
  label,
  value,
  min,
  max,
  step,
  helper,
  disabled = false,
  integer = false,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  helper: string;
  disabled?: boolean;
  integer?: boolean;
  onChange: (value: number) => void;
}) {
  const normalizedValue = clampNumber(value, min, max);
  const commitValue = (next: number) => {
    const clamped = clampNumber(next, min, max);
    onChange(integer ? Math.round(clamped) : Number(clamped.toFixed(2)));
  };

  return (
    <div className={`generation-control ${disabled ? "is-disabled" : ""}`}>
      <div className="flex items-center justify-between gap-3">
        <label className="text-xs font-semibold">{label}</label>
        <DraftNumberInput
          ariaLabel={`${label} value`}
          min={min}
          max={max}
          step={step}
          committedValue={normalizedValue}
          disabled={disabled}
          onCommit={commitValue}
        />
      </div>
      <input
        type="range"
        aria-label={`${label} slider`}
        min={min}
        max={max}
        step={step}
        value={normalizedValue}
        disabled={disabled}
        className="generation-range"
        onChange={(event) => commitValue(Number(event.currentTarget.value))}
      />
      <p className="generation-helper">{helper}</p>
      {disabled && (
        <p className="generation-support-note">
          Not supported by selected model.
        </p>
      )}
    </div>
  );
}

/**
 * Number input that keeps the raw text while the user is typing and only
 * parses/clamps on blur or Enter. Empty or invalid input reverts to the
 * last committed value, so clearing the field never snaps to the minimum
 * mid-keystroke. The paired slider stays synced to committed values.
 */
function DraftNumberInput({
  ariaLabel,
  min,
  max,
  step,
  committedValue,
  disabled,
  onCommit,
}: {
  ariaLabel: string;
  min: number;
  max: number;
  step: number;
  committedValue: number;
  disabled: boolean;
  onCommit: (value: number) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);

  const commitDraft = (raw: string) => {
    setDraft(null);
    const parsed = Number(raw);
    if (raw.trim() === "" || !Number.isFinite(parsed)) return;
    onCommit(parsed);
  };

  return (
    <Input
      type="number"
      aria-label={ariaLabel}
      min={min}
      max={max}
      step={step}
      value={draft ?? committedValue}
      disabled={disabled}
      className="sidebar-dialog-field generation-number-input"
      onChange={(event) => setDraft(event.currentTarget.value)}
      onBlur={(event) => commitDraft(event.currentTarget.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          commitDraft(event.currentTarget.value);
        }
      }}
    />
  );
}

/** Display form of a stop sequence: real newlines render as the literal "\n". */
function displayStopSequence(sequence: string): string {
  return sequence.replaceAll("\n", "\\n");
}

/**
 * Per-sequence character cap for the stop-sequence input. Prevents a single
 * pasted value from overflowing the dialog at the source; chips additionally
 * truncate the displayed text and expose the full value via `title`.
 */
const STOP_SEQUENCE_MAX_LENGTH = 100;

/**
 * Chip editor for stop sequences. Committing converts a typed literal "\n"
 * into a real newline; chips render it back as "\n". Capped at
 * MAX_STOP_SEQUENCES; duplicates are ignored quietly.
 */
function StopSequencesSetting({
  sequences,
  disabled,
  onChange,
}: {
  sequences: string[];
  disabled: boolean;
  onChange: (sequences: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const atCap = sequences.length >= MAX_STOP_SEQUENCES;

  const commitDraft = () => {
    if (disabled || atCap) return;
    // Typed literal "\n" becomes a real newline in the committed sequence.
    const converted = draft.replaceAll("\\n", "\n");
    if (converted.length === 0) return;
    if (!sequences.includes(converted)) {
      onChange([...sequences, converted]);
    }
    setDraft("");
  };

  const removeSequence = (sequence: string) => {
    onChange(sequences.filter((s) => s !== sequence));
  };

  return (
    <div className={`generation-control ${disabled ? "is-disabled" : ""}`}>
      <div className="flex items-center justify-between gap-3">
        <label className="text-xs font-semibold">Stop sequences</label>
        <span
          className="text-[11px]"
          style={{ color: "rgba(202, 212, 224, 0.72)" }}
          data-testid="stop-sequence-count"
        >
          {sequences.length}/{MAX_STOP_SEQUENCES}
        </span>
      </div>
      {sequences.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {sequences.map((sequence, index) => {
            const display = displayStopSequence(sequence);
            return (
              <span
                key={sequence}
                className="inline-flex max-w-[12rem] items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[11px]"
                style={{
                  borderColor: "rgba(200, 216, 236, 0.16)",
                  backgroundColor: "rgba(200, 216, 236, 0.08)",
                  color: "rgba(238, 243, 249, 0.88)",
                }}
                data-testid="stop-sequence-chip"
                title={display}
              >
                <span className="truncate">{display}</span>
                {/* Remove stays enabled even when the model lacks stop support:
                    a user who switches to a model without stop support must
                    still be able to clear stale chips (stop is filtered out of
                    unsupported-model requests anyway, so removal is always safe). */}
                <button
                  type="button"
                  aria-label={`Remove stop sequence ${index + 1}`}
                  className="shrink-0 opacity-60 transition-opacity hover:opacity-100"
                  onClick={() => removeSequence(sequence)}
                >
                  <X size={10} />
                </button>
              </span>
            );
          })}
        </div>
      )}
      <div className="mt-2 flex items-center gap-2">
        <Input
          type="text"
          aria-label="Stop sequence"
          value={draft}
          disabled={disabled || atCap}
          maxLength={STOP_SEQUENCE_MAX_LENGTH}
          placeholder={atCap ? "Limit reached" : "Add a sequence"}
          className="sidebar-dialog-field h-8 flex-1 text-xs"
          onChange={(event) => setDraft(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              commitDraft();
            }
          }}
        />
        <Button
          type="button"
          size="sm"
          className="sidebar-dialog-action text-xs"
          disabled={disabled || atCap}
          onClick={commitDraft}
        >
          Add
        </Button>
      </div>
      <p className="generation-helper">
        {"Generation stops when the model outputs one of these. Type \\n for a newline. Max 4 sequences."}
      </p>
      {disabled && (
        <p className="generation-support-note">
          Not supported by selected model.
        </p>
      )}
    </div>
  );
}

function SeedSetting({
  value,
  disabled,
  onChange,
}: {
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <div className={`generation-control ${disabled ? "is-disabled" : ""}`}>
      <div className="flex items-center justify-between gap-3">
        <label className="text-xs font-semibold">Seed</label>
        <Input
          type="number"
          aria-label="Seed value"
          step={1}
          value={value}
          disabled={disabled}
          placeholder="empty"
          className="sidebar-dialog-field generation-number-input"
          onChange={(event) => onChange(event.currentTarget.value)}
        />
      </div>
      <p className="generation-helper">
        May improve repeatability, but determinism depends on model/provider.
        Values outside {SEED_MIN} to {SEED_MAX} are clamped.
      </p>
      {disabled && (
        <p className="generation-support-note">
          Not supported by selected model.
        </p>
      )}
    </div>
  );
}
