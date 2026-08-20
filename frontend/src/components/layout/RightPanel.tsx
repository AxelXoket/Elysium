import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PanelMist } from "@/components/backdrop/MistCanvas";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useUiStore } from "@/lib/store/uiStore";
import { ModelPanel } from "@/components/models/ModelPanel";
import { SettingsPanel } from "@/components/settings/SettingsPanel";
import { ExtractionSettings } from "@/components/notebook/ExtractionSettings";
import { WorkerPanel } from "@/components/notebook/WorkerPanel";
import { BoundaryPanel } from "@/components/notebook/BoundaryPanel";
import { Separator } from "@/components/ui/separator";
import { NotebookPanel } from "@/components/notebook/NotebookPanel";
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
          setActiveTab(v as "models" | "secrets" | "persona" | "notebook")
        }
        className="flex h-full flex-col"
      >
        {/* Tab strip - soft pill style */}
        <TabsList
          className="mx-3 mt-3 grid h-9 w-auto grid-cols-4 items-center rounded-xl px-1"
          style={{
            backgroundColor: "rgba(28, 38, 50, 0.06)",
            border: "1px solid var(--color-es-glass-border-dark)",
          }}
        >
          {/* text-xs on each TRIGGER, not on the list. The shared trigger
              hardcodes text-sm in its cva base, which beats a class on the
              parent - so at the panel's 300px floor a fourth column leaves
              about 52px of label space for a 14px "Security" that needs 57,
              and `whitespace-nowrap` with no truncate means it runs into its
              neighbour rather than eliding. */}
          <TabsTrigger value="models" className="text-xs">Models</TabsTrigger>
          {/* LABEL only. The stored value stays "secrets": renaming it would
              cost a persist version bump and a normalizeTab entry, and buy
              nothing - the panel now holds the switches that protect the
              secrets as well as the secrets themselves. */}
          <TabsTrigger value="secrets" className="text-xs">Security</TabsTrigger>
          <TabsTrigger value="persona" className="text-xs">Persona</TabsTrigger>
          <TabsTrigger value="notebook" className="text-xs">Notes</TabsTrigger>
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

        {/* Four sibling panels with nothing between them read as one
            column: four peer h4 headings were the only landmarks across
            roughly 1600px of scroll, and there was no signal at all for
            where one idea ended. At full token strength rather than
            SettingsPanel's opacity-15: --border inside .glass-right is
            rgba(30,45,62,0.16), and multiplying that by 0.15 lands at about
            0.024 alpha, which is five values of 255 away from the panel it
            sits on. A landmark nobody can see is not a landmark. The
            sub-section rules inside SettingsPanel can stay faint because
            they divide parts of one idea; these divide four features. */}
        <TabsContent value="notebook" className="flex-1 overflow-y-auto">
          <NotebookPanel />
          <Separator />
          <BoundaryPanel />
          <Separator />
          <ExtractionSettings />
          <Separator />
          <WorkerPanel />
        </TabsContent>
      </Tabs>
    </aside>
  );
}
