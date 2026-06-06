import { useRef, useEffect } from "react";
import { useUiStore } from "@/lib/store/uiStore";
import { useMessages } from "@/lib/query/chats";
import { useSendMessage } from "@/lib/query/completions";
import { useReducedMotion } from "@/components/motion/ReducedMotion";
import { MessageList } from "./MessageList";
import { EmptyState } from "./EmptyState";
import { Composer } from "./Composer";

export function ChatCanvas() {
  const selectedChatId = useUiStore((s) => s.selectedChatId);
  const selectedModelId = useUiStore((s) => s.selectedModelId);
  const send = useSendMessage();
  const { data: messages } = useMessages(selectedChatId);
  // resetKey: changes each time a send succeeds (new user_message.id), signaling Composer to clear
  const resetKey = send.data ? send.data.user_message.id : 0;
  const scrollRef = useRef<HTMLDivElement>(null);
  const reduced = useReducedMotion();


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

  const handleSend = (messageText: string) => {
    if (selectedChatId == null || selectedModelId == null) return;
    send.mutate({
      chatId: selectedChatId,
      message: messageText,
      modelId: selectedModelId,
    });
  };

  return (
    <main className="warm-canvas flex flex-1 flex-col overflow-hidden">
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
        resetKey={resetKey}
      />
    </main>
  );
}
