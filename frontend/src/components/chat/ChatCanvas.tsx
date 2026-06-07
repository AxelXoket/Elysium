import { useRef, useEffect, useState, useCallback } from "react";
import { useUiStore } from "@/lib/store/uiStore";
import { useMessages } from "@/lib/query/chats";
import { useSendMessage } from "@/lib/query/completions";
import { usePersonas } from "@/lib/query/personas";
import { useModels } from "@/lib/query/models";
import { getSelectedPersonaId, safePersonaId } from "@/lib/personas";
import { findModelById } from "@/lib/models";
import { useGenerationSettings } from "@/components/generation/GenerationSettingsContext";
import { useReducedMotion } from "@/components/motion/ReducedMotion";
import { MessageList } from "./MessageList";
import { EmptyState } from "./EmptyState";
import { Composer } from "./Composer";
import { ErrorToastStack } from "@/components/errors/ErrorToastStack";

export function ChatCanvas() {
  const selectedChatId = useUiStore((s) => s.selectedChatId);
  const selectedModelId = useUiStore((s) => s.selectedModelId);
  const send = useSendMessage();
  const { data: messages } = useMessages(selectedChatId);
  const { data: personas } = usePersonas();
  const { data: models } = useModels();
  const generationSettings = useGenerationSettings();
  const scrollRef = useRef<HTMLDivElement>(null);
  const reduced = useReducedMotion();

  // Draft restoration: if send fails, this holds the text to restore
  const [restoredDraft, setRestoredDraft] = useState<string | null>(null);

  // Scroll to bottom when messages change
  useEffect(() => {
    const el = scrollRef.current;
    if (el && messages && messages.length > 0 && typeof el.scrollTo === "function") {
      el.scrollTo({
        top: el.scrollHeight,
        behavior: reduced ? "instant" : "smooth",
      });
    }
  }, [messages?.length, reduced]);

  const handleSend = useCallback(
    (messageText: string) => {
      if (selectedChatId == null || selectedModelId == null) return;
      const personaId = safePersonaId(getSelectedPersonaId(personas));
      const selectedModel = findModelById(models?.models, selectedModelId);
      const { generationParams, contextBudgetTokens } =
        generationSettings.getRequestSettings();
      setRestoredDraft(null);
      send.mutate(
        {
          chatId: selectedChatId,
          message: messageText,
          modelId: selectedModelId,
          generationParams,
          personaId,
          contextBudgetTokens,
          model: selectedModel,
        },
        {
          onError: () => {
            // Restore draft on failure so user can retry
            setRestoredDraft(messageText);
          },
        },
      );
    },
    [selectedChatId, selectedModelId, personas, models?.models, generationSettings, send],
  );

  return (
    <main className="warm-canvas flex flex-1 flex-col overflow-hidden">
      <ErrorToastStack />

      {/* Message area */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {selectedChatId != null ? (
          <MessageList chatId={selectedChatId} isPending={send.isPending} />
        ) : (
          <EmptyState />
        )}
      </div>

      {/* Composer */}
      <Composer
        onSend={handleSend}
        isPending={send.isPending}
        sendError={send.error}
        clearOnSend={true}
        restoredDraft={restoredDraft}
      />
    </main>
  );
}
