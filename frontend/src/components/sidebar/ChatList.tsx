import { useChats } from "@/lib/query/chats";
import { useUiStore } from "@/lib/store/uiStore";
import { ChatCreateDialog } from "@/components/chats/ChatCreateDialog";
import { AnimatedList, AnimatedListItem } from "@/components/motion/AnimatedList";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { isApiError } from "@/lib/api/client";
import { MessageSquarePlus, MessageCircle } from "lucide-react";

export function ChatList() {
  const selectedCharacterId = useUiStore((s) => s.selectedCharacterId);
  const selectedChatId = useUiStore((s) => s.selectedChatId);
  const selectChat = useUiStore((s) => s.selectChat);
  const { data: allChats, isLoading, error } = useChats();

  // Filter chats by selected character
  const chats =
    selectedCharacterId != null && allChats
      ? allChats.filter((c) => c.character_id === selectedCharacterId)
      : [];

  // No character selected
  if (selectedCharacterId == null) {
    return (
      <div className="px-3 py-3">
        <p
          className="px-1 text-[11px] font-semibold uppercase tracking-widest"
          style={{ color: "var(--color-es-text-muted)", opacity: 0.75 }}
        >
          Chats
        </p>
        <div
          className="mt-2 rounded-xl p-4 text-center text-xs"
          style={{
            backgroundColor: "rgba(255,255,255,0.03)",
            color: "var(--color-es-text-muted)",
          }}
        >
          Select a character first
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col overflow-hidden px-3 py-3">
      {/* Header */}
      <div className="flex items-center justify-between px-1 mb-2">
        <p
          className="text-[11px] font-semibold uppercase tracking-widest"
          style={{ color: "var(--color-es-text-muted)", opacity: 0.75 }}
        >
          Chats
        </p>
        <ChatCreateDialog
          trigger={
            <Button
              variant="ghost"
              size="sm"
              className="h-6 w-6 p-0 opacity-60 hover:opacity-100"
              aria-label="New chat"
            >
              <MessageSquarePlus size={12} />
            </Button>
          }
        />
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="space-y-1">
          {Array.from({ length: 2 }).map((_, i) => (
            <Skeleton
              key={i}
              className="h-10 w-full rounded-xl"
              style={{ backgroundColor: "rgba(255,255,255,0.04)" }}
            />
          ))}
        </div>
      )}

      {/* Error */}
      {error && (
        <p
          className="mt-2 rounded-xl px-3 py-2 text-xs"
          style={{
            backgroundColor: "rgba(201, 110, 91, 0.08)",
            color: "var(--color-es-danger)",
          }}
        >
          {isApiError(error) ? error.detail : "Failed to load chats"}
        </p>
      )}

      {/* Chat list */}
      {!isLoading && chats.length > 0 && (
        <div className="flex-1 overflow-y-auto">
          <AnimatedList className="space-y-0.5">
            {chats.map((chat) => {
              const isSelected = selectedChatId === chat.id;
              return (
                <AnimatedListItem key={chat.id}>
                  <button
                    type="button"
                    onClick={() => selectChat(chat.id)}
                    className={`sidebar-item w-full rounded-xl px-3 py-2 text-left ${
                      isSelected ? "sidebar-item-selected" : "sidebar-item-unselected"
                    }`}
                    aria-label={`Select chat ${chat.title ?? `Chat #${chat.id}`}`}
                    aria-pressed={isSelected}
                  >
                    <div className="flex items-center gap-2">
                      <MessageCircle
                        size={11}
                        style={{
                          color: isSelected
                            ? "rgba(228, 234, 224, 0.88)"
                            : "var(--color-es-text-muted)",
                          opacity: isSelected ? 1 : 0.7,
                        }}
                      />
                      <span
                        className="truncate text-sm"
                        style={{ color: "var(--color-es-text-light)" }}
                      >
                        {chat.title || `Chat #${chat.id}`}
                      </span>
                    </div>
                    <div className="mt-0.5 flex items-center gap-2 pl-[18px]">
                      <span
                        className="text-[10px]"
                        style={{ color: "var(--color-es-text-muted)", opacity: 0.65 }}
                      >
                        {chat.message_count} msg
                      </span>
                      <span
                        className="text-[10px]"
                        style={{ color: "var(--color-es-text-muted)", opacity: 0.65 }}
                      >
                        {new Date(chat.updated_at).toLocaleDateString()}
                      </span>
                    </div>
                  </button>
                </AnimatedListItem>
              );
            })}
          </AnimatedList>
        </div>
      )}

      {/* Empty */}
      {!isLoading && !error && chats.length === 0 && (
        <div
          className="mt-2 rounded-xl p-4 text-center text-xs"
          style={{
            backgroundColor: "rgba(255,255,255,0.03)",
            color: "var(--color-es-text-muted)",
          }}
        >
          No chats yet
        </div>
      )}
    </div>
  );
}
