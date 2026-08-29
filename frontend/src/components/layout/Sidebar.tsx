import { useState } from "react";
import { PanelMist } from "@/components/backdrop/MistCanvas";
import { SidebarHeader } from "@/components/sidebar/SidebarHeader";
import { SidebarFooter } from "@/components/sidebar/SidebarFooter";
import { PersonaStrip } from "@/components/sidebar/PersonaStrip";
import { SidebarSearch } from "@/components/sidebar/SidebarSearch";
import { CharacterList } from "@/components/sidebar/CharacterList";
import { ChatList } from "@/components/sidebar/ChatList";
import { Separator } from "@/components/ui/separator";
import { useUiStore } from "@/lib/store/uiStore";

export function Sidebar() {
  // Character search query - lifted here so the SEARCH input and the character
  // list (its two sibling sections) share it. Transient: never persisted.
  const [characterQuery, setCharacterQuery] = useState("");
  const collapsed = useUiStore((s) => s.sidebarCollapsed);

  return (
    <aside
      id="es-sidebar"
      // Two asides on this page both map to role complementary. Unnamed, a
      // screen reader's landmark list reads "complementary, complementary,
      // main" and there is no way to tell the chat list from the model panel
      // without entering each one.
      aria-label="Chats and characters"
      className="glass-dark es-side-panel flex h-full flex-col"
      data-collapsed={collapsed}
      // On the ASIDE, not on the inner wrapper. With them one level down the
      // landmark itself survived the collapse, so focus mode left an empty
      // complementary region for a screen reader to walk into.
      inert={collapsed || undefined}
      aria-hidden={collapsed || undefined}
      style={{
        width: collapsed ? "0px" : "var(--sidebar-width)",
        minWidth: collapsed ? "0px" : "var(--sidebar-width)",
        // `1px solid transparent` still OCCUPIES a pixel, and the glass
        // background paints into it - that pixel was the dark strip left
        // beside the canvas when the panel was shut. Zero width, not a
        // transparent colour.
        borderRight: collapsed
          ? "0px solid transparent"
          : "1px solid var(--color-es-glass-border-dark)",
        boxShadow: collapsed ? "none" : "var(--shadow-panel)",
      }}
    >
      {/* Content stays mounted (so scroll position and search text survive a
          toggle) but goes inert while collapsed - clipped is not the same as
          gone, see Collapse.tsx's own note on this exact bug shape. */}
      {/* OUTSIDE the wrapper, deliberately. `.glass-dark > *` gives every
          direct child `position: relative; z-index: 1`, so a wrapper here
          becomes a stacking context and traps the mist's `z-index: -1` inside
          it - the veil then paints ABOVE the ambient glow and the right-edge
          sheen instead of below them, and the sidebar quietly loses both.
          The mist is decorative and holds no focusable node, so it does not
          need the inert treatment the content below it does. */}
      <PanelMist side="left" />
      <div className="es-side-panel-inner flex h-full min-w-[var(--sidebar-width)] flex-col">
        <SidebarHeader />

        <Separator style={{ opacity: 0.15 }} />

        <PersonaStrip />
        <SidebarSearch value={characterQuery} onChange={setCharacterQuery} />

        <Separator style={{ opacity: 0.15 }} />

        <div className="max-h-[40%] shrink-0 overflow-hidden">
          <CharacterList query={characterQuery} />
        </div>

        <Separator style={{ opacity: 0.15 }} />

        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <ChatList />
        </div>

        <Separator style={{ opacity: 0.15 }} />

        <SidebarFooter />
      </div>
    </aside>
  );
}
