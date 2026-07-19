import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { Collapse } from "@/components/motion/Collapse";
import { useUiStore } from "@/lib/store/uiStore";
import { useCharacters } from "@/lib/query/characters";
import { usePersonas } from "@/lib/query/personas";
import { useModels } from "@/lib/query/models";
import { useChats, useMessages } from "@/lib/query/chats";
import { useGenerationSettings } from "@/components/generation/GenerationSettingsContext";
import { buildActiveContextPreview } from "@/lib/preview";
import { findModelById, TEXT_ONLY_NOTE } from "@/lib/models";
import { estimateContextUsage, formatTokensCompact } from "@/lib/context";

/**
 * ActiveContextPreviewCard - FE-8B: display-only preview of what Elysium
 * plans to include in the next completion request.
 *
 * Purely derived from existing hooks on every render - no own fetching, no
 * payload construction, no browser persistence (the collapse state is plain
 * component state, per the roadmap rules). All privacy filtering happens in
 * buildActiveContextPreview; this component renders only what it returns.
 */
export function ActiveContextPreviewCard() {
  // Collapsed by default; never persisted.
  const [open, setOpen] = useState(false);

  const selectedChatId = useUiStore((s) => s.selectedChatId);
  const selectedCharacterId = useUiStore((s) => s.selectedCharacterId);
  const selectedModelId = useUiStore((s) => s.selectedModelId);

  const { data: characters } = useCharacters();
  const { data: personas } = usePersonas();
  const { data: modelList } = useModels();
  const { data: chats } = useChats();
  const { data: messages } = useMessages(selectedChatId);
  const { getRequestSettings } = useGenerationSettings();

  const model = findModelById(modelList?.models, selectedModelId) ?? null;
  const character =
    selectedCharacterId != null
      ? characters?.find((c) => c.id === selectedCharacterId) ?? null
      : null;
  const messageCount =
    selectedChatId != null ? messages?.length ?? null : null;
  const { generationParams, contextBudgetTokens } = getRequestSettings();

  const preview = buildActiveContextPreview({
    model,
    personas: personas ?? null,
    character,
    messageCount,
    generationParams,
    contextBudgetTokens,
  });
  const { included } = preview;

  const paramKeys = included.generationParams
    ? Object.keys(included.generationParams).join(", ")
    : "None";

  // Estimated context usage for the Messages row - same estimator as the
  // Selected Model card meter (lib/context), no duplicate math. The backend
  // builds the system block from the CHAT's character (chats.character_id),
  // so resolve it the same way here.
  const selectedChat =
    selectedChatId != null
      ? chats?.find((c) => c.id === selectedChatId)
      : undefined;
  const chatCharacter = selectedChat
    ? characters?.find((c) => c.id === selectedChat.character_id) ?? null
    : null;
  const contextUsage = estimateContextUsage({
    model,
    character: chatCharacter,
    personas: personas ?? null,
    messages: messages ?? null,
    generationParams,
    contextBudgetTokens,
  });
  const messagesValue =
    contextUsage && contextUsage.totalMessages > 0
      ? `${contextUsage.includedMessages} of ${contextUsage.totalMessages} ` +
        `${contextUsage.totalMessages === 1 ? "message fits" : "messages fit"} ` +
        `(≈${formatTokensCompact(contextUsage.usedTokens)} / ` +
        `${formatTokensCompact(contextUsage.capacityTokens)} tokens)`
      : included.messages?.note ?? "No chat selected";

  return (
    <section className="px-4 pb-4" data-testid="active-context-preview">
      <div
        className="rounded-xl"
        style={{
          backgroundColor: "rgba(255,255,255,0.34)",
          border: "1px solid rgba(28, 38, 50,0.14)",
        }}
      >
        <button
          type="button"
          aria-expanded={open}
          data-testid="active-context-preview-toggle"
          className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left"
          onClick={() => setOpen((current) => !current)}
        >
          <span
            className="text-xs font-semibold"
            style={{ color: "var(--color-es-text-light)" }}
          >
            Active Context Preview
          </span>
          <ChevronDown
            size={13}
            className={`shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
            style={{ color: "var(--color-es-text-muted)" }}
          />
        </button>

        <Collapse open={open}>
          <div className="space-y-3 px-3 pb-3">
            <section className="space-y-1.5">
              <h4
                className="text-xs font-semibold"
                style={{ color: "var(--color-es-text-light)" }}
              >
                Next request includes
              </h4>
              <div className="space-y-1 text-xs">
                <PreviewRow
                  label="Model"
                  value={included.model?.displayName ?? "No model selected"}
                />
                <PreviewRow
                  label="Persona"
                  value={included.persona?.displayName ?? "No active persona"}
                />
                <PreviewRow
                  label="Character"
                  value={included.character?.name ?? "No character selected"}
                />
                <PreviewRow label="Messages" value={messagesValue} />
                <PreviewRow label="Parameters" value={paramKeys} />
                <PreviewRow
                  label="Context budget"
                  value={
                    included.contextBudget
                      ? `${included.contextBudget.tokens} tokens`
                      : "Not set"
                  }
                />
              </div>
              {contextUsage && contextUsage.droppedMessages > 0 && (
                <p
                  className="generation-support-note"
                  data-testid="context-usage-dropped-note"
                >
                  ({contextUsage.droppedMessages} oldest dropped)
                </p>
              )}
              {included.contextBudget && (
                <p className="generation-support-note">
                  {included.contextBudget.label}
                </p>
              )}
              {included.model?.showTextOnlyNote && (
                <p className="generation-support-note">{TEXT_ONLY_NOTE}</p>
              )}
            </section>

            <section className="space-y-1.5">
              <h4
                className="text-xs font-semibold"
                style={{ color: "var(--color-es-text-light)" }}
              >
                Not included
              </h4>
              <ul className="ml-3 list-disc space-y-0.5">
                {preview.notIncluded.map((item) => (
                  <li
                    key={item}
                    className="text-[11px]"
                    style={{ color: "var(--color-es-text-muted)" }}
                  >
                    {item}
                  </li>
                ))}
              </ul>
            </section>

            <p className="generation-support-note">{preview.disclaimer}</p>
          </div>
        </Collapse>
      </div>
    </section>
  );
}

function PreviewRow({ label, value }: { label: string; value: string }) {
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
