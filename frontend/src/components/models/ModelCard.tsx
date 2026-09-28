import { memo, useId } from "react";
import { ModalityBadge } from "./ModalityBadge";
import type { Model } from "@/lib/schemas/models";
import { useUiStore } from "@/lib/store/uiStore";
import { Check, ShieldOff } from "lucide-react";

/** Why a row cannot be picked (Q-121). Exported so tests read the same words. */
export const NO_PRIVATE_PROVIDER_LABEL = "No private provider";
export const NO_PRIVATE_PROVIDER_REASON =
  "Unavailable: no provider for this model meets Elysium's privacy rules (zero data retention), so a message could not be sent to it.";

interface ModelCardProps {
  model: Model;
}

/* memo + boolean selector: with 237 cards mounted, every parent re-render
   (each search keystroke) and every selection change used to re-render the
   whole list in one burst - visible as an ambient-fog hiccup. The boolean
   selector re-renders exactly two cards on selection (old + new); memo
   skips the rest entirely (model objects are stable query-cache refs). */
export const ModelCard = memo(function ModelCard({ model }: ModelCardProps) {
  const isSelected = useUiStore((s) => s.selectedModelId === model.id);
  const selectModel = useUiStore((s) => s.selectModel);
  const reasonId = useId();
  // Q-121: 0 means the backend counted this model's zero-data-retention
  // endpoints and none takes a chat request, so the send point would refuse
  // it. Disabled with the reason on the row, never hidden. null/undefined
  // (the list unknown) claims nothing and leaves the row as it was.
  // aria-disabled rather than `disabled`, so the row stays focusable and a
  // screen reader can still reach the reason.
  const blocked = model.zdr_endpoints === 0;

  return (
    <button
      type="button"
      onClick={blocked ? undefined : () => selectModel(model.id)}
      className={`sidebar-item w-full rounded-xl px-3 py-2.5 text-left ${
        isSelected ? "sidebar-item-selected" : "sidebar-item-unselected"
      }`}
      style={blocked ? { opacity: 0.55, cursor: "not-allowed" } : undefined}
      aria-label={`Select model ${model.name}`}
      aria-pressed={isSelected}
      aria-disabled={blocked || undefined}
      aria-describedby={blocked ? reasonId : undefined}
      title={blocked ? NO_PRIVATE_PROVIDER_REASON : undefined}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            {isSelected && (
              <Check
                size={12}
                style={{ color: "var(--color-es-primary-sage)" }}
              />
            )}
            <span
              className="truncate text-ui font-semibold"
              style={{ color: "var(--color-es-text-light)" }}
              title={model.name}
            >
              {model.name}
            </span>
          </div>
          <p
            className="mt-0.5 truncate text-micro"
            style={{ color: "var(--color-es-text-muted)" }}
            title={model.id}
          >
            {model.id}
          </p>
        </div>
      </div>

      {/* Details */}
      {/* min-h reserves the line whether or not this row has anything in it.
          Every card used to carry at least a "Text" badge, so the row always
          took its height - and .model-row-lazy's contain-intrinsic-size (78px)
          was calibrated against that. Dropping the text badge let the row
          collapse on plain models, which made rows SHORTER than the estimate:
          scrolling then re-measured the list shorter than the scrollbar
          promised, scrollTop was clamped back, and the list could not be
          scrolled down. Reserving the line keeps estimate and reality equal. */}
      <div className="mt-1.5 flex min-h-[1.125rem] flex-wrap items-center gap-1.5">
        {blocked && (
          <span
            id={reasonId}
            className="inline-flex items-center gap-1 text-micro"
            style={{ color: "var(--color-es-danger)" }}
          >
            <ShieldOff size={10} aria-hidden="true" />
            {NO_PRIVATE_PROVIDER_LABEL}
            <span className="sr-only">. {NO_PRIVATE_PROVIDER_REASON}</span>
          </span>
        )}
        {model.context_length && (
          <span
            className="text-micro"
            style={{ color: "var(--color-es-text-muted)" }}
          >
            ctx:{" "}
            {model.context_length >= 1000
              ? `${Math.round(model.context_length / 1000)}k`
              : model.context_length}
          </span>
        )}
        {model.max_completion_tokens && (
          <span
            className="text-micro"
            style={{ color: "var(--color-es-text-muted)" }}
          >
            max:{" "}
            {model.max_completion_tokens >= 1000
              ? `${Math.round(model.max_completion_tokens / 1000)}k`
              : model.max_completion_tokens}
          </span>
        )}
        {/* "Text" is dropped from BOTH sides. Every model reads and writes
            text, so the badge carried no information while taking the space and
            the attention of the ones that do: a card now shows a badge only for
            something worth knowing, and a plain text model shows none at all. */}
        {model.input_modalities
          .filter((m) => m !== "text")
          .map((m) => (
            <ModalityBadge key={`in-${m}`} modality={m} direction="in" />
          ))}
        {model.output_modalities
          .filter((m) => m !== "text")
          .map((m) => (
            <ModalityBadge key={`out-${m}`} modality={m} direction="out" />
          ))}
      </div>
    </button>
  );
});
