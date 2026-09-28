/**
 * NotesTab - the Notes tab as three views: Notebook | Limits | Settings.
 *
 * WHY VIEWS AND NOT FOUR SECTIONS IN ONE SCROLL. The tab carried
 * four features in one column: the notes (daily), the limits, the note
 * model and the background reader (rare). With thirty notes the limits
 * started 3545 px down, reachable only past every note, and the reason a
 * chat was not being read sat at the very bottom. The leading chat
 * assistants all keep the memory list on one screen and its settings on
 * another; collapsing the settings in place would still leave them under
 * the list, so the list's length would still decide how far away a limit
 * is. Here every view starts at the top of the tab, one press away.
 *
 * The first view is "Notebook", not "Notes": the panel tab it sits in is
 * "Notes", and two tabs with one name (and one count) were one too many.
 * The count of what waits is on the panel's Notes tab only.
 *
 * NOTHING THAT NEEDS THE READER IS HIDDEN BY IT. The first line of the Notes
 * view says whether this chat is being read, and a view that holds
 * something to act on (a reason this chat is not read, a setting that could
 * not be loaded) says so in its tab's name and with a dot beside its word.
 *
 * The house tabs (base-ui, the WAI-ARIA tabs pattern: arrow keys move
 * between tabs, one tab stop, each panel named by its tab), in their `line`
 * variant so the row reads as a level below the pill strip above it.
 */
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Separator } from "@/components/ui/separator";
import { NotebookPanel } from "@/components/notebook/NotebookPanel";
import { BoundaryPanel } from "@/components/notebook/BoundaryPanel";
import { ExtractionSettings } from "@/components/notebook/ExtractionSettings";
import { WorkerPanel } from "@/components/notebook/WorkerPanel";
import {
  NOTES_VIEW_WORDS,
  viewName,
  type NotesView,
} from "@/components/notebook/notesTabProse";
import {
  useChatRead,
  useNotesViewStore,
  useViewsNeedingLook,
} from "@/components/notebook/notesTabState";
import { useUiStore } from "@/lib/store/uiStore";

const VIEWS: readonly NotesView[] = ["notes", "limits", "settings"];

export function NotesTab() {
  const chatId = useUiStore((s) => s.selectedChatId);
  const view = useNotesViewStore((s) => s.view);
  const setView = useNotesViewStore((s) => s.setView);
  const needsLook = useViewsNeedingLook(chatId);
  const read = useChatRead(chatId);
  const alarm = read.kind === "not" || read.kind === "test";
  // A hand-off to a control in another view (a "Not read" line's button) is
  // served by RightPanel, the one parent of every tab and view.

  return (
    <Tabs
      value={view}
      onValueChange={(v) => setView(v as NotesView)}
      className="gap-0"
    >
      <TabsList
        variant="line"
        aria-label={NOTES_VIEW_WORDS.list}
        className="mx-4 mt-3 w-auto justify-start gap-4 px-0"
      >
        {VIEWS.map((v) => (
          <TabsTrigger
            key={v}
            value={v}
            aria-label={needsLook[v] ? viewName(v, true) : undefined}
            data-needs-look={needsLook[v] || undefined}
            // The chrome step, like every button's words inside a panel
            // (IP-05); the pill strip above keeps its own smaller step.
            className="flex-none gap-1.5 px-1 text-ui"
          >
            {NOTES_VIEW_WORDS[v]}
            {needsLook[v] && (
              <span
                aria-hidden="true"
                data-testid={`notes-view-${v}-dot`}
                className="inline-block size-1.5 shrink-0 rounded-badge"
                style={{ background: "var(--es-island-danger-ink)" }}
              />
            )}
          </TabsTrigger>
        ))}
      </TabsList>

      {/* NOT A TAB STOP of their own (tabIndex -1): each view opens on
          controls, so Tab goes from its tab straight to the first of them,
          as the WAI-ARIA pattern allows for a panel with focusable content.
          A stop there drew a ring on the whole view (1.96:1).

          ALL THREE KEPT MOUNTED, hidden (the `hidden` attribute: out of
          sight, out of the tab order and the accessibility tree) while
          another view is open. A view holds work in progress that a remount
          would lose or send: a discarded suggestion is HELD with its Undo
          until the note list leaves the chat or unmounts (then the deletes
          are sent), and a delete question, an open disclosure or the
          reading preview are state too. Looking at the limits must not be
          what sends a discard, and coming back must find things as they
          were. Only leaving the tab or the chat does that, as before. */}
      <TabsContent value="notes" keepMounted tabIndex={-1}>
        <NotebookPanel />
      </TabsContent>
      <TabsContent value="limits" keepMounted tabIndex={-1}>
        <BoundaryPanel />
      </TabsContent>
      {/* The note model and the background reader: how notes get written.
          Two features, so the landmark between them stays. */}
      <TabsContent value="settings" keepMounted tabIndex={-1}>
        {/* The reason this chat is not read, first, in the words and ink of
            the Notes view's line: "Open Settings" lands here, and the card
            below opens on the reader's own state ("Running."), which is
            true of the other chats and must not stand alone over this one. */}
        {alarm && (
          <p
            className="px-4 pt-4 text-label font-semibold leading-relaxed"
            style={{ color: "var(--es-island-danger-ink)" }}
            data-testid="settings-chat-read-line"
          >
            {read.text}
          </p>
        )}
        <ExtractionSettings />
        <Separator />
        <WorkerPanel />
      </TabsContent>
    </Tabs>
  );
}
