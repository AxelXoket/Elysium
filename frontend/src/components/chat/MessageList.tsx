import { useMessages } from "@/lib/query/chats";
import { MessageBubble } from "./MessageBubble";
import { Skeleton } from "@/components/ui/skeleton";
import { AnimatedList, AnimatedListItem } from "@/components/motion/AnimatedList";
import { isApiError } from "@/lib/api/client";
import { AlertCircle } from "lucide-react";

interface MessageListProps {
  chatId: number;
  isPending?: boolean;
}

export function MessageList({ chatId, isPending }: MessageListProps) {
  const { data: messages, isLoading, error } = useMessages(chatId);

  if (isLoading) {
    return (
      <div className="space-y-4 px-6 py-8">
        {Array.from({ length: 3 }).map((_, i) => (
          <div
            key={i}
            className={`flex ${i % 2 === 0 ? "justify-start" : "justify-end"}`}
          >
            <Skeleton
              className="h-12 rounded-2xl"
              style={{
                width: i % 2 === 0 ? "60%" : "50%",
                backgroundColor: "rgba(47, 49, 45, 0.12)",
              }}
            />
          </div>
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center p-8">
        <div
          className="flex items-center gap-2 rounded-xl px-4 py-3 text-xs"
          style={{
            backgroundColor: "rgba(201, 110, 91, 0.10)",
            color: "var(--color-es-danger)",
          }}
        >
          <AlertCircle size={14} />
          {isApiError(error) ? error.detail : "Failed to load messages"}
        </div>
      </div>
    );
  }

  if (!messages || messages.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div
          className="rounded-2xl px-8 py-10 text-center"
          style={{
            backgroundColor: "rgba(47, 49, 45, 0.07)",
            maxWidth: "340px",
            border: "1px solid rgba(47, 49, 45, 0.10)",
          }}
        >
          <p
            className="text-sm font-medium"
            style={{ color: "var(--color-es-asst-bubble-text)" }}
          >
            {isPending ? "Sending…" : "No messages yet"}
          </p>
          {!isPending && (
            <p
              className="mt-1.5 text-xs leading-relaxed"
              style={{ color: "var(--color-es-asst-bubble-text)", opacity: 0.6 }}
            >
              Type a message below to start the conversation.
            </p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="px-6 py-8">
      <AnimatedList className="space-y-4">
        {messages.map((msg) => (
          <AnimatedListItem key={msg.id}>
            <MessageBubble message={msg} />
          </AnimatedListItem>
        ))}
      </AnimatedList>

      {/* Thinking indicator — transient, not in cache */}
      {isPending && <ThinkingBubble />}
    </div>
  );
}

/** Transient "assistant is thinking" indicator. Never inserted into query cache. */
function ThinkingBubble() {
  return (
    <div
      className="mt-4 flex justify-start"
      role="status"
      aria-live="polite"
      aria-label="Assistant is thinking"
    >
      <div
        className="flex items-center gap-2 rounded-2xl px-5 py-3"
        style={{
          backgroundColor: "var(--color-es-asst-bubble)",
          borderBottomLeftRadius: "5px",
          boxShadow: "var(--shadow-bubble)",
        }}
      >
        {/* thinking-dot class defined in index.css with staggered bounce animation */}
        <span
          className="thinking-dot inline-block h-2 w-2 rounded-full"
          style={{ backgroundColor: "var(--color-es-asst-bubble-text)" }}
        />
        <span
          className="thinking-dot inline-block h-2 w-2 rounded-full"
          style={{ backgroundColor: "var(--color-es-asst-bubble-text)" }}
        />
        <span
          className="thinking-dot inline-block h-2 w-2 rounded-full"
          style={{ backgroundColor: "var(--color-es-asst-bubble-text)" }}
        />
      </div>
    </div>
  );
}
