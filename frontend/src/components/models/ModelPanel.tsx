import { useState, useMemo } from "react";
import { useModels, useRefreshModels } from "@/lib/query/models";
import { ModelCard } from "./ModelCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { SlideIn } from "@/components/motion/SlideIn";
import { AnimatedList, AnimatedListItem } from "@/components/motion/AnimatedList";
import { RefreshCw, AlertCircle, Search, X } from "lucide-react";
import { isApiError } from "@/lib/api/client";
import { useUiStore } from "@/lib/store/uiStore";
import { GenerationSettingsDialog } from "@/components/generation/GenerationSettingsDialog";

export function ModelPanel() {
  const { data, isLoading, error } = useModels();
  const refresh = useRefreshModels();
  const [search, setSearch] = useState("");
  const selectedModelId = useUiStore((s) => s.selectedModelId);

  // Client-side filtering — null-safe: use empty string fallback for optional fields.
  // Never makes a new network request. No server-side search endpoint.
  const filteredModels = useMemo(() => {
    if (!data?.models) return [];
    const q = search.trim().toLowerCase();
    if (!q) return data.models;
    return data.models.filter((m) => {
      const id   = (m.id   ?? "").toLowerCase();
      const name = (m.name ?? "").toLowerCase();
      return id.includes(q) || name.includes(q);
    });
  }, [data?.models, search]);

  // The selected model may be filtered out during search. Find its name for the chip.
  const selectedModel = data?.models.find((m) => m.id === selectedModelId);
  const selectedIsFiltered =
    search.trim().length > 0 &&
    selectedModelId != null &&
    !filteredModels.some((m) => m.id === selectedModelId);
  const contextLength = selectedModel?.context_length ?? null;
  const contextLabel =
    contextLength != null && contextLength >= 1000
      ? `${Math.round(contextLength / 1000)}K`
      : contextLength?.toString() ?? "-";

  return (
    <SlideIn>
      <div className="model-panel space-y-3 p-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h3
            className="text-sm font-medium"
            style={{ color: "var(--color-es-text-light)" }}
          >
            Models
          </h3>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => refresh.mutate()}
            disabled={refresh.isPending || isLoading}
            className="h-7 w-7 p-0 opacity-60 hover:opacity-100"
            aria-label="Refresh models"
          >
            <RefreshCw
              size={13}
              className={refresh.isPending ? "animate-spin" : ""}
            />
          </Button>
        </div>

        {/* Source + cached badge */}
        {data && (
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge
              variant="outline"
              className="text-[10px]"
              data-testid="model-source-badge"
              style={{
                borderColor: "var(--color-es-primary-sage-deep)",
                color: "var(--color-es-primary-sage)",
              }}
            >
              {data.source}
            </Badge>
            {data.cached && (
              <Badge
                variant="outline"
                className="text-[10px]"
                style={{
                  borderColor: "var(--color-es-border-dark)",
                  color: "var(--color-es-text-muted)",
                }}
              >
                cached
              </Badge>
            )}
            <span
              className="text-[10px]"
              style={{ color: "var(--color-es-text-muted)" }}
            >
              {search.trim()
                ? `${filteredModels.length} / ${data.count}`
                : `${data.count}`}{" "}
              models
            </span>
          </div>
        )}

        {/* Fallback reason */}
        {data?.fallback_reason && (
          <div
            className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs"
            data-testid="fallback-reason"
            style={{
              backgroundColor: "rgba(215, 168, 110, 0.08)",
              color: "var(--color-es-accent-amber)",
            }}
          >
            <AlertCircle size={12} />
            {data.fallback_reason}
          </div>
        )}

        {/* Search input — only shown when data is available */}
        {data && (
          <div className="space-y-4">
            <section className="space-y-2">
              <h4
                className="text-xs font-semibold"
                style={{ color: "var(--color-es-text-light)" }}
              >
                Selected Model
              </h4>
              <div
                className="rounded-xl p-4 text-center"
                style={{
                  backgroundColor: "rgba(255,255,255,0.34)",
                  border: "1px solid rgba(47,49,45,0.14)",
                  boxShadow: "0 10px 22px rgba(47,49,45,0.08)",
                }}
              >
                <div className="flex flex-col items-center gap-2">
                  <div className="min-w-0 max-w-full">
                    <p
                      className="truncate text-sm font-semibold"
                      style={{ color: "var(--color-es-text-light)" }}
                    >
                      {selectedModel?.id ?? "No model selected"}
                    </p>
                    <p
                      className="mt-1 text-xs"
                      style={{ color: "var(--color-es-text-muted)" }}
                    >
                      {contextLabel === "-" ? "Text" : `${contextLabel} context · Text`}
                    </p>
                  </div>
                  <Badge
                    variant="outline"
                    className="shrink-0 text-[10px]"
                    style={{
                      borderColor: "rgba(47,49,45,0.18)",
                      color: "var(--color-es-text-muted)",
                    }}
                  >
                    {data.source}
                  </Badge>
                </div>
              </div>
            </section>

            <GenerationSettingsDialog selectedModel={selectedModel} />

            <section className="space-y-2">
              <h4
                className="text-xs font-semibold"
                style={{ color: "var(--color-es-text-light)" }}
              >
                Model Source
              </h4>
              <div className="space-y-2 text-xs">
                <InfoRow label="Source" value={data.source} />
                <InfoRow label="Cached" value={data.cached ? "yes" : "no"} />
                <InfoRow label="Fallback" value={data.fallback_reason ?? "-"} />
              </div>
            </section>
          </div>
        )}

        {data && (
          <div
            className="flex items-center gap-2 rounded-xl px-3 py-2"
            style={{
              backgroundColor: "rgba(255,255,255,0.05)",
              border: "1px solid var(--color-es-glass-border-dark)",
            }}
          >
            <Search
              size={12}
              style={{ color: "var(--color-es-text-muted)", flexShrink: 0 }}
            />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search models…"
              aria-label="Search models"
              className="min-w-0 flex-1 bg-transparent text-xs outline-none"
              style={{ color: "var(--color-es-text-light)" }}
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch("")}
                aria-label="Clear search"
                className="shrink-0 opacity-60 hover:opacity-100 transition-opacity"
                style={{ color: "var(--color-es-text-muted)" }}
              >
                <X size={12} />
              </button>
            )}
          </div>
        )}

        {/* Selected model chip — shown when filtered out of results */}
        {selectedIsFiltered && selectedModel && (
          <div
            className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs"
            style={{
              backgroundColor: "rgba(167, 200, 161, 0.08)",
              color: "var(--color-es-primary-sage)",
              border: "1px solid rgba(167, 200, 161, 0.18)",
            }}
          >
            <span className="opacity-70">Selected:</span>
            <span className="truncate font-medium">{selectedModel.name}</span>
          </div>
        )}

        {/* Loading */}
        {isLoading && (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton
                key={i}
                className="h-14 w-full rounded-xl"
                style={{ backgroundColor: "rgba(255,255,255,0.04)" }}
              />
            ))}
          </div>
        )}

        {/* Error */}
        {error && (
          <div
            className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs"
            style={{
              backgroundColor: "rgba(201, 110, 91, 0.08)",
              color: "var(--color-es-danger)",
            }}
          >
            <AlertCircle size={12} />
            {isApiError(error) ? error.detail : "Failed to load models"}
          </div>
        )}

        {/* Model list */}
        {filteredModels.length > 0 && (
          <AnimatedList className="space-y-1">
            {filteredModels.map((model) => (
              <AnimatedListItem key={model.id}>
                <ModelCard model={model} />
              </AnimatedListItem>
            ))}
          </AnimatedList>
        )}

        {/* Empty search result */}
        {data && filteredModels.length === 0 && search.trim().length > 0 && (
          <p
            className="py-4 text-center text-xs"
            style={{ color: "var(--color-es-text-muted)" }}
            data-testid="model-search-empty"
          >
            No models match &ldquo;{search}&rdquo;
          </p>
        )}

        {/* No models at all */}
        {data && data.models.length === 0 && search.trim().length === 0 && (
          <p
            className="py-4 text-center text-xs"
            style={{ color: "var(--color-es-text-muted)" }}
          >
            No models available
          </p>
        )}
      </div>
    </SlideIn>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span style={{ color: "var(--color-es-text-muted)" }}>{label}</span>
      <span
        className="truncate text-right"
        style={{ color: "var(--color-es-text-light)" }}
      >
        {value}
      </span>
    </div>
  );
}
