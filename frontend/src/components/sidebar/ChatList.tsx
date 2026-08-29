import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  useChats,
  useClearChat,
  useDeleteChat,
  useRenameChat,
} from "@/lib/query/chats";
import { useUiStore } from "@/lib/store/uiStore";
import { parseServerDate } from "@/lib/chat";
import { useStreamRegistry } from "@/lib/chat/streamRegistry";
import { ChatCreateDialog } from "@/components/chats/ChatCreateDialog";
import { AnimatedList, AnimatedListItem } from "@/components/motion/AnimatedList";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { parseApiError } from "@/lib/errors";
import {
  Eraser,
  Loader2,
  Plus,
  MessageCircle,
  MoreHorizontal,
  Pencil,
  Trash2,
} from "lucide-react";
import type { Chat } from "@/lib/schemas/chats";

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

  // Overflow detection for the New Chat dock: its dissolve scrim exists for
  // rows scrolling underneath, so it should only paint when the list really
  // overflows - on a short list the dock stays bare and New Chat sits on the
  // same ground as New Character. ResizeObserver fires once per observe()
  // (initial measurement included); jsdom lacks it, leaving the flat default.
  const scrollRef = useRef<HTMLDivElement>(null);
  const [listOverflows, setListOverflows] = useState(false);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      setListOverflows(el.scrollHeight > el.clientHeight + 1);
    });
    ro.observe(el);
    // Children drive scrollHeight (container size alone misses list growth);
    // re-run on length change keeps new rows observed.
    for (const child of el.children) ro.observe(child);
    return () => ro.disconnect();
  }, [chats.length, isLoading, error]);

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
    <div className="flex h-full min-h-0 flex-col overflow-hidden px-3 py-3">
      {/* Header - the New Chat action now lives at the section foot */}
      <p
        className="mb-2 shrink-0 px-1 text-[11px] font-semibold uppercase tracking-widest"
        style={{ color: "var(--color-es-text-muted)", opacity: 0.75 }}
      >
        Chats
      </p>

      {/* Content scrolls; NOT flex-1, so it hugs the list when short (the
          New Chat dock sits right after the last chat with a void below) and
          only grows to fill/scroll once there are enough chats. */}
      <div ref={scrollRef} className="min-h-0 overflow-y-auto">
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
            className="mt-1 rounded-xl px-3 py-2 text-xs"
            style={{
              backgroundColor: "rgba(195, 106, 114, 0.08)",
              color: "var(--color-es-danger)",
            }}
          >
            {parseApiError(error).message}
          </p>
        )}

        {/* Chat list */}
        {!isLoading && chats.length > 0 && (
          <AnimatedList className="space-y-0.5">
            {chats.map((chat) => {
              const isSelected = selectedChatId === chat.id;
              return (
                <AnimatedListItem key={chat.id}>
                  <ChatListItem
                    chat={chat}
                    isSelected={isSelected}
                    onSelect={() => selectChat(chat.id)}
                  />
                </AnimatedListItem>
              );
            })}
          </AnimatedList>
        )}

        {/* Empty */}
        {!isLoading && !error && chats.length === 0 && (
          <div
            className="mt-1 rounded-xl p-4 text-center text-xs"
            style={{
              backgroundColor: "rgba(255,255,255,0.03)",
              color: "var(--color-es-text-muted)",
            }}
          >
            No chats yet
          </div>
        )}

        {/* New Chat - flows right after the list, then stays stuck to the
            bottom of the scroll area once the list overflows (always
            reachable). The frosted fade lets the scrolling list dissolve
            under it instead of hard-cutting against the button. */}
        <div
          className={`chat-newchat-dock${listOverflows ? " is-stuck" : ""}`}
        >
          <ChatCreateDialog
            trigger={
              <Button
                type="button"
                className="sidebar-primary-action h-9 w-full gap-2 rounded-lg text-xs"
                style={{ color: "var(--color-es-text-light)" }}
              >
                <Plus size={14} />
                New Chat
              </Button>
            }
          />
        </div>
      </div>
    </div>
  );
}

function ChatListItem({
  chat,
  isSelected,
  onSelect,
}: {
  chat: Chat;
  isSelected: boolean;
  onSelect: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmAction, setConfirmAction] = useState<"clear" | "delete" | null>(
    null,
  );

  // Collapsing the sidebar cannot hide what the sidebar opened OUTSIDE itself.
  // This menu renders through a portal into document.body, so it is not a DOM
  // descendant of the panel and `inert` never reaches it: it would sit at
  // stale coordinates, visible and fully focusable, beside a panel that is now
  // zero pixels wide. Its own dismissal is pointer-driven, so a keyboard user
  // who tabs to the handle and presses Enter fires no pointer event and
  // nothing closes it - the same shape as the failure Collapse.tsx is named
  // for, with a longer rope. Closed, not hidden.
  //
  // Render-phase reset, not an effect: the popup has to be gone in the SAME
  // commit the panel shuts, not one commit later.
  const sidebarCollapsed = useUiStore((s) => s.sidebarCollapsed);
  const [prevCollapsed, setPrevCollapsed] = useState(sidebarCollapsed);
  if (sidebarCollapsed !== prevCollapsed) {
    setPrevCollapsed(sidebarCollapsed);
    if (sidebarCollapsed) {
      setMenuOpen(false);
      setConfirmAction(null);
    }
  }
  const [editing, setEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState("");
  const selectedChatId = useUiStore((s) => s.selectedChatId);
  const selectChat = useUiStore((s) => s.selectChat);
  const clearChat = useClearChat();
  const deleteChat = useDeleteChat();
  const renameChat = useRenameChat();
  // Live-stream guard (v1.1 FF1/H7): while this chat is generating, Clear/
  // Delete are disabled - destructive actions on a mid-flight exchange are
  // the ghost-message factory. The registry is module-level, so this row
  // sees streams started by ChatCanvas's hook instance.
  const isStreamingThisChat = useStreamRegistry((s) =>
    s.controllers.has(chat.id),
  );
  const title = chat.title || `Chat #${chat.id}`;
  const isBusy =
    clearChat.isPending || deleteChat.isPending || renameChat.isPending;

  const containerRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const confirmButtonRef = useRef<HTMLButtonElement | null>(null);
  const editInputRef = useRef<HTMLInputElement | null>(null);
  const popupRef = useRef<HTMLDivElement | null>(null);

  // The ⋯ menu/confirm are rendered in a portal with FIXED coordinates so the
  // chat list's overflow clip can never crop them. Positioned from the trigger
  // rect, flipping upward when there isn't room below.
  const [popupPos, setPopupPos] = useState<{
    top?: number;
    bottom?: number;
    right: number;
  } | null>(null);

  const computePopupPos = () => {
    const r = triggerRef.current?.getBoundingClientRect();
    if (!r) return;
    const right = Math.max(8, window.innerWidth - r.right);
    const spaceBelow = window.innerHeight - r.bottom;
    setPopupPos(
      spaceBelow < 210
        ? { bottom: window.innerHeight - r.top + 6, right }
        : { top: r.bottom + 6, right },
    );
  };

  const closeAll = () => {
    setMenuOpen(false);
    setConfirmAction(null);
    setEditing(false);
  };

  const popupOpen = menuOpen || confirmAction != null || editing;

  // A11y: Escape closes the menu/confirm/rename edit and returns focus to the
  // ⋯ trigger; clicking outside closes without stealing focus from the click
  // target (the rename input additionally cancels itself on blur).
  // TODO: arrow-key navigation between menu items (out of scope for now).
  useEffect(() => {
    if (!popupOpen) return;

    const handlePointerDown = (event: PointerEvent) => {
      const container = containerRef.current;
      const popup = popupRef.current;
      if (
        event.target instanceof Node &&
        !(container && container.contains(event.target)) &&
        !(popup && popup.contains(event.target))
      ) {
        // While renaming, DON'T closeAll here - it would unmount the input
        // before its onBlur commits the typed title (v1.1 FF11). The input's
        // blur fires from this same outside click and does the commit itself.
        // (`editing` is in this effect's deps so the closure is never stale.)
        if (editing) return;
        closeAll();
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeAll();
        triggerRef.current?.focus();
      }
    };
    // The popup is fixed-positioned from a one-time trigger rect; any scroll or
    // resize would detach it, so close instead of chasing the anchor. ONLY the
    // portaled surfaces though - the inline rename input scrolls WITH the row,
    // and closing it here would silently discard a rename mid-typing on the
    // first trackpad tick.
    const handleReflow = () => {
      setMenuOpen(false);
      setConfirmAction(null);
    };

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    window.addEventListener("scroll", handleReflow, true);
    window.addEventListener("resize", handleReflow);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("scroll", handleReflow, true);
      window.removeEventListener("resize", handleReflow);
    };
    // `editing` included so the outside-click handler sees the live rename
    // state (popupOpen alone stays true across the menu->rename transition).
  }, [popupOpen, editing]);

  // Destructive inline confirm: focus the confirm button when it appears.
  useEffect(() => {
    if (confirmAction != null) {
      confirmButtonRef.current?.focus();
    }
  }, [confirmAction]);

  // Inline rename: focus the input with its text selected when it appears.
  useEffect(() => {
    if (editing) {
      editInputRef.current?.focus();
      editInputRef.current?.select();
    }
  }, [editing]);

  const closeAndRefocusTrigger = () => {
    closeAll();
    triggerRef.current?.focus();
  };

  const startEditing = () => {
    setMenuOpen(false);
    setConfirmAction(null);
    setDraftTitle(title);
    setEditing(true);
  };

  // Escape must still CANCEL even though blur now commits (v1.1 FF11 + H10):
  // the flag is set before the Escape-driven blur so the single blur-commit
  // path can distinguish "cancelled" from "committed".
  const escapeCancelRef = useRef(false);

  const commitRename = () => {
    const trimmed = draftTitle.trim();
    setEditing(false);
    triggerRef.current?.focus();
    // Empty or unchanged → cancel silently (no request)
    if (trimmed.length === 0 || trimmed === title) return;
    renameChat.mutate({ chatId: chat.id, title: trimmed });
  };

  // Single commit path: Enter/blur commit, Escape cancels. Both keys blur the
  // input so onBlur is the ONLY place that decides commit-vs-cancel (avoids a
  // double mutate from Enter-then-blur).
  const handleEditBlur = () => {
    if (escapeCancelRef.current) {
      escapeCancelRef.current = false;
      setEditing(false);
      triggerRef.current?.focus();
      return;
    }
    commitRename();
  };

  const handleEditKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      event.currentTarget.blur();
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation(); // don't let the document handler also fire
      escapeCancelRef.current = true;
      event.currentTarget.blur();
    }
  };

  const handleConfirm = () => {
    if (confirmAction === "clear") {
      clearChat.mutate(chat.id, {
        onSuccess: closeAndRefocusTrigger,
      });
      return;
    }

    if (confirmAction === "delete") {
      deleteChat.mutate(chat.id, {
        onSuccess: () => {
          if (selectedChatId === chat.id) {
            selectChat(null);
          }
          // The row unmounts after delete - the optional chain makes the
          // focus return a no-op in that case.
          closeAndRefocusTrigger();
        },
      });
    }
  };

  return (
    <div
      ref={containerRef}
      className={`chat-list-item sidebar-item ${
        isSelected ? "sidebar-item-selected" : "sidebar-item-unselected"
      }`}
    >
      {editing ? (
        <div className="chat-list-select flex items-center">
          <input
            ref={editInputRef}
            type="text"
            value={draftTitle}
            onChange={(event) => setDraftTitle(event.target.value)}
            onKeyDown={handleEditKeyDown}
            onBlur={handleEditBlur}
            aria-label={`Rename chat ${title}`}
            className="w-full rounded-lg px-2 py-1 text-sm outline-none"
            style={{
              backgroundColor: "rgba(200, 216, 236, 0.07)",
              border: "1px solid rgba(200, 216, 236, 0.20)",
              color: "var(--color-es-text-light)",
            }}
            disabled={isBusy}
          />
        </div>
      ) : (
        <button
          type="button"
          onClick={() => {
            closeAll();
            onSelect();
          }}
          className="chat-list-select"
          aria-label={`Select chat ${title}`}
          aria-pressed={isSelected}
        >
          <div className="flex items-center gap-2">
            <MessageCircle
              size={11}
              style={{
                color: isSelected
                  ? "rgba(226, 234, 243, 0.88)"
                  : "var(--color-es-text-muted)",
                opacity: isSelected ? 1 : 0.7,
              }}
            />
            <span
              className="truncate text-sm"
              style={{ color: "var(--color-es-text-light)" }}
            >
              {title}
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
              {parseServerDate(chat.updated_at).toLocaleDateString()}
            </span>
          </div>
        </button>
      )}

      <button
        ref={triggerRef}
        type="button"
        className="chat-action-trigger"
        aria-label={`Open chat actions for ${title}`}
        aria-haspopup="menu"
        aria-expanded={menuOpen || confirmAction != null}
        onClick={() => {
          setConfirmAction(null);
          setEditing(false);
          if (!menuOpen) computePopupPos();
          setMenuOpen((open) => !open);
        }}
        disabled={isBusy}
      >
        {isBusy ? <Loader2 size={13} className="animate-spin" /> : <MoreHorizontal size={14} />}
      </button>

      {menuOpen && popupPos && createPortal(
        <div
          ref={popupRef}
          className="chat-action-menu"
          role="menu"
          style={{
            position: "fixed",
            top: popupPos.top,
            bottom: popupPos.bottom,
            right: popupPos.right,
            zIndex: 40,
          }}
        >
          <button
            type="button"
            role="menuitem"
            className="chat-action-menu-item"
            onClick={startEditing}
          >
            <Pencil size={13} />
            Rename
          </button>
          <button
            type="button"
            role="menuitem"
            className="chat-action-menu-item"
            disabled={isStreamingThisChat}
            title={
              isStreamingThisChat
                ? "Wait for the reply to finish (or stop it) first"
                : undefined
            }
            onClick={() => {
              setMenuOpen(false);
              setConfirmAction("clear");
            }}
          >
            <Eraser size={13} />
            Clear chat
          </button>
          <button
            type="button"
            role="menuitem"
            className="chat-action-menu-item is-danger"
            disabled={isStreamingThisChat}
            title={
              isStreamingThisChat
                ? "Wait for the reply to finish (or stop it) first"
                : undefined
            }
            onClick={() => {
              setMenuOpen(false);
              setConfirmAction("delete");
            }}
          >
            <Trash2 size={13} />
            Delete chat
          </button>
        </div>,
        document.body,
      )}

      {confirmAction && popupPos && createPortal(
        <div
          ref={popupRef}
          className="chat-action-confirm"
          role="dialog"
          aria-label={confirmAction === "clear" ? "Confirm clear chat" : "Confirm delete chat"}
          style={{
            position: "fixed",
            top: popupPos.top,
            bottom: popupPos.bottom,
            right: popupPos.right,
            zIndex: 40,
          }}
        >
          <p>
            {confirmAction === "clear"
              ? "Clear all messages in this chat?"
              : "Delete this chat permanently?"}
          </p>
          <div className="mt-2 flex justify-end gap-1.5">
            <button
              type="button"
              className="inline-confirm-button"
              onClick={closeAndRefocusTrigger}
              disabled={isBusy}
            >
              Cancel
            </button>
            <button
              ref={confirmButtonRef}
              type="button"
              className="inline-confirm-button is-danger"
              onClick={handleConfirm}
              disabled={isBusy}
            >
              {isBusy ? "Working..." : confirmAction === "clear" ? "Clear" : "Delete"}
            </button>
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
