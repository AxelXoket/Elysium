import type { Message } from "@/lib/schemas/chats";
import { FadeIn } from "@/components/motion/FadeIn";

interface MessageBubbleProps {
  message: Message;
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === "user";

  return (
    <FadeIn duration={0.15}>
      <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
        <div
          className="max-w-[75%] rounded-2xl px-5 py-3 text-sm leading-relaxed"
          style={
            isUser
              ? {
                  backgroundColor: "var(--color-es-user-bubble)",
                  color: "var(--color-es-user-bubble-text)",
                  borderBottomRightRadius: "5px",
                  boxShadow: "var(--shadow-bubble)",
                }
              : {
                  backgroundColor: "var(--color-es-asst-bubble)",
                  color: "var(--color-es-asst-bubble-text)",
                  borderBottomLeftRadius: "5px",
                  boxShadow: "var(--shadow-bubble)",
                }
          }
        >
          <p className="whitespace-pre-wrap break-words">{message.content}</p>
          <time
            className="mt-1.5 block text-[9px] opacity-40"
            dateTime={message.created_at}
          >
            {new Date(message.created_at).toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
            })}
          </time>
        </div>
      </div>
    </FadeIn>
  );
}
