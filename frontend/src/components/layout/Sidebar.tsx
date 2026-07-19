import { useState } from "react";
import { PanelMist } from "@/components/backdrop/MistCanvas";
import { SidebarHeader } from "@/components/sidebar/SidebarHeader";
import { SidebarFooter } from "@/components/sidebar/SidebarFooter";
import { PersonaStrip } from "@/components/sidebar/PersonaStrip";
import { SidebarSearch } from "@/components/sidebar/SidebarSearch";
import { CharacterList } from "@/components/sidebar/CharacterList";
import { ChatList } from "@/components/sidebar/ChatList";
import { Separator } from "@/components/ui/separator";

export function Sidebar() {
  // Character search query - lifted here so the SEARCH input and the character
  // list (its two sibling sections) share it. Transient: never persisted.
  const [characterQuery, setCharacterQuery] = useState("");

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
      <PanelMist side="left" />
      <SidebarHeader />

      <Separator style={{ opacity: 0.15 }} />

      {/* Identity anchor + search - the freed top strip. */}
      <PersonaStrip />
      <SidebarSearch value={characterQuery} onChange={setCharacterQuery} />

      <Separator style={{ opacity: 0.15 }} />

      {/* Characters - max 40% of available space; new/import pinned at its foot */}
      <div className="max-h-[40%] shrink-0 overflow-hidden">
        <CharacterList query={characterQuery} />
      </div>

      <Separator style={{ opacity: 0.15 }} />

      {/* Chats - takes remaining space; New Chat pinned at its foot */}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <ChatList />
      </div>

      <Separator style={{ opacity: 0.15 }} />

      <SidebarFooter />
    </aside>
  );
}
