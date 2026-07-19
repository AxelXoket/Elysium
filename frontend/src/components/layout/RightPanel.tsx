import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PanelMist } from "@/components/backdrop/MistCanvas";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useUiStore } from "@/lib/store/uiStore";
import { ModelPanel } from "@/components/models/ModelPanel";
import { SettingsPanel } from "@/components/settings/SettingsPanel";
import { PersonaPanel } from "@/components/persona/PersonaPanel";

/**
 * RightPanel - Phase 6E-A restructure.
 *
 * Tabs changed from "model | info | settings" → "models | secrets | persona".
 * Old "Info" tab (character/chat summary) has been removed. Its content was
 * minimal read-only display. Character info remains visible in the sidebar.
 * The Info tab removal is an intentional UX simplification.
 *
 * Tab migration: old persisted values are normalized in uiStore (version 2 migrate).
 */
export function RightPanel() {
  const activeTab = useUiStore((s) => s.activeRightPanelTab);
  const setActiveTab = useUiStore((s) => s.setActiveRightPanelTab);

  return (
    <aside
      className="glass-right relative flex h-full flex-col border-l"
      style={{
        width: "var(--right-panel-width)",
        minWidth: "var(--right-panel-width)",
        borderColor: "var(--color-es-glass-border-warm)",
        boxShadow: "var(--shadow-panel)",
      }}
    >
      <PanelMist side="right" />
      <Tabs
        value={activeTab}
        onValueChange={(v) =>
          setActiveTab(v as "models" | "secrets" | "persona")
        }
        className="flex h-full flex-col"
      >
        {/* Tab strip - soft pill style */}
        <TabsList
          className="mx-3 mt-3 grid h-9 w-auto grid-cols-3 items-center rounded-xl px-1"
          style={{
            backgroundColor: "rgba(28, 38, 50, 0.06)",
            border: "1px solid var(--color-es-glass-border-dark)",
          }}
        >
          <TabsTrigger value="models">Models</TabsTrigger>
          <TabsTrigger value="secrets">Secrets</TabsTrigger>
          <TabsTrigger value="persona">Persona</TabsTrigger>
        </TabsList>

        {/* Panels remount per switch ON PURPOSE: the model-list cascade is a
            loved part of the tab's feel and replays on mount. The switch
            stutter was NOT the remount itself - it was the cascade animating
            all 237 rows (~10s of scheduled tweens starving the fog's rAF);
            ModelPanel now only animates the rows that can be seen entering. */}

        {/* Models tab */}
        <TabsContent value="models" className="flex-1 overflow-hidden">
          <ScrollArea className="h-full">
            <ModelPanel />
          </ScrollArea>
        </TabsContent>

        {/* Secrets tab - existing SettingsPanel, visually reframed */}
        <TabsContent value="secrets" className="flex-1 overflow-hidden">
          <ScrollArea className="h-full">
            <SettingsPanel />
          </ScrollArea>
        </TabsContent>

        {/* Persona tab - shell only in Phase 6E-A, no persistence */}
        <TabsContent value="persona" className="flex-1 overflow-hidden">
          <PersonaPanel />
        </TabsContent>
      </Tabs>
    </aside>
  );
}
