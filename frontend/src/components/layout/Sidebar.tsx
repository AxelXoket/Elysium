import { SidebarHeader } from "@/components/sidebar/SidebarHeader";
import { SidebarFooter } from "@/components/sidebar/SidebarFooter";
import { CharacterList } from "@/components/sidebar/CharacterList";
import { ChatList } from "@/components/sidebar/ChatList";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/button";
import { ChatCreateDialog } from "@/components/chats/ChatCreateDialog";
import { Plus } from "lucide-react";

export function Sidebar() {
  return (
    <aside
      className="glass-dark flex h-full flex-col"
      style={{
        width: "var(--sidebar-width)",
        minWidth: "var(--sidebar-width)",
        borderRight: "1px solid var(--color-es-glass-border-dark)",
        boxShadow: "var(--shadow-panel)",
      }}
    >
      <SidebarHeader />

      <Separator style={{ opacity: 0.15 }} />

      <div className="px-5 py-4">
        <ChatCreateDialog
          trigger={
            <Button
              type="button"
              className="sidebar-primary-action h-10 w-full gap-2 rounded-lg text-sm"
              style={{
                color: "var(--color-es-text-light)",
              }}
            >
              <Plus size={15} />
              New Chat
            </Button>
          }
        />
      </div>

      {/* Characters — max 40% of available space */}
      <div className="max-h-[40%] shrink-0 overflow-hidden">
        <CharacterList />
      </div>

      <Separator style={{ opacity: 0.15 }} />

      {/* Chats — takes remaining space */}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <ChatList />
      </div>

      <Separator style={{ opacity: 0.15 }} />

      <SidebarFooter />
    </aside>
  );
}
