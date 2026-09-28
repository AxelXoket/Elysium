import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PanelMist } from "@/components/backdrop/MistCanvas";
import { useUiStore } from "@/lib/store/uiStore";
import { ModelPanel } from "@/components/models/ModelPanel";
import { SettingsPanel } from "@/components/settings/SettingsPanel";
import { NotesTab } from "@/components/notebook/NotesTab";
import { notesTabName } from "@/components/notebook/notesTabProse";
import {
  useNotesFollowRuns,
  useNotesToReview,
  useNotesViewStore,
} from "@/components/notebook/notesTabState";
import { useLayoutEffect, useRef } from "react";
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
  const collapsed = useUiStore((s) => s.rightPanelCollapsed);
  const chatId = useUiStore((s) => s.selectedChatId);
  // The Notes tab says, from any tab, that something waits there.
  // The refresh that lets it notice a note written in the background is
  // owned here, by the frame, because the panels under it come and go.
  useNotesFollowRuns(chatId);
  const toReview = useNotesToReview(chatId);

  // THE KEYBOARD HAND-OFF. A "Not read" line's button
  // asks for the control that fixes the problem: the API key field or the
  // proxy on the Security tab, the note model picker, a Try again, the
  // worker card. Served here, the one parent of every tab and view, in a
  // layout effect: the pressed button is hidden or unmounted by the same
  // commit, and a later effect would leave the keyboard on the page for a
  // frame. Served once the tab it was meant for is the open one. A target
  // inside a hidden view or an inert (shut) Collapse cannot take focus and
  // is skipped; with none left, the open view's own tab takes it, never
  // nothing.
  const focusRequest = useNotesViewStore((s) => s.focusTarget);
  const notesView = useNotesViewStore((s) => s.view);
  const asideRef = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    if (!focusRequest || focusRequest.tab !== activeTab) return;
    useNotesViewStore.getState().clearFocusTarget();
    const root = asideRef.current;
    if (!root) return;
    const el = [...root.querySelectorAll<HTMLElement>(
      `[data-notes-target="${focusRequest.target}"]`)]
      .find((e) => !e.closest("[hidden], [inert]") && !(e as HTMLButtonElement).disabled);
    el?.focus();
    if (el && document.activeElement === el) {
      // jsdom has no scrollIntoView; a browser always does.
      if (typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "nearest" });
      return;
    }
    const open = root.querySelectorAll<HTMLElement>('[role="tab"][aria-selected="true"]');
    open[open.length - 1]?.focus();
  }, [focusRequest, activeTab, notesView]);

  return (
    <aside
      className={`glass-right es-side-panel relative flex h-full flex-col${
        collapsed ? "" : " border-l"
      }`}
      ref={asideRef}
      id="es-right-panel"
      aria-label="Models, security and notes"
      data-collapsed={collapsed}
      inert={collapsed || undefined}
      aria-hidden={collapsed || undefined}
      style={{
        width: collapsed ? "0px" : "var(--right-panel-width)",
        minWidth: collapsed ? "0px" : "var(--right-panel-width)",
        // Same pixel as the sidebar's: a transparent border is still a
        // border. The border-l class itself is dropped while collapsed.
        borderColor: "var(--color-es-glass-border-warm)",
        boxShadow: collapsed ? "none" : "var(--shadow-panel)",
      }}
    >
      {/* Same rule as the sidebar: clipped is not gone, so the whole body goes
          inert while collapsed rather than staying tabbable behind a zero
          width. The Tabs body below is deliberately left at its original
          indentation - re-indenting it would bury this change in noise. */}
      {/* Outside the wrapper for the same reason as the sidebar's: a direct
          child of the glass surface owns the stacking context the mist's
          negative z-index depends on. */}
      <PanelMist side="right" />
      <div className="es-side-panel-inner flex h-full min-w-[var(--right-panel-width)] flex-col">
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
          {/* text-label on each TRIGGER, not on the list. The shared trigger
              writes text-control into its own base string, which beats a
              class on the parent - so at the panel's 300px floor a fourth
              column leaves about 52px of label space for a 14px "Security"
              that needs 57, and `whitespace-nowrap` with no truncate means it
              runs into its neighbour rather than eliding. */}
          <TabsTrigger value="models" className="text-label">Models</TabsTrigger>
          {/* LABEL only. The stored value stays "secrets": renaming it would
              cost a persist version bump and a normalizeTab entry, and buy
              nothing - the panel now holds the switches that protect the
              secrets as well as the secrets themselves. */}
          <TabsTrigger value="secrets" className="text-label">Security</TabsTrigger>
          <TabsTrigger value="persona" className="text-label">Persona</TabsTrigger>
          {/* The count: model notes not yet seen plus suggestions waiting,
              in this chat. None when there are none. Its name keeps the
              visible "Notes 3" whole, then says what the number is. */}
          <TabsTrigger
            value="notebook"
            className="gap-1 text-label"
            aria-label={toReview > 0 ? notesTabName(toReview) : undefined}
            data-testid="notes-tab-trigger"
          >
            Notes
            {toReview > 0 && (
              <span className="notes-count-badge" data-testid="notes-tab-count">
                {toReview}
              </span>
            )}
          </TabsTrigger>
        </TabsList>

        {/* Panels remount per switch ON PURPOSE: the model-list cascade is a
            loved part of the tab's feel and replays on mount. The switch
            stutter was NOT the remount itself - it was the cascade animating
            all 237 rows (~10s of scheduled tweens starving the fog's rAF);
            ModelPanel now only animates the rows that can be seen entering.

            READ THE SECOND HALF BEFORE COPYING THE FIRST. What the paragraph
            above left out for a long time is that a remount does not only
            replay an animation - it destroys everything the panel was
            holding, and the panels hold text somebody typed. Clicking a
            neighbouring tab silently threw away a half-written persona
            description, a half-written note, a half-typed limit. No warning,
            no undo, and the sentence above read as though the trade had been
            considered when only one side of it had.

            The trade is now taken on the other axis. The panels still
            unmount, so the cascade still replays and nothing runs behind a
            hidden tab; the text no longer lives in the panels. Each box
            writes through `usePanelDraft` into the same module-scope store
            that already holds unsent composer and edit text
            (lib/store/draftStore.ts), so it survives the unmount the same way
            those do and dies with the process the same way too. base-ui's
            `keepMounted` was the other candidate and would have cost the
            cascade plus four tabs' worth of live queries.

            FIVE controls in the Security tab are deliberately NOT covered and
            still lose their contents here: the API key box, the three vault
            passphrase boxes, and the proxy URL. The store outlives a vault
            lock by design, and a credential must not - and a URL counts,
            because it is allowed to carry a user and a password inside it.
            That exclusion is argued in full in the draft store's header. */}

        {/* All four tabs scroll on the tab body itself. Two of them handed
            that job to a ScrollArea, which builds a viewport of its own and
            draws its own bar inside it. The sheet already gives every
            scroller in the app one appearance by name, and it reached that
            viewport too, so the panel was carrying a library bar and a
            native one at once and looked different depending on which tab
            was open. A wrapper that replaces the scrolling element is also
            what reading-place logic like lib/layout/holdReaderPlace.ts holds
            on to, which is the reason the direction here is native rather
            than the other way round. */}
        {/* Models tab */}
        <TabsContent value="models" className="flex-1 overflow-y-auto">
          <ModelPanel />
        </TabsContent>

        {/* Secrets tab - existing SettingsPanel, visually reframed */}
        <TabsContent value="secrets" className="flex-1 overflow-y-auto">
          <SettingsPanel />
        </TabsContent>

        {/* Persona tab - shell only in Phase 6E-A, no persistence */}
        <TabsContent value="persona" className="flex-1 overflow-hidden">
          <PersonaPanel />
        </TabsContent>

        {/* The Notes tab holds three views of its own (NotesTab.tsx): the
            notes, the limits, and the settings that write notes. They were
            four panels in one scroll of up to 6197 px. */}
        <TabsContent value="notebook" className="flex-1 overflow-y-auto">
          <NotesTab />
        </TabsContent>
      </Tabs>
      </div>
    </aside>
  );
}
