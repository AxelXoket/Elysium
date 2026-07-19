import { useState, useMemo, useId, useEffect } from "react";
import { useModels, useRefreshModels } from "@/lib/query/models";
import { useChats, useMessages } from "@/lib/query/chats";
import { useCharacters } from "@/lib/query/characters";
import { usePersonas } from "@/lib/query/personas";
import { ModelCard } from "./ModelCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { SlideIn } from "@/components/motion/SlideIn";
import { AnimatedList, AnimatedListItem } from "@/components/motion/AnimatedList";
import { Collapse } from "@/components/motion/Collapse";
import { RefreshCw, AlertCircle, Search, X, ChevronDown } from "lucide-react";
import { parseApiError } from "@/lib/errors";
import { useUiStore } from "@/lib/store/uiStore";
import { GenerationSettingsDialog } from "@/components/generation/GenerationSettingsDialog";
import { useGenerationSettings } from "@/components/generation/GenerationSettingsContext";
import { ActiveContextPreviewCard } from "@/components/preview/ActiveContextPreviewCard";
import {
  estimateContextUsage,
  formatTokensCompact,
  getContextUsageState,
  type ContextUsageEstimate,
  type ContextUsageState,
} from "@/lib/context";

/**
 * Map the backend's raw fallback_reason to safe UI copy.
 * Raw values ("timeout", "http_502", exception class names) are internal
 * diagnostics and must never render verbatim.
 */
function describeFallbackReason(reason: string): string {
  if (reason === "timeout") return "primary source timed out";
  const httpMatch = /^http_(\d{3})$/.exec(reason);
  if (httpMatch) return `primary source error (HTTP ${httpMatch[1]})`;
  return "primary source unavailable";
}

/** How many model rows join the entrance cascade - roughly one viewport's
 * worth. Rows past this render static/lazy (see the model-list comment). */
const CASCADE_ROWS = 16;

/** Rows added per idle period while the long tail mounts in the background. */
const IDLE_CHUNK = 48;

export function ModelPanel() {
  const { data, isLoading, error } = useModels();
  const refresh = useRefreshModels();
  const [search, setSearch] = useState("");
  const [sourceOpen, setSourceOpen] = useState(false);
  const selectedModelId = useUiStore((s) => s.selectedModelId);
  const selectedChatId = useUiStore((s) => s.selectedChatId);

  // Live context usage inputs - all derived from existing hooks, so the
  // meter re-renders on chat, message, model, and settings changes.
  const { data: chats } = useChats();
  const { data: messages } = useMessages(selectedChatId);
  const { data: characters } = useCharacters();
  const { data: personas } = usePersonas();
  const { getRequestSettings } = useGenerationSettings();

  // Client-side filtering - null-safe: use empty string fallback for optional fields.
  // Never makes a new network request. No server-side search endpoint.
  // `models` is hoisted out of the memo so the React Compiler can preserve the
  // memoization (optional chaining inside the deps array defeated its analysis).
  const models = data?.models;
  const filteredModels = useMemo(() => {
    if (!models) return [];
    const q = search.trim().toLowerCase();
    if (!q) return models;
    return models.filter((m) => {
      const id   = (m.id   ?? "").toLowerCase();
      const name = (m.name ?? "").toLowerCase();
      return id.includes(q) || name.includes(q);
    });
  }, [models, search]);

  // Staged mounting: the tab-switch commit builds ONLY the cascade rows; the
  // long tail mounts in idle-time chunks after the entrance settles. Building
  // all 237 cards inside the click's commit was the residual switch hitch
  // ("tık" vs "tıkıt") - lazy paint skips layout, not DOM construction.
  const [rowBudget, setRowBudget] = useState(CASCADE_ROWS);
  const totalModels = models?.length ?? 0;

  useEffect(() => {
    if (rowBudget >= totalModels) return;
    let idleId: number | null = null;
    let fallbackId: ReturnType<typeof setTimeout> | null = null;
    const grow = () =>
      setRowBudget((b) => Math.min(b + IDLE_CHUNK, totalModels));
    const schedule = () => {
      if (typeof requestIdleCallback === "function") {
        idleId = requestIdleCallback(grow);
      } else {
        fallbackId = setTimeout(grow, 60); // jsdom / older engines
      }
    };
    // The first growth waits out the entrance cascade; later ones re-enter
    // through this effect and go straight to the next idle period.
    const starter = setTimeout(schedule, rowBudget === CASCADE_ROWS ? 700 : 0);
    return () => {
      clearTimeout(starter);
      if (fallbackId != null) clearTimeout(fallbackId);
      if (idleId != null && typeof cancelIdleCallback === "function") {
        cancelIdleCallback(idleId);
      }
    };
  }, [rowBudget, totalModels]);

  // The row budget applies in BOTH modes: a broad first keystroke can still
  // match 150+ models, and mounting them all in one commit is the exact
  // "tıkıt" hitch staged mounting exists to avoid (content-visibility skips
  // paint, not DOM construction). Growth continues under search (totalModels
  // is the unfiltered count), so the tail fills within ~1s either way.
  const searching = search.trim().length > 0;
  const mountableModels = filteredModels.slice(
    0,
    Math.max(rowBudget, CASCADE_ROWS),
  );

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

  // Estimated context usage for the CURRENT chat with the selected model.
  // The backend derives the system block from the chat's character
  // (completions.py reads chats.character_id), so resolve it the same way.
  const selectedChat =
    selectedChatId != null
      ? chats?.find((c) => c.id === selectedChatId)
      : undefined;
  const chatCharacter = selectedChat
    ? characters?.find((c) => c.id === selectedChat.character_id)
    : undefined;
  const { generationParams, contextBudgetTokens } = getRequestSettings();
  const contextUsage = estimateContextUsage({
    model: selectedModel ?? null,
    character: chatCharacter ?? null,
    personas: personas ?? null,
    messages: messages ?? null,
    generationParams,
    contextBudgetTokens,
  });

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
              backgroundColor: "rgba(94, 130, 174, 0.08)",
              color: "var(--color-es-accent-amber)",
            }}
          >
            <AlertCircle size={12} />
            {describeFallbackReason(data.fallback_reason)}
          </div>
        )}

        {/* Search input - only shown when data is available */}
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
                  border: "1px solid rgba(28, 38, 50,0.14)",
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
                  {selectedModel && contextUsage && (
                    <ContextUsageMeter usage={contextUsage} />
                  )}
                  {selectedModel && selectedChatId == null && (
                    <p
                      className="w-full text-[11px]"
                      style={{ color: "var(--color-es-text-muted)" }}
                      data-testid="context-usage-empty"
                    >
                      Select a chat to see context usage
                    </p>
                  )}
                  <Badge
                    variant="outline"
                    className="shrink-0 text-[10px]"
                    style={{
                      borderColor: "rgba(28, 38, 50,0.18)",
                      color: "var(--color-es-text-muted)",
                    }}
                  >
                    {data.source}
                  </Badge>
                </div>
              </div>
            </section>
          </div>
        )}

        {/* Secondary controls & info - above the model list, which stays at the
            bottom of the panel. */}
        {data && (
          <div className="space-y-4">
            <GenerationSettingsDialog selectedModel={selectedModel} />

            <ActiveContextPreviewCard />

            <div
              className="rounded-xl"
              style={{
                backgroundColor: "rgba(255,255,255,0.34)",
                border: "1px solid rgba(28, 38, 50,0.14)",
              }}
            >
              <button
                type="button"
                aria-expanded={sourceOpen}
                onClick={() => setSourceOpen((current) => !current)}
                className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left"
              >
                <span
                  className="text-xs font-semibold"
                  style={{ color: "var(--color-es-text-light)" }}
                >
                  Model Source
                </span>
                <ChevronDown
                  size={13}
                  className={`shrink-0 transition-transform ${sourceOpen ? "rotate-180" : ""}`}
                  style={{ color: "var(--color-es-text-muted)" }}
                />
              </button>
              <Collapse open={sourceOpen}>
                <div className="space-y-2 px-3 pb-3 text-xs">
                  <InfoRow label="Source" value={data.source} />
                  <InfoRow label="Cached" value={data.cached ? "yes" : "no"} />
                  <InfoRow
                    label="Fallback"
                    value={
                      data.fallback_reason
                        ? describeFallbackReason(data.fallback_reason)
                        : "-"
                    }
                  />
                </div>
              </Collapse>
            </div>
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

        {/* Selected model chip - shown when filtered out of results */}
        {selectedIsFiltered && selectedModel && (
          <div
            className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs"
            style={{
              backgroundColor: "rgba(62, 114, 176, 0.08)",
              color: "var(--color-es-primary-sage)",
              border: "1px solid rgba(62, 114, 176, 0.18)",
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
              backgroundColor: "rgba(195, 106, 114, 0.08)",
              color: "var(--color-es-danger)",
            }}
          >
            <AlertCircle size={12} />
            {parseApiError(error).message}
          </div>
        )}

        {/* Model list. The entrance cascade covers only the rows that can be
            SEEN entering - staggering all 237 spawned ~10 seconds of live
            tweens that starved the ambient fog's rAF budget (the tab-switch
            stutter). Rows below the fold render static and lazily
            (.model-row-lazy skips their layout/paint until scrolled near),
            which also makes the tab mount itself cheap. */}
        {filteredModels.length > 0 &&
          (searching ? (
            /* Search results: one wrapper type for every row (still budgeted)
               - mixing the cascade wrapper in would remount rows crossing
               index 16 on each keystroke (entrance replay + focus loss).
               Filter updates are not entrances; they render flat and lazy. */
            <div className="space-y-1">
              {mountableModels.map((model) => (
                <div key={model.id} className="model-row-lazy">
                  <ModelCard model={model} />
                </div>
              ))}
            </div>
          ) : (
            <AnimatedList className="space-y-1">
              {mountableModels.slice(0, CASCADE_ROWS).map((model) => (
                <AnimatedListItem key={model.id}>
                  <ModelCard model={model} />
                </AnimatedListItem>
              ))}
              {mountableModels.slice(CASCADE_ROWS).map((model) => (
                <div key={model.id} className="model-row-lazy">
                  <ModelCard model={model} />
                </div>
              ))}
            </AnimatedList>
          ))}

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

/** Meter fill color per severity - vars already used for warnings/danger. */
const METER_COLORS: Record<ContextUsageState, string> = {
  normal: "var(--color-es-primary-sage)",
  warning: "var(--color-es-accent-amber)",
  danger: "var(--color-es-danger)",
};

/**
 * Slim estimated-usage meter for the Selected Model card. The "≈" marks the
 * numbers as an estimate (chars/3 math mirroring the backend budget, see
 * lib/context/estimateContextUsage.ts) - never an exact token count.
 */
function ContextUsageMeter({ usage }: { usage: ContextUsageEstimate }) {
  const state = getContextUsageState(usage.percent);
  const caveatId = useId();
  // aria-valuenow must be an integer within [valuemin, valuemax]; the visible
  // math (fill width, label) is left untouched - this only derives a safe value
  // for assistive technology.
  const ariaPercent = Math.round(Math.min(100, Math.max(0, usage.percent)));
  const label =
    `Context ≈ ${formatTokensCompact(usage.usedTokens)} / ` +
    `${formatTokensCompact(usage.capacityTokens)} tokens · ` +
    `${usage.totalMessages} ${usage.totalMessages === 1 ? "msg" : "msgs"}` +
    (usage.droppedMessages > 0
      ? ` (${usage.droppedMessages} oldest dropped)`
      : "");

  return (
    <div
      className="w-full"
      data-testid="context-usage-meter"
      data-state={state}
    >
      <div
        role="progressbar"
        aria-valuenow={ariaPercent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`Estimated context usage: ${ariaPercent} percent`}
        aria-describedby={caveatId}
        className="h-1.5 w-full overflow-hidden rounded-full"
        style={{ backgroundColor: "rgba(28, 38, 50,0.12)" }}
        title="Estimated locally from message sizes - actual provider token counts vary."
      >
        <div
          className="h-full rounded-full transition-[width]"
          data-testid="context-usage-fill"
          style={{
            width: `${usage.percent}%`,
            backgroundColor: METER_COLORS[state],
          }}
        />
      </div>
      <p
        className="mt-1 text-[11px]"
        style={{ color: "var(--color-es-text-muted)" }}
      >
        {label}
      </p>
      {/* Caveat surfaced to assistive tech (not mouse-hover only). The visible
          "≈" already marks the numbers as an estimate. */}
      <span id={caveatId} className="sr-only">
        Estimated locally from message sizes; actual provider token counts vary.
      </span>
    </div>
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
