import { ModalityBadge } from "./ModalityBadge";
import type { Model } from "@/lib/schemas/models";
import { useUiStore } from "@/lib/store/uiStore";
import { Check } from "lucide-react";

interface ModelCardProps {
  model: Model;
}

export function ModelCard({ model }: ModelCardProps) {
  const selectedModelId = useUiStore((s) => s.selectedModelId);
  const selectModel = useUiStore((s) => s.selectModel);
  const isSelected = selectedModelId === model.id;

  return (
    <button
      type="button"
      onClick={() => selectModel(model.id)}
      className={`sidebar-item w-full rounded-xl px-3 py-2.5 text-left ${
        isSelected ? "sidebar-item-selected" : "sidebar-item-unselected"
      }`}
      aria-label={`Select model ${model.name}`}
      aria-pressed={isSelected}
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
              className="truncate text-sm font-medium"
              style={{ color: "var(--color-es-text-light)" }}
            >
              {model.name}
            </span>
          </div>
          <p
            className="mt-0.5 truncate text-[11px]"
            style={{ color: "var(--color-es-text-muted)" }}
          >
            {model.id}
          </p>
        </div>
      </div>

      {/* Details */}
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        {model.context_length && (
          <span
            className="text-[10px]"
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
            className="text-[10px]"
            style={{ color: "var(--color-es-text-muted)" }}
          >
            max:{" "}
            {model.max_completion_tokens >= 1000
              ? `${Math.round(model.max_completion_tokens / 1000)}k`
              : model.max_completion_tokens}
          </span>
        )}
        {model.input_modalities.map((m) => (
          <ModalityBadge key={`in-${m}`} modality={m} />
        ))}
        {model.output_modalities
          .filter((m) => m !== "text")
          .map((m) => (
            <ModalityBadge key={`out-${m}`} modality={m} />
          ))}
      </div>
    </button>
  );
}
