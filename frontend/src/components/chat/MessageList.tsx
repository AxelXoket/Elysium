import { memo, useMemo, useState } from "react";
import { useMessages } from "@/lib/query/chats";
import { MessageBubble } from "./MessageBubble";
import { MessageText } from "./MessageText";
import { Skeleton } from "@/components/ui/skeleton";
import { AnimatedList, AnimatedListItem } from "@/components/motion/AnimatedList";
import { parseApiError } from "@/lib/errors";
import { messageAnchor, isMessageActive } from "@/lib/chat";
import { AlertCircle } from "lucide-react";
import type { Message } from "@/lib/schemas/chats";
import type { StreamingEntry } from "@/lib/chat/useStreamingCompletion";

/** Upper bound for the stable-key map - old swaps stop mattering long before
 * this; the cap just prevents unbounded growth in very long sessions. */
const STABLE_KEY_CAP = 50;

/** Previous-render snapshot for the stable-key derivation. */
interface StableKeyState {
  chatId: number;
  messages: Message[] | undefined;
  keyMap: ReadonlyMap<number, string>;
}

/**
 * Derive the next realId→key map from one messages transition (pure).
 *
 * When the previous list had an optimistic (negative-id) message at some
 * index and the current list has a real message at the same index with
 * identical role+content, the real id inherits the key the optimistic entry
 * rendered with. Returns the previous map when nothing changed.
 */
function deriveStableKeys(
  prevKeys: ReadonlyMap<number, string>,
  prevMessages: Message[] | undefined,
  messages: Message[] | undefined,
): ReadonlyMap<number, string> {
  if (!prevMessages || !messages) return prevKeys;
  let next: Map<number, string> | null = null;
  const overlap = Math.min(prevMessages.length, messages.length);
  for (let i = 0; i < overlap; i++) {
    const before = prevMessages[i];
    const after = messages[i];
    if (
      before.id < 0 &&
      after.id > 0 &&
      before.role === after.role &&
      before.content === after.content &&
      !prevKeys.has(after.id)
    ) {
      next ??= new Map(prevKeys);
      // The key the optimistic entry rendered with (String(negId) unless it
      // was itself mapped, which negative ids never are).
      next.set(after.id, prevKeys.get(before.id) ?? String(before.id));
    }
  }
  if (next == null) return prevKeys;
  while (next.size > STABLE_KEY_CAP) {
    const oldest = next.keys().next().value;
    if (oldest == null) break;
    next.delete(oldest);
  }
  return next;
}

/** One rendered bubble: a variant group collapsed to its displayed row. */
interface DisplayEntry {
  anchor: number;
  rows: Message[];
  display: Message;
}

interface MessageListProps {
  chatId: number;
  /** True when a send or regenerate for THIS chat is in flight. */
  isPending?: boolean;
  /** True when a regenerate for THIS chat is in flight (bubble spinner). */
  regenerating?: boolean;
  /** Called with the target message id when the user asks for a new variant. */
  onRegenerate?: (messageId: number) => void;
  /** Called with a sibling row id to make it the active variant. */
  onActivateVariant?: (messageId: number) => void;
  /** Aborts the in-flight generation (left arrow during streaming). */
  onAbortGeneration?: () => void;
  /** Active streaming state for THIS chat (accumulating text), if any. */
  streaming?: StreamingEntry | null;
}

/* memo: ChatCanvas re-renders per composer keystroke (live drafts) and per
   streaming flush; every prop here is referentially stable across keystrokes
   (streaming is null or the same entry between flushes), so memo confines
   typing cost to the composer instead of reconciling the whole thread. */
export const MessageList = memo(function MessageList({
  chatId,
  isPending,
  regenerating,
  onRegenerate,
  onActivateVariant,
  onAbortGeneration,
  streaming,
}: MessageListProps) {
  const { data: messages, isLoading, error } = useMessages(chatId);

  // Collapse variant siblings into one bubble per group (the active row).
  // Groups keep the position of their FIRST row, so the list order never
  // shifts when a different variant becomes active.
  const displayEntries = useMemo<DisplayEntry[]>(() => {
    if (!messages) return [];
    const byAnchor = new Map<number, DisplayEntry>();
    const order: DisplayEntry[] = [];
    for (const msg of messages) {
      const anchor = messageAnchor(msg);
      let entry = byAnchor.get(anchor);
      if (!entry) {
        entry = { anchor, rows: [], display: msg };
        byAnchor.set(anchor, entry);
        order.push(entry);
      }
      entry.rows.push(msg);
    }
    for (const entry of order) {
      entry.display =
        entry.rows.find(isMessageActive) ?? entry.rows[entry.rows.length - 1];
    }
    return order;
  }, [messages]);

  // Stable keys across the optimistic→real id swap (deferred finding L10):
  // when the persisted user row replaces the optimistic (negative-id) entry,
  // the bubble must keep the key it already rendered with - otherwise React
  // remounts the node and the entrance animation replays as a visible
  // flicker. Uses the documented "adjust state during render" pattern so the
  // map is correct in THIS render pass (an effect would be one commit late).
  const [keyState, setKeyState] = useState<StableKeyState>(() => ({
    chatId,
    messages,
    keyMap: new Map<number, string>(),
  }));

  let keyMap = keyState.keyMap;
  if (keyState.chatId !== chatId) {
    // Chat switch - ids never carry over; drop the map.
    keyMap = new Map<number, string>();
    setKeyState({ chatId, messages, keyMap });
  } else if (keyState.messages !== messages) {
    keyMap = deriveStableKeys(keyState.keyMap, keyState.messages, messages);
    setKeyState({ chatId, messages, keyMap });
  }

  // Bubble keys are the GROUP anchor (not the row id): a variant switch must
  // never remount the bubble node, or the entrance animation replays as a
  // flicker - the same failure class the optimistic-id keyMap exists for.
  const keyFor = (message: Message, anchor: number): string =>
    keyMap.get(message.id) ?? String(anchor);

  if (isLoading) {
    return (
      <div className="space-y-4 px-6 py-8">
        {Array.from({ length: 3 }).map((_, i) => (
          <div
            key={i}
            className={`flex ${i % 2 === 0 ? "justify-start" : "justify-end"}`}
          >
            <Skeleton
              className="h-12 rounded-xl"
              style={{
                width: i % 2 === 0 ? "60%" : "50%",
                backgroundColor: "rgba(28, 38, 50, 0.12)",
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
            backgroundColor: "rgba(195, 106, 114, 0.10)",
            color: "var(--color-es-danger)",
          }}
        >
          <AlertCircle size={14} />
          {parseApiError(error).message}
        </div>
      </div>
    );
  }

  if (!messages || messages.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div
          className="rounded-xl px-8 py-10 text-center"
          style={{
            backgroundColor: "rgba(28, 38, 50, 0.07)",
            maxWidth: "340px",
            border: "1px solid rgba(28, 38, 50, 0.10)",
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

  // Streaming display state:
  //  - kind="send": before the first delta the ThinkingBubble stays; from the
  //    first delta a transient assistant bubble renders the accumulating text.
  //  - kind="regenerate": the TARGET GROUP's bubble hosts the whole
  //    generation in place (dots pane, then the accumulating text) - no
  //    bottom-of-list indicator.
  const streamingHasText = streaming != null && streaming.text.length > 0;
  const streamingSendText =
    streaming?.kind === "send" && streaming.text.length > 0
      ? streaming.text
      : null;
  const regenTargetAnchor =
    streaming?.kind === "regenerate" ? streaming.targetAnchor ?? null : null;

  return (
    <div className="px-6 py-8">
      <AnimatedList className="space-y-4">
        {displayEntries.map(({ anchor, rows, display }) => (
          <AnimatedListItem key={keyFor(display, anchor)}>
            <MessageBubble
              chatId={chatId}
              message={display}
              messages={messages}
              group={rows}
              onRegenerate={onRegenerate}
              onActivateVariant={onActivateVariant}
              onAbortGeneration={onAbortGeneration}
              regenerating={regenerating}
              pendingForChat={isPending}
              isStreamingTarget={regenTargetAnchor === anchor}
              streamingText={
                regenTargetAnchor === anchor && streaming!.text.length > 0
                  ? streaming!.text
                  : null
              }
            />
          </AnimatedListItem>
        ))}
      </AnimatedList>

      {/* Streaming assistant bubble - transient, not in cache */}
      {streamingSendText != null && <StreamingBubble text={streamingSendText} />}

      {/* Thinking indicator - transient, not in cache. Regenerate renders its
          own in-bubble dots, so it never shows a bottom indicator. */}
      {isPending && !streamingHasText && streaming?.kind !== "regenerate" && (
        <ThinkingBubble />
      )}
    </div>
  );
});

/** Transient assistant bubble rendering in-flight streamed text. */
function StreamingBubble({ text }: { text: string }) {
  return (
    <div className="mt-4 flex justify-start">
      <div
        className="message-bubble-shell is-assistant max-w-[75%] rounded-xl px-5 py-3 text-sm leading-relaxed"
        style={{
          backgroundColor: "var(--color-es-asst-bubble)",
          color: "var(--color-es-asst-bubble-text)",
          borderBottomLeftRadius: "2px",
          boxShadow: "var(--shadow-bubble)",
        }}
      >
        <p className="message-text whitespace-pre-wrap break-words">
          <MessageText text={text} streaming />
          <span aria-hidden="true" style={{ opacity: 0.6, marginLeft: "1px" }}>
            {"▍"}
          </span>
        </p>
      </div>
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
        className="flex items-center gap-2 rounded-xl px-5 py-3"
        style={{
          backgroundColor: "var(--color-es-asst-bubble)",
          borderBottomLeftRadius: "2px",
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
