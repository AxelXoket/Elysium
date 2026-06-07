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
  clampNumber,
  getContextBudgetUiMax,
  getMaxTokensUiMax,
  getModelAwareGenerationDefaults,
  useGenerationSettings,
  type GenerationSettingsValues,
} from "./GenerationSettingsContext";
import type { Model } from "@/lib/schemas/models";
import { SlidersHorizontal } from "lucide-react";

type NumericSettingKey = Exclude<keyof GenerationSettingsValues, "seed">;
type SupportedGenerationKey =
  | "temperature"
  | "top_p"
  | "top_k"
  | "repetition_penalty"
  | "max_tokens"
  | "seed";

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
];

export function GenerationSettingsDialog({
  selectedModel,
}: GenerationSettingsDialogProps) {
  const [open, setOpen] = useState(false);
  const { settings, setSetting, resetAll } = useGenerationSettings();
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
    displayedSettings.seed.trim().length > 0;

  const supportedCount = supportKnown
    ? SUPPORT_KEYS.filter((key) => isSupported(key, selectedModel)).length
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
              disabled={!isSupported("temperature", selectedModel)}
              onChange={(value) => setNumeric("temperature", value)}
            />
            <RangeSetting
              label="Top P"
              value={displayedSettings.top_p}
              min={0}
              max={1}
              step={0.01}
              helper="Limits sampling to tokens whose probabilities add up to this value."
              disabled={!isSupported("top_p", selectedModel)}
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
              disabled={!isSupported("top_k", selectedModel)}
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
              disabled={!isSupported("repetition_penalty", selectedModel)}
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
              disabled={!isSupported("max_tokens", selectedModel)}
              onChange={(value) => setNumeric("max_tokens", value)}
            />
            <SeedSetting
              value={settings.seed}
              disabled={!isSupported("seed", selectedModel)}
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

function isSupported(
  key: SupportedGenerationKey,
  model: Model | undefined,
): boolean {
  if (!model?.supported_parameters || model.supported_parameters.length === 0) {
    return true;
  }
  return model.supported_parameters.includes(key);
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
        <Input
          type="number"
          aria-label={`${label} value`}
          min={min}
          max={max}
          step={step}
          value={normalizedValue}
          disabled={disabled}
          className="sidebar-dialog-field generation-number-input"
          onChange={(event) => commitValue(Number(event.currentTarget.value))}
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
      </p>
      {disabled && (
        <p className="generation-support-note">
          Not supported by selected model.
        </p>
      )}
    </div>
  );
}
