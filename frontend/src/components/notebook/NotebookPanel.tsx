/**
 * NotebookPanel - what this chat has established, and what it costs.
 *
 * The screen has one job the storage layer cannot do: make the difference
 * between a note that IS being sent and one that is not visible at a glance.
 * Three states look identical in a plain list and are not:
 *
 *   - live      - in the payload right now
 *   - retired   - a newer note replaced it; kept, not sent
 *   - over the ceiling - it did not fit this turn; kept, not sent
 *
 * The rule is that a note never disappears, so none of these is ever
 * hidden. They are marked instead, and the mark says which one it is.
 *
 * Derived from the existing language on purpose: lucide icons at 13px, the
 * the persona row vocabulary (`persona-card`, `persona-ghost-action`,
 * `persona-danger-action`) because this panel sits inside `.glass-right`, the
 * app's one LIGHT surface - the chat-bubble classes are painted for the dark
 * shell and would arrive as pale icons on a white panel.
 *
 * Pin is one button with a swapped icon. Delete is the house confirm pattern:
 * the trigger is replaced in place by confirm/cancel, which is what every
 * other destructive action here does.
 */
import { useContextNotesStore } from "@/lib/chat/contextNotes";
import { useSeenNotesStore } from "@/lib/chat/seenNotes";
import { useHeldDiscards } from "@/lib/chat/heldDiscards";
import {
  EDIT_EMPTY,
  MODEL_NOTE_WORDS,
  NOTE_ROW_WORDS,
  NEW_NOTE_PROMPT,
  NOTE_STATE_WORDS,
  NOTES_EMPTY,
  NOTES_IN_ENGLISH,
  NOTES_UNREAD,
  saveFailedProse,
} from "@/components/notebook/noteProse";
import { CHAT_READ_WORDS, WAITING_WORDS } from "@/components/notebook/notesTabProse";
import {
  justSavedOf,
  useChatRead,
  useNotesViewStore,
  waitingOf,
} from "@/components/notebook/notesTabState";
import { PendingButton } from "@/components/notebook/PendingButton";
import { focusHandoff } from "@/components/notebook/focusHandoff";
import { settledAt } from "@/components/notebook/useRetryPress";
import { SettingUnread } from "@/components/notebook/SettingUnread";
import { useHeldValue } from "@/components/settings/useHeldValue";
import { isApiError } from "@/lib/api/client";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  Pin, PinOff, Plus, Trash2, X, Check, Loader2, Undo2, Search, Pencil, Settings2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { BackfillStrip } from "@/components/notebook/BackfillStrip";
import { SlideIn } from "@/components/motion/SlideIn";
import { Collapse } from "@/components/motion/Collapse";
import { CrossFade } from "@/components/motion/CrossFade";
import { FadeIn } from "@/components/motion/FadeIn";
import { useUiStore } from "@/lib/store/uiStore";
import {
  PANEL_DRAFT_KEYS,
  chatScopedPanelDraftKey,
  useDraftStore,
  usePanelDraft,
} from "@/lib/store/draftStore";
import { useErrorStore } from "@/lib/errors/errorStore";
import {
  useNotebook,
  useCreateNote,
  usePatchNote,
  useDeleteNote,
  useAcceptNote,
  useKeepNote,
  useCommitDiscards,
} from "@/lib/query/notebook";
import type { NotebookEntry } from "@/lib/schemas/notebook";

/** Mirrors backend notebook_store.ENTRY_MAX_CHARS. The backend refuses past
 *  it; this only stops the user typing a paragraph they will lose. */
const ENTRY_MAX_CHARS = 240;

/** Letters that exist in Turkish and not in English.
 *
 *  Used only to decide whether a quote is worth SHOWING, never to decide
 *  anything the model sees. A false positive costs one extra line on screen;
 *  a false negative costs a line the reader wanted. Neither is worth a
 *  language-detection library. */
const TURKISH_LETTERS = /[çÇğĞıİöÖşŞüÜ]/;

/** The model's own quote, when it is worth putting under the note.
 *
 *  Notes are written in English on purpose - the extractor is a small cheap
 *  model, and small cheap models read and write English far better than they
 *  read and write Turkish. The cost is that a sentence the user typed in
 *  Turkish comes back as somebody else's English paraphrase, and there is no
 *  way to check it against what was actually said.
 *
 *  So the check comes back: `evidence` is stored verbatim, in whatever
 *  language it was said in, and it is shown under the English note whenever
 *  the two are not the same words. The reader can see at a glance whether
 *  the paraphrase is fair. */
function originalOf(entry: NotebookEntry): string | null {
  const quote = (entry.evidence ?? "").trim();
  if (!quote) return null;
  if (entry.text.includes(quote)) return null;   // same words, no second line
  return quote;
}

//: The four characters the dotted/dotless I problem is made of.
//:
//: `İ` U+0130, `I` U+0049, `ı` U+0131, `i` U+0069.
const I_FAMILY = /[İIıi]/g;

/** Case-folded for SEARCH, with the I family collapsed to one letter.
 *
 *  Turkish lowercasing and invariant lowercasing disagree about this family
 *  in OPPOSITE directions, and picking a locale picks which half to break:
 *
 *    * `toLocaleLowerCase("tr")` wins `İstanbul` -> `istanbul` and LOSES
 *      `India` -> `ındia`, so typing `india` finds nothing;
 *    * the invariant rules win `India` and lose `İstanbul`, which folds to
 *      `i̇stanbul` with a combining dot.
 *
 *  The panel's own text says suggested notes are written in ENGLISH, so an
 *  English capital I is not an edge case here - and the notes are a Turkish
 *  speaker's, so `İ` is not either. Neither half is the one to sacrifice.
 *
 *  So the family is normalised BEFORE lowercasing and the locale question
 *  disappears with it. The cost is stated rather than hidden: a search for
 *  `ı` also matches `i` and vice versa. That is a wider net, not a wrong
 *  one - it can only ever return MORE notes, and this is a filter over notes
 *  the reader already owns, not an identity check.
 *
 *  AND NFC FIRST, which the paragraph above needs and did not have. The
 *  character class matches a base `I`; fed the DECOMPOSED form of `İ` -
 *  `I` followed by U+0307, which is what macOS and a good many IMEs and web
 *  pages produce - it replaced the `I` and left the combining dot stranded,
 *  giving exactly the `i̇stanbul` this comment names as the failure it is
 *  avoiding. The old `toLocaleLowerCase("tr")` handled that case, so it was
 *  a regression rather than an inherited hole. Composing first also closes
 *  the pre-existing half nobody had noticed: `ğ ş ü ö ç` each differ NFC
 *  vs NFD, so a decomposed `baş` did not match a typed `baş` either.
 */
function fold(text: string): string {
  return text.normalize("NFC").replace(I_FAMILY, "i").toLowerCase().trim();
}

/** Matches the note AND the verbatim quote under it.
 *
 *  The quote is the point: notes are written in English, so a Turkish
 *  sentence is stored as somebody else's paraphrase. Searching only the note
 *  text would mean the words the user actually typed are the one thing they
 *  cannot search for. */
function matches(entry: NotebookEntry, needle: string): boolean {
  if (!needle) return true;
  const q = fold(needle);
  return fold(entry.text).includes(q)
    || fold(entry.evidence ?? "").includes(q);
}

function noteState(
  entry: NotebookEntry,
): "live" | "proposed" | "retired" | "over" | "pinned_over" {
  if (entry.retired_at) return "retired";
  // Before `excluded_reason`: a proposal is not being sent for a much more
  // important reason than not fitting, and reading as "live" would tell the
  // user a note is in force while it is still waiting for them.
  if (entry.status === "proposed") return "proposed";
  // TWO reasons, not one. The server writes `over_ceiling` when a note was
  // evicted to make room, and `pinned_over_ceiling` when the pinned notes
  // ALONE are over the limit and even a pinned one had to go. Folding them
  // together printed "Pin it to protect it." on a note that was already
  // pinned - the one action that cannot help, told to the one person who
  // had already taken it.
  if (entry.excluded_reason === "pinned_over_ceiling") return "pinned_over";
  if (entry.excluded_reason) return "over";
  return "live";
}

/** A stable empty list, so the held-discards selector does not return a
 *  new array on every render. */
const NO_IDS: number[] = [];

/** What the empty field asks for; its placeholder and its title. */

export function NotebookPanel() {
  const chatId = useUiStore((s) => s.selectedChatId);
  const notebook = useNotebook(chatId);
  const { data, isLoading, isError } = notebook;
  const create = useCreateNote();
  const patch = usePatchNote();
  const remove = useDeleteNote();
  const accept = useAcceptNote();
  const keep = useKeepNote();
  const commitDiscards = useCommitDiscards();
  const pushError = useErrorStore((s) => s.pushError);
  const [handoff] = useState(focusHandoff);
  const cardTextRef = useRef<HTMLParagraphElement>(null);

  // The two swaps below go through `CrossFade`, the house primitive for a
  // REPLACEMENT rather than an arrival. It lived inline here until the
  // backfill strip needed the same gesture; the duration, the easing, the
  // reduced-motion collapse and the `data-slot`/`data-pane` the tests ask
  // through are all in the one component now.

  // The note being written outlives this panel, because this panel does not
  // outlive a click on a neighbouring tab. Same store as the composer's
  // unsent text, for the same reason and with the same lifetime.
  //
  // Keyed on the chat, like the composer buffer and unlike anything else in
  // this panel. The note is filed into whichever chat is open when Add is
  // pressed, so a buffer shared across chats would offer a sentence written
  // about one story for filing into the next one.
  const noteDraftKey = chatScopedPanelDraftKey(
    PANEL_DRAFT_KEYS.newNote,
    chatId,
  );
  const [draft, setDraft] = usePanelDraft(noteDraftKey);
  const clearPanelDraft = useDraftStore((s) => s.clearPanelDraft);
  // The filter is held the same way, under its own field of the same chat.
  // Being keyed on the chat is what enforces the rule the block below used to
  // enforce by hand, and enforces it BETTER: the value is read during render
  // from the key the open chat owns, so there is no frame in which the new
  // chat is filtered by the old chat's word, and nothing has to remember to
  // clear anything.
  const [query, setQuery] = usePanelDraft(
    chatScopedPanelDraftKey(PANEL_DRAFT_KEYS.newNote, chatId, "filter"),
  );
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  // Undo on this strip deletes every note in it at once, permanently, and it
  // had no confirmation at all - one press of a ghost button. The same
  // screen already asks before deleting ONE note (see `confirmDeleteId` and
  // the Confirm delete / Keep note pair on each row); this is that pattern,
  // not a new one.
  const [confirmUndo, setConfirmUndo] = useState(false);

  // The filter used to be cleared here by hand, and no longer is: it is now
  // keyed on the chat, so opening another conversation reads that chat's own
  // (usually absent) filter instead of inheriting this one's. The rule that
  // mattered is unchanged and is now structural. Why it mattered: the search
  // box only renders above five notes, so filtering a forty-note chat and
  // then opening a three-note one hid all three behind a filter whose input
  // had itself gone - an empty notebook, no visible cause, no control that
  // would clear it.
  //
  // Adjusted during render rather than in an effect on purpose. An effect
  // runs after the DOM is painted, so the reader would still get one frame of
  // the new chat with the wrong strip armed.
  //
  // `confirmDeleteId` is deliberately NOT reset with it: notebook entry ids
  // are AUTOINCREMENT over the whole table, so an id carried in from another
  // chat matches no row here and the open question has nothing to attach to.
  //
  // `confirmUndo` has no such excuse and IS reset. It is a bare boolean, so
  // it carries into the next chat and arms a strip nobody armed: the reader
  // opens chat B and its Undo row is already offering "Remove all N"
  // about a set they have never seen. That is the same unconfirmed
  // bulk delete this pair exists to prevent, one chat over.
  const [filterChatId, setFilterChatId] = useState(chatId);
  if (chatId !== filterChatId) {
    setFilterChatId(chatId);
    setConfirmUndo(false);
  }

  // DISCARD IS HELD, NOT SENT, while its row says "Suggestion discarded."
  // with an Undo: a suggestion is not in force, so holding it back changes
  // nothing the model sees, and Undo can put it back exactly as it was
  // (the store has no way to bring a deleted row back). The deletes are
  // sent, as ordinary requests, when the panel leaves the chat or unmounts,
  // or when "Undo this run" is pressed (the strip sends them first). Once
  // sent they are gone from the store, so no Undo is offered for them.
  // Nothing is sent when the page closes: this app sends no request that
  // outlives its page (no keepalive, no beacon; the test egress sentinel
  // refuses one), so a suggestion discarded just before closing is simply
  // still there next time - the safe side. Held in `useHeldDiscards`.
  const discarded = useHeldDiscards((s) =>
    chatId == null ? NO_IDS : s.byChat[chatId] ?? NO_IDS);
  useEffect(() => () => {
    if (chatId != null) commitDiscards(useHeldDiscards.getState().take(chatId), chatId);
  }, [chatId, commitDiscards]);

  const busy = create.isPending || patch.isPending || remove.isPending
    || accept.isPending || keep.isPending;

  const all = data?.entries ?? [];
  const entries = all.filter((e) => matches(e, query));
  // The number the SERVER computed for the last turn, when there has been
  // one. Counting live notes here is a client-side guess that is right only
  // while nothing was trimmed - and the case where something was is the whole
  // reason this number is on screen. Before a turn has run there is nothing to
  // report, so the count stands in.
  const turn = useContextNotesStore((s) =>
    chatId == null ? undefined : s.byChat[chatId]);
  const live = all.filter((e) => noteState(e) === "live").length;
  // Counted the way the SERVER counts it, which is the whole job of a
  // fallback: build_notebook_blocks takes `total` from the accepted,
  // non-retired rows and nothing else. `all.length` counted every row on
  // screen instead, proposals and retired ones included, so a notebook with
  // five accepted notes and two hundred waiting proposals read "5 of 205
  // sent" and then "5 of 5" one message later, when the server's number
  // arrived. Same notebook, two numbers, and the one before the first turn
  // was measuring a set that is never sent.
  const inForce = all.filter(
    (e) => e.status === "accepted" && !e.retired_at).length;
  const sent = turn?.notebook_sent ?? live;
  const total = turn?.notebook_total ?? inForce;

  // Accepted, written by the model, and never announced. `proposed` rows are
  // deliberately excluded: those already announce themselves by sitting in
  // the list unsent, which is the whole point of review being on.
  //
  // And a note the READER already answered is not asked about again: one
  // they kept by hand (`hand_accepted_at`, written by the Keep press
  // itself), whose words they changed (`edited_at`), or that they pinned -
  // the card's Undo must never offer to delete a pinned note. All on disk, so
  // this holds after a restart too, when the in-memory `seen` list is
  // empty. The strip used to open on the note the reader had just kept and
  // ask "Keep it?" about it.
  const seen = useSeenNotesStore((s) =>
    chatId == null ? undefined : s.byChat[chatId]);
  const markSeen = useSeenNotesStore((s) => s.markSeen);
  // One definition, shared with the count on the Notes tab button.
  const justSaved = justSavedOf(all, seen);

  // WAITING FIRST. Suggestions wait unsent until the reader
  // answers them, so they are the part of this list that needs a person:
  // counted, grouped at the top, and answerable in one press each way. The
  // search does not reach them: it sits under the group and filters the
  // notes, and a suggestion hidden by a word typed there would be one the
  // count, the tab's number and "Keep all" disagree about. A discarded one
  // keeps its place (with its Undo) in the group until the panel leaves
  // the chat.
  const isWaiting = (e: NotebookEntry) => e.status === "proposed" && !e.retired_at;
  const waitingRows = all.filter(isWaiting);
  const keptAll = all.filter((e) => !isWaiting(e));
  const keptRows = entries.filter((e) => !isWaiting(e));
  const waiting = waitingOf(waitingRows, discarded);
  // The ids the last "Discard all" held, so "Undo all" brings back that
  // batch and nothing the reader discarded one by one.
  const [discardBatch, setDiscardBatch] = useState<number[]>([]);
  const batchHeld = discardBatch.filter((id) => discarded.includes(id));
  // The line keeps its last number while it closes (KURAL 5.5).
  const shownBatch = useHeldValue(batchHeld.length > 0 ? batchHeld.length : null) ?? 0;
  const batchLineRef = useRef<HTMLParagraphElement>(null);
  const waitingHeadRef = useRef<HTMLParagraphElement>(null);
  const read = useChatRead(chatId);
  // Not read, or read only by a billed test call: the line takes the danger
  // ink and offers the way to the reason.
  const alarm = read.kind === "not" || read.kind === "test";

  /** "Keep it" / "Keep them": a hand keep, written on the server
   *  (`hand_accepted_at`), so "Undo this run" keeps these notes and the
   *  card does not ask about them again, after a restart too. Only what
   *  was kept is marked seen; a failure leaves the rest announced. */
  async function handleKeepSaved() {
    if (chatId == null || busy) return;
    const kept: number[] = [];
    try {
      for (const e of justSaved) {
        await keep.mutateAsync([e.id, chatId]);
        kept.push(e.id);
      }
    } catch (err) {
      pushError(err, "error", { chatId });
    }
    if (kept.length) markSeen(chatId, kept);
    // The card closes under the keyboard; the new-note field takes it.
    focusTarget.current = "new";
  }

  async function handleUndo() {
    // Deleted, not retired: the user is saying it should never have been
    // written. A retired row would go on sitting in the panel forever as a
    // fact they explicitly rejected.
    const doomed = justSaved.map((e) => e.id);
    if (chatId == null) return;
    // The Remove button is about to leave: the card's words hold the
    // keyboard until the deletes are done (and stay holding it if all fail).
    cardTextRef.current?.focus({ preventScroll: true });
    setConfirmUndo(false);
    // ACKNOWLEDGE ONLY WHAT ACTUALLY WENT, AND ONLY AFTERWARDS.
    //
    // `acknowledge()` used to run BEFORE the loop and marked every id as
    // seen. The first failed DELETE then broke out to the catch with the
    // rest of the ids already silenced - so notes that are still in the
    // notebook stopped being announced by the one strip that announces
    // them, and the model's writing became invisible by way of an error
    // nobody could act on.
    const removed: number[] = [];
    try {
      // The chat is named on every one of these: the routes refuse a note
      // that is not in the chat the caller says it is acting from.
      for (const id of doomed) {
        await remove.mutateAsync([id, chatId]);
        removed.push(id);
      }
    } catch (err) {
      pushError(err, "error", { chatId: chatId ?? undefined });
    }
    if (removed.length) {
      markSeen(chatId, removed);
      focusTarget.current = "new";
    }
  }

  /** "Keep all": the same Keep, once per waiting row, so each is written
   *  as the reader's own keep (`hand_accepted_at`) exactly as a single press
   *  writes it. Stops at the first failure and says so; what was kept stays
   *  kept, and the rest stay waiting with the button still under the
   *  keyboard. Once every one is kept the group leaves, and the new-note
   *  field takes the keyboard. */
  async function handleKeepAll() {
    if (chatId == null || busy) return;
    const ids = waiting.map((e) => e.id);
    let kept = 0;
    try {
      for (const id of ids) {
        await accept.mutateAsync([id, chatId]);
        kept += 1;
      }
    } catch (err) {
      pushError(err, "error", { chatId });
    }
    if (kept === ids.length) focusTarget.current = "new";
  }

  /** "Discard all": every waiting row is held exactly as a single Discard
   *  holds it, and the batch stands as one line with one Undo that takes
   *  the batch back. Nothing is sent until the panel leaves the chat. */
  function handleDiscardAll() {
    if (chatId == null) return;
    const ids = waiting.map((e) => e.id);
    handoff.want("undo-all", waitingHeadRef.current);
    // Added to the batch, not in place of it: an earlier Discard all whose
    // rows are still held stays takeable back by the same Undo all.
    setDiscardBatch((was) => [...was.filter((id) => discarded.includes(id)), ...ids]);
    const held = useHeldDiscards.getState();
    for (const id of ids) held.hold(chatId, id);
  }

  function handleUndoAll() {
    if (chatId == null) return;
    handoff.want("discard-all", batchLineRef.current);
    const held = useHeldDiscards.getState();
    for (const id of batchHeld) held.release(chatId, id);
    setDiscardBatch([]);
  }

  /** "Open Settings" / "Open Security" beside a "Not read" line: to the
   *  control that fixes it, in the tab's Settings view or on the panel's
   *  Security tab. RightPanel puts the keyboard on it once it is drawn. */
  function openFix() {
    if (read.kind !== "not" && read.kind !== "test") return;
    const views = useNotesViewStore.getState();
    if (read.fix.where === "security") {
      views.requestFocus(read.fix.target, "secrets");
      useUiStore.getState().setActiveRightPanelTab("secrets");
    } else {
      views.openAt("settings", read.fix.target);
    }
  }

  /** "Show progress": the reading strip, after the list, and its Stop. */
  function showProgress() {
    const stop = document.querySelector<HTMLElement>(
      '[data-testid="backfill-progress"] button');
    stop?.scrollIntoView({ block: "nearest" });
    stop?.focus({ preventScroll: true });
  }

  async function handleAdd() {
    const text = draft.trim();
    if (!text || chatId == null) return;
    try {
      await create.mutateAsync([chatId, { text }]);
      // Dropped rather than emptied: the note is filed, so there is nothing
      // left to protect and no reason to leave a zero-byte entry behind.
      clearPanelDraft(noteDraftKey);
    } catch (err) {
      pushError(err, "error", { chatId });
    }
  }

  async function handlePin(entry: NotebookEntry) {
    try {
      if (chatId == null) return;
      await patch.mutateAsync([entry.id, chatId, { pinned: !entry.pinned }]);
    } catch (err) {
      pushError(err, "error", { chatId: chatId ?? undefined });
    }
  }

  /** Saves new words for a note through the existing PATCH. Answers null
   *  when saved, else the sentence that says why not, from the server's own
   *  reason: the ROW says it, beside the words it kept. `provenance` is not
   *  in the payload and cannot be; the store writes `edited_at` itself. */
  async function handleSave(entry: NotebookEntry, text: string): Promise<string | null> {
    if (chatId == null) return saveFailedProse(null);
    try {
      await patch.mutateAsync([entry.id, chatId, { text }]);
      return null;
    } catch (err) {
      return saveFailedProse(isApiError(err) ? err.detail : null);
    }
  }

  /** Where the keyboard goes once a row is gone: the next row, else the one
   *  before, else the new-note field. Without it focus fell to the page's
   *  body and a keyboard reader was thrown back to the top. Picked BEFORE
   *  the request, from the list on screen, because the row being removed
   *  is still there when the answer comes back. */
  const newNoteRef = useRef<HTMLInputElement>(null);
  const focusTarget = useRef<number | "new" | null>(null);
  function focusAfterRemoving(id: number) {
    // In the order on screen: the waiting group, then the list.
    const shown = [...waitingRows, ...keptRows];
    const at = shown.findIndex((e) => e.id === id);
    const neighbour = shown[at + 1] ?? shown[at - 1];
    return () => { focusTarget.current = neighbour ? neighbour.id : "new"; };
  }
  // Moved on the first render that is no longer busy: the new-note field is
  // disabled while a request is out, and a disabled element cannot take
  // focus. A ref and an effect with no list, so it is read after EVERY
  // render; the refetch that takes the row away is one, and nothing is set
  // here that would cause another.
  useLayoutEffect(() => {
    const target = focusTarget.current;
    if (target == null || busy) return;
    const row = target === "new" ? null
      : document.querySelector<HTMLElement>(
        `[data-testid="note-${target}"] button:not([disabled])`);
    (row ?? newNoteRef.current)?.focus();
    focusTarget.current = null;
  });
  // A single Keep: the row leaves the waiting group for the list once the
  // refetch says it is kept, and is drawn anew there. Its Pin takes the
  // keyboard in that same commit (the row did this itself while it stayed
  // in place). Until then the Keep button, still in the old row, holds it.
  const keptMoves = useRef<number | null>(null);
  useLayoutEffect(() => {
    const id = keptMoves.current;
    if (id == null) return;
    const row = document.querySelector<HTMLElement>(`[data-testid="note-${id}"]`);
    if (!row) { keptMoves.current = null; return; }
    if (row.dataset.state === "proposed") return;
    keptMoves.current = null;
    row.querySelector<HTMLElement>(`[data-testid="note-${id}-pin-slot"] button`)?.focus();
  });

  /** Declining a suggestion: one press, no question, and not silent - the
   *  row becomes "Suggestion discarded." with an Undo, which takes the
   *  keyboard. The delete itself is held (see `discarded`). */
  function handleDiscard(id: number) {
    handoff.want(`undo-discard-${id}`);
    if (chatId != null) useHeldDiscards.getState().hold(chatId, id);
  }

  /** Undo a discard: the suggestion comes back, and its Discard button
   *  takes the keyboard, where the reader was. */
  function handleUndoDiscard(id: number) {
    handoff.want(`discard-${id}`);
    if (chatId != null) useHeldDiscards.getState().release(chatId, id);
  }

  /** Keep a suggestion. Answers whether it worked, so the row only hands
   *  the keyboard on for a keep that happened. */
  async function handleAccept(id: number): Promise<boolean> {
    try {
      if (chatId == null) return false;
      await accept.mutateAsync([id, chatId]);
      return true;
    } catch (err) {
      pushError(err, "error", { chatId: chatId ?? undefined });
      return false;
    }
  }

  async function handleDelete(id: number) {
    setConfirmDeleteId(null);
    if (chatId == null) return;
    const refocus = focusAfterRemoving(id);
    try {
      await remove.mutateAsync([id, chatId]);
      refocus();
    } catch (err) {
      pushError(err, "error", { chatId });
    }
  }

  /** One row, where it is drawn: a discarded suggestion stands in its own
   *  place with its Undo, anything else is a note row. A kept suggestion
   *  moves from the waiting group into the list, which draws it anew, so
   *  its Pin (where a single Keep hands the keyboard) is found there. */
  function renderRow(entry: NotebookEntry) {
    return discarded.includes(entry.id) ? (
      <DiscardedRow
        key={`discarded-${entry.id}`}
        entry={entry}
        takeFocus={handoff.take}
        onUndo={() => handleUndoDiscard(entry.id)}
      />
    ) : (
      <NoteRow
        key={entry.id}
        entry={entry}
        busy={busy}
        confirming={confirmDeleteId === entry.id}
        onSave={(text) => handleSave(entry, text)}
        onPin={() => void handlePin(entry)}
        onAccept={() => {
          // Armed before the request: the refetch that moves the row can
          // land before this promise's own callback runs.
          keptMoves.current = entry.id;
          return handleAccept(entry.id).then((ok) => {
            if (!ok && keptMoves.current === entry.id) keptMoves.current = null;
            return ok;
          });
        }}
        onDiscard={() => handleDiscard(entry.id)}
        onAskDelete={() => setConfirmDeleteId(entry.id)}
        onCancelDelete={() => setConfirmDeleteId(null)}
        onDelete={() => void handleDelete(entry.id)}
        takeFocus={handoff.take}
      />
    );
  }

  if (chatId == null) {
    return (
      <SlideIn>
        <section className="space-y-3 p-4">
          <p className="text-label leading-relaxed text-muted-foreground">
            Open a chat to see what it has established.
          </p>
        </section>
      </SlideIn>
    );
  }

  return (
    <SlideIn>
      <section className="space-y-3 p-4" aria-label="Notebook">
        {/* THIS CHAT'S STATE, FIRST. The count beside it used to
            be the whole header, and "3 of 3 sent" looked healthy while the
            chat was not being read at all; the reason sat on the Background
            notes card, screens further down. Now the first words of the tab
            say whether this chat is read, and a "Not read" line is drawn in
            the panel's danger ink with the way to its reason under it. The
            view's tab already names it Notes, so the heading that said
            "Notebook" gave its row to this line. */}
        <div className="flex items-baseline justify-between gap-3">
          <p
            className="min-w-0 text-label leading-relaxed"
            data-testid="chat-read-line"
            data-read={read.kind}
            role="status"
            style={{
              color: alarm
                ? "var(--es-island-danger-ink)"
                : "var(--muted-foreground)",
              fontWeight: alarm ? 600 : undefined,
            }}
          >
            {read.text}
          </p>
          {/* Persistent, not a toast. errorStore merges by code and chat, so a
              per-turn count would announce itself once and then go quiet just
              as the ceiling starts biting every turn. */}
          {/* `role="status"` because this number changes on its own: it is
              rewritten by the `done` frame of a turn the user spent in the
              composer, not by anything they did here. A note quietly going
              from sent to not sent is the one event in this panel a reader
              who is not watching it must still be told about. */}
          <span
            className="shrink-0 whitespace-nowrap text-label leading-relaxed text-muted-foreground"
            data-testid="notebook-sent-count"
            role="status"
          >
            {sent} of {total} sent
          </span>
        </div>
        {/* The way to the reason (Settings) or to the Stop of a reading in
            progress (the strip after the list). Only when there is one. */}
        <Collapse open={alarm || read.kind === "reading"} className="min-w-0">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            data-testid="chat-read-action"
            onClick={alarm ? openFix : showProgress}
            className="persona-ghost-action h-7 gap-1.5 px-2 text-ui"
          >
            {alarm && <Settings2 size={12} className="size-3" aria-hidden />}
            {read.kind === "reading" ? CHAT_READ_WORDS.showProgress
              : alarm && read.fix.where === "security" ? CHAT_READ_WORDS.openSecurity
                : CHAT_READ_WORDS.openSettings}
          </Button>
        </Collapse>

        {/* THE WAITING GROUP: counted, first, and answerable all
            at once. The count is its own line at the top of the list; the
            two buttons act on exactly the rows under it. */}
        <Collapse open={waitingRows.length > 0} className="min-w-0">
          <div className="space-y-1" role="group" aria-labelledby="notes-waiting-head">
            <Collapse open={waiting.length > 0} className="min-w-0">
              <div className="space-y-1">
                <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                  <p
                    ref={waitingHeadRef}
                    id="notes-waiting-head"
                    tabIndex={-1}
                    className="text-label font-semibold outline-none"
                    style={{ color: "var(--color-es-text-light)" }}
                    data-testid="notes-waiting-count"
                  >
                    {WAITING_WORDS.count(waiting.length)}
                  </p>
                  <span className="flex flex-wrap items-center justify-end gap-2">
                    <PendingButton
                      type="button"
                      size="sm"
                      variant="ghost"
                      pending={busy}
                      onClick={() => void handleKeepAll()}
                      className="persona-ghost-action h-7 gap-1.5 px-2 text-ui"
                    >
                      <Check size={12} className="size-3" />
                      {WAITING_WORDS.keepAll}
                    </PendingButton>
                    <PendingButton
                      ref={handoff.take("discard-all")}
                      type="button"
                      size="sm"
                      variant="ghost"
                      pending={busy}
                      onClick={handleDiscardAll}
                      className="persona-ghost-action h-7 gap-1.5 px-2 text-ui"
                    >
                      <X size={12} className="size-3" />
                      {WAITING_WORDS.discardAll}
                    </PendingButton>
                  </span>
                </div>
                <p className="text-label leading-relaxed text-muted-foreground">
                  {WAITING_WORDS.hint}
                </p>
              </div>
            </Collapse>
            {/* ONE LINE FOR ONE DISCARD ALL: the batch
                stands as a single row with a single Undo, not a row and an
                Undo per suggestion beside a second Undo for all of them.
                A suggestion discarded on its own keeps its own row. */}
            <Collapse open={batchHeld.length > 0} className="min-w-0">
              <div
                className="persona-card flex items-center gap-2"
                data-testid="notes-discarded-batch"
              >
                <p
                  ref={batchLineRef}
                  tabIndex={-1}
                  className="min-w-0 flex-1 text-label leading-relaxed text-muted-foreground outline-none"
                  role="status"
                >
                  {WAITING_WORDS.discarded(shownBatch)}
                </p>
                <Button
                  ref={handoff.take("undo-all")}
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={handleUndoAll}
                  className="persona-ghost-action h-7 shrink-0 gap-1.5 px-2 text-ui"
                >
                  <Undo2 size={12} className="size-3" />
                  {WAITING_WORDS.undoAll}
                </Button>
              </div>
            </Collapse>
            <div className="space-y-1">
              {waitingRows.filter((e) => !batchHeld.includes(e.id)).map((entry) => renderRow(entry))}
            </div>
          </div>
        </Collapse>

        {/* A35. With auto-accept ON there is no review step, so a note the
            model wrote reaches the prompt having been seen by nobody - and
            "the write was invisible" is the complaint every shipped version
            of this feature collected. Announced once, takeable back, and
            then remembered as announced.

            `role="status"` for that same reason taken one step further: the
            strip appears after NO user action, so a reader who is not looking
            at this panel gets no signal at all that something was written
            into their prompt. Silent for a sighted user is the complaint this
            strip answers; silent for a screen reader is the same complaint
            with nothing left to answer it. */}
        <Collapse open={justSaved.length > 0} className="min-w-0">
          <div
            className="persona-card space-y-2"
            data-testid="just-saved"
            role="status"
          >
            {/* Breaks inside a word, as the note's own row below does: a note
                that is one long word quoted here otherwise ran past the card
                and was cut by its clip. */}
            <p
              ref={cardTextRef}
              tabIndex={-1}
              className="break-words text-label leading-relaxed text-muted-foreground outline-none"
            >
              Saved {justSaved.length === 1 ? "a note" : `${justSaved.length} notes`}{" "}
              the model wrote{justSaved.length === 1
                ? `: "${justSaved[0].text}"`
                : "."}
            </p>
            <CrossFade
              pane={confirmUndo ? "confirm" : "undo"}
              className="flex items-center gap-2"
            >
              {confirmUndo ? (
                <>
                  <PendingButton
                    type="button"
                    size="sm"
                    variant="ghost"
                    pending={busy}
                    onClick={() => void handleUndo()}
                    className="persona-danger-action h-7 gap-1.5 px-2 text-ui"
                  >
                    <Trash2 size={12} className="size-3" />
                    {/* "Remove" is this tab's word for an undo (this card's
                        and the run's), "Delete" for a note, "Discard" for
                        a suggestion. */}
                    Remove {justSaved.length === 1
                      ? "it" : `all ${justSaved.length}`}
                  </PendingButton>
                  <PendingButton
                    ref={handoff.take("card-cancel")}
                    type="button"
                    size="sm"
                    variant="ghost"
                    pending={busy}
                    onClick={() => {
                      handoff.want("card-undo", cardTextRef.current);
                      setConfirmUndo(false);
                    }}
                    className="persona-ghost-action h-7 px-2 text-ui"
                  >
                    Cancel
                  </PendingButton>
                </>
              ) : (
                <>
                  <PendingButton
                    ref={handoff.take("card-undo")}
                    type="button"
                    size="sm"
                    variant="ghost"
                    pending={busy}
                    onClick={() => {
                      handoff.want("card-cancel", cardTextRef.current);
                      setConfirmUndo(true);
                    }}
                    className="persona-danger-action h-7 gap-1.5 px-2 text-ui"
                  >
                    <Undo2 size={12} className="size-3" />
                    Undo
                  </PendingButton>
                  <PendingButton
                    type="button"
                    size="sm"
                    variant="ghost"
                    pending={busy}
                    onClick={() => void handleKeepSaved()}
                    className="persona-ghost-action h-7 px-2 text-ui"
                  >
                    Keep {justSaved.length === 1 ? "it" : "them"}
                  </PendingButton>
                </>
              )}
            </CrossFade>
          </div>
        </Collapse>

        {/* Only once there are enough notes for the eye to lose one. A search
            box over four rows is furniture. */}
        <Collapse open={all.length > 5} className="min-w-0">
          <div className="relative">
            <Search
              size={12}
              className="pointer-events-none absolute left-2 top-1/2 size-3 -translate-y-1/2"
              style={{ color: "var(--muted-foreground)" }}
              aria-hidden="true"
            />
            <Input
              value={query}
              placeholder="Search notes..."
              title="Search notes..."
              aria-label="Search notes"
              onChange={(e) => setQuery(e.target.value)}
              className="persona-field pl-7 text-label"
            />
            {query !== "" && (
              <FadeIn>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-label="Clear search"
                  onClick={() => setQuery("")}
                  className="persona-ghost-action absolute right-1 top-1/2 h-6 w-6 -translate-y-1/2 p-0"
                >
                  <X size={12} className="size-3" />
                </Button>
              </FadeIn>
            )}
          </div>
        </Collapse>

        <div className="flex items-center gap-2">
          <Input
            ref={newNoteRef}
            value={draft}
            maxLength={ENTRY_MAX_CHARS}
            placeholder={NEW_NOTE_PROMPT}
            title={NEW_NOTE_PROMPT}
            // The placeholder is a prompt, not a name. It is the only thing
            // that named this box, and a placeholder stops being read out the
            // moment there is a value - so the control announced itself by
            // its own contents while the user typed, and as nothing at all
            // once they paused. The label stays put.
            aria-label="New note"
            disabled={busy}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleAdd();
            }}
            // `persona-field` like every other input on this light
          // island. The size is the label step alone: Input's base string
          // used to carry a breakpoint size of its own that beat a bare
          // size from a call site above 768px, and every caller here
          // repeated the override. The base reads one scale step now, and
          // cn() knows the scale, so the call site's size wins on its own.
          className="persona-field text-label"
          />
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={busy || !draft.trim()}
            onClick={() => void handleAdd()}
            aria-label="Add note"
            className="persona-ghost-action h-7 w-7 p-0"
          >
            {/* A span, not a div: this sits inside a button's label, which
                is phrasing content. */}
            <CrossFade
              as="span"
              pane={create.isPending ? "busy" : "idle"}
              className="inline-flex"
            >
              {create.isPending ? (
                <Loader2 size={12} className="size-3 animate-spin" />
              ) : (
                <Plus size={12} className="size-3" />
              )}
            </CrossFade>
          </Button>
        </div>

        {/* The first load only: a retry keeps its own line open instead. */}
        <Collapse open={isLoading && notebook.errorUpdateCount === 0} className="min-w-0">
          <p className="text-label leading-relaxed text-muted-foreground">Loading...</p>
        </Collapse>

        {/* A failed load is not an empty notebook, and it used to read as
            one. `isError` was never taken off the query, so a 500 left
            `isLoading` false and `data` undefined - which is exactly the
            shape of a notebook with nothing in it - and the panel told the
            reader "Nothing yet." about notes that were sitting in the
            database. The worst possible lie for this feature: the whole
            point of the notebook is that the character stops forgetting, and
            the panel said the forgetting had already happened.

            First in the chain, so the two branches below can stay the pair
            they are rather than being taught about a third case each. */}
        <SettingUnread
          open={!isLoading && isError}
          tone="muted"
          text={NOTES_UNREAD}
          retrying={notebook.isFetching}
          settled={settledAt(notebook)}
          onRetry={() => void notebook.refetch()}
          focusAfter={() => newNoteRef.current}
        />

        {/* One or the other, never both. `entries` is the FILTERED list, so
            an empty notebook and a filter that matched nothing produce the
            same empty list and used to print both sentences at once: "Nothing
            yet." sitting directly above "No note matches that. 3 are still
            here." The two contradict each other, and one of them is telling
            the reader their notes are gone. The filter decides which is
            true. */}
        <Collapse open={!isLoading && !isError && all.length === 0 && query === ""} className="min-w-0">
          <p className="text-label leading-relaxed text-muted-foreground">
            {NOTES_EMPTY}
          </p>
        </Collapse>

        {/* `role="status"`: the list empties out under the reader's own
            typing, and for anyone not watching the rows the count is the only
            evidence that the notes are still there and only hidden. */}
        <Collapse open={!isLoading && !isError && keptRows.length === 0 && query !== ""} className="min-w-0">
          <p
            className="text-label leading-relaxed text-muted-foreground"
            role="status"
          >
            {/* Two short sentences: the second used to run to 16
                words explaining what the search covers. */}
            No note matches that. {keptAll.length} {keptAll.length === 1 ? "is" : "are"} still
            here, hidden by the search.
          </p>
        </Collapse>

        <div className="space-y-1">
          {keptRows.map((entry) => renderRow(entry))}
        </div>

        {/* Once, not on every row, and after the list: it explains the rows,
            so it is read after them rather than standing between the reader
            and the first one. */}
        {[...waitingRows, ...keptRows].some((e) => e.provenance === "model") && (
          <p className="text-label leading-relaxed text-muted-foreground">
            {NOTES_IN_ENGLISH}
          </p>
        )}

        {/* THE WAY BACK to a chat's earlier messages: a job with a price, a
            preview, a stop and an undo, and still the one door to it. AFTER
            the list: reading history is occasional, the notes are
            what the tab is for. While a reading runs, the line at the top
            says so and "Show progress" brings the keyboard to its Stop; a
            pick made in the chat scrolls the strip into view itself. */}
        <BackfillStrip chatId={chatId} />
      </section>
    </SlideIn>
  );
}

/** A suggestion the reader discarded, standing in its place until the
 *  panel leaves the chat: the discard is held there, not sent, so Undo can
 *  put it back. Nothing about a Discard is silent any more. */
function DiscardedRow({
  entry,
  takeFocus,
  onUndo,
}: {
  entry: NotebookEntry;
  takeFocus: (key: string) => (el: HTMLElement | null) => void;
  onUndo: () => void;
}) {
  return (
    <Collapse open className="min-w-0">
      <div
        className="persona-card flex items-center gap-2"
        data-testid={`note-${entry.id}-discarded`}
      >
        <p
          className="min-w-0 flex-1 text-label leading-relaxed text-muted-foreground"
          role="status"
        >
          {NOTE_ROW_WORDS.discarded}
        </p>
        <Button
          ref={takeFocus(`undo-discard-${entry.id}`)}
          type="button"
          size="sm"
          variant="ghost"
          onClick={onUndo}
          className="persona-ghost-action h-7 shrink-0 gap-1.5 px-2 text-ui"
        >
          <Undo2 size={12} className="size-3" />
          {NOTE_ROW_WORDS.undoDiscard}
        </Button>
      </div>
    </Collapse>
  );
}

function NoteRow({
  entry,
  busy,
  confirming,
  onPin,
  onAccept,
  onDiscard,
  onAskDelete,
  onCancelDelete,
  onDelete,
  onSave,
  takeFocus,
}: {
  entry: NotebookEntry;
  busy: boolean;
  confirming: boolean;
  onPin: () => void;
  /** Keeps a suggestion; resolves whether it worked. */
  onAccept: () => Promise<boolean>;
  /** Declines a suggestion; the panel shows "Suggestion discarded." with an
   *  Undo in its place. */
  onDiscard: () => void;
  onAskDelete: () => void;
  onCancelDelete: () => void;
  onDelete: () => void;
  /** Saves new words; resolves null when saved, else the sentence that
   *  says why not. */
  onSave: (text: string) => Promise<string | null>;
  /** The panel's one-shot focus hand-off (see `focusHandoff`). */
  takeFocus: (key: string) => (el: HTMLElement | null) => void;
}) {
  const state = noteState(entry);
  const original = originalOf(entry);
  const proposed = state === "proposed";

  // EDIT IN PLACE. The text line becomes a field in the same row, the same
  // size and in the same spot, and the row itself never remounts (it is
  // keyed on the note's id and this is its own state). The pencil is ONE
  // button whose icon and name swap to Save, so the keyboard never loses
  // the element it is standing on; the last slot (Discard or Delete) gives
  // its place to Cancel. Enter (with or without Shift) saves - a note is
  // one line - Escape cancels and puts the words back, and focus returns to
  // the pencil. Nothing happens on blur.
  // THE BOX'S WORDS LIVE IN THE DRAFT STORE, keyed by chat and note, so
  // they outlive the lock screen's remount and a tab switch (my rule: no
  // silent loss). The entry existing is what holds the box open. In memory
  // only, like every draft (draftStore.ts), and dropped on Save or Cancel.
  const editKey = chatScopedPanelDraftKey(
    PANEL_DRAFT_KEYS.noteEdit, entry.chat_id, String(entry.id));
  const held = useDraftStore((s) => s.panel[editKey]);
  const setPanelDraft = useDraftStore((s) => s.setPanelDraft);
  const clearPanelDraft = useDraftStore((s) => s.clearPanelDraft);
  const editing = held != null;
  const draftText = held?.text ?? "";
  const setDraftText = (text: string) => { setPanelDraft(editKey, text); };
  // A box that was already open when the row mounted (after an unlock, or
  // back from another tab) does not take the keyboard by itself.
  const openAtMount = useRef(editing);
  const [saving, setSaving] = useState(false);
  /** Why the last save did not land, or null. */
  const [saveFailed, setSaveFailed] = useState<string | null>(null);
  const editButtonRef = useRef<HTMLButtonElement>(null);
  const pinRef = useRef<HTMLButtonElement>(null);
  const fieldRef = useRef<HTMLTextAreaElement>(null);
  const wasEditing = useRef(false);
  const emptyDraft = draftText.trim() === "";
  const hintId = `note-${entry.id}-edit-hint`;

  // Layout, not passive: the field is focused in the same task as the
  // commit that drew it, so nothing paints with focus nowhere. After Escape
  // or Cancel the keyboard goes back to the pencil; a Save pressed on the
  // pencil never loses it (the same element, only `aria-disabled` while it
  // saves).
  const giveFocusBack = useRef(false);
  useLayoutEffect(() => {
    if (editing) {
      wasEditing.current = true;
      giveFocusBack.current = false;
      if (openAtMount.current) {
        openAtMount.current = false;
        return;
      }
      const field = fieldRef.current;
      if (field) {
        field.focus();
        const end = field.value.length;
        field.setSelectionRange(end, end);
      }
    } else if (wasEditing.current) {
      wasEditing.current = false;
      giveFocusBack.current = true;
    }
  }, [editing]);
  // KEEP hands the keyboard on too: the Keep button leaves the row once the
  // suggestion is accepted, and Pin, the next button, is where it lands.
  const keepPressed = useRef(false);
  useLayoutEffect(() => {
    if (keepPressed.current && !proposed) {
      keepPressed.current = false;
      pinRef.current?.focus();
    }
    if (giveFocusBack.current && editButtonRef.current) {
      giveFocusBack.current = false;
      editButtonRef.current.focus();
    }
  });

  function beginEdit() {
    if (confirming) onCancelDelete();
    setSaveFailed(null);
    setPanelDraft(editKey, entry.text);
  }

  function cancelEdit() {
    setSaveFailed(null);
    clearPanelDraft(editKey);
  }

  async function saveEdit() {
    if (saving) return;
    const text = draftText.trim();
    if (!text) return;
    if (text === entry.text) {
      cancelEdit();
      return;
    }
    setSaving(true);
    setSaveFailed(null);
    const why = await onSave(text);
    setSaving(false);
    if (why == null) clearPanelDraft(editKey);
    else setSaveFailed(why);
  }

  // The delete question opens UNDER the note's words, in its own line, and
  // the Trash button stays where it was (hidden and out of reach while the
  // question is open). The question used to replace the Trash with two
  // worded buttons, which narrowed the words' column and re-wrapped them.
  // The safe choice takes the keyboard; Escape and Cancel give it back to
  // the Trash button.
  const deleteTriggerRef = useRef<HTMLButtonElement>(null);
  const keepButtonRef = useRef<HTMLButtonElement>(null);
  const wasConfirming = useRef(false);

  // Layout, not passive: the question's buttons leave in the same commit
  // that brings the trigger back, and a passive effect left the keyboard on
  // the page's body for a frame. After Delete the trigger holds it while
  // the request is out (it is `aria-disabled`, not disabled), keeps it if
  // the delete fails, and the panel moves it on once the row is gone.
  useLayoutEffect(() => {
    if (confirming) {
      wasConfirming.current = true;
      keepButtonRef.current?.focus();
    } else if (wasConfirming.current) {
      wasConfirming.current = false;
      deleteTriggerRef.current?.focus();
    }
  }, [confirming]);

  useEffect(() => {
    if (!confirming) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      // Stopped for the same reason MessageBubble stops it: Composer binds
      // Escape at the WINDOW to "stop generating".
      event.stopPropagation();
      event.preventDefault();
      onCancelDelete();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [confirming, onCancelDelete]);

  return (
    <div
      className="persona-card flex items-start gap-2"
      data-testid={`note-${entry.id}`}
      data-state={state}
      data-editing={editing || undefined}
      // Escape anywhere in the row while editing backs out. Stopped here,
      // at the row: Composer binds Escape at the WINDOW to "stop
      // generating", and React's handler runs before the window's.
      onKeyDown={(e) => {
        if (!editing || e.key !== "Escape") return;
        e.stopPropagation();
        e.preventDefault();
        if (!saving) cancelEdit();
      }}
    >
      <div className="min-w-0 flex-1">
        {/* The panel's own ink, named here: a line with none inherits the
            body's, which is the dark theme's text, and on this light panel
            that measured 1.02 to 1.07:1.

            While editing, the SAME spot holds a field with the same type,
            weight and ink; its border and padding are pulled back out with
            negative margins, so the words do not move. `field-sizing:
            content` makes it as tall as its text. */}
        {editing ? (
          <textarea
            ref={fieldRef}
            value={draftText}
            rows={1}
            maxLength={ENTRY_MAX_CHARS}
            aria-label={NOTE_ROW_WORDS.field}
            aria-invalid={saveFailed != null || undefined}
            aria-describedby={emptyDraft || saveFailed != null ? hintId : undefined}
            readOnly={saving}
            // ONE LINE, like the new-note box: a note is one line on the
            // server, so a pasted line break becomes a space here and
            // Enter, with or without Shift, saves.
            onChange={(e) => {
              setDraftText(e.target.value.replace(/[\r\n]+/g, " "));
              setSaveFailed(null);
            }}
            onKeyDown={(e) => {
              if (e.key !== "Enter" || e.nativeEvent.isComposing) return;
              e.preventDefault();
              void saveEdit();
            }}
            className="persona-field is-strong-focus block w-[calc(100%+10px)] min-w-0 resize-none rounded-sm border px-1 py-0 -mx-[5px] -my-px break-words text-ui font-semibold outline-none [field-sizing:content]"
          />
        ) : (
          <p
            className="break-words text-ui font-semibold"
            style={{ color: "var(--color-es-text-light)" }}
          >
            {entry.text}
          </p>
        )}
        {/* Why Save is off, or why it did not work. One line, in place;
            a failure keeps every word typed and says why, in the server's
            own terms. */}
        <Collapse open={editing && (emptyDraft || saveFailed != null)} className="min-w-0">
          <p
            id={hintId}
            className={saveFailed != null ? "persona-local-error" : "text-label leading-relaxed text-muted-foreground"}
            role={saveFailed != null ? "alert" : undefined}
            data-testid={`note-${entry.id}-edit-hint`}
          >
            {saveFailed ?? (emptyDraft ? EDIT_EMPTY : "")}
          </p>
        </Collapse>
        {state === "retired" && (
          <p className="text-label leading-relaxed text-muted-foreground">{NOTE_STATE_WORDS.retired}</p>
        )}
        {state === "over" && (
          <p className="text-label leading-relaxed text-muted-foreground">
            {NOTE_STATE_WORDS.over}
          </p>
        )}
        {state === "pinned_over" && (
          <p className="text-label leading-relaxed text-muted-foreground">
            {NOTE_STATE_WORDS.pinnedOver}
          </p>
        )}
        {/* A suggestion's "not sent until you keep it" is said once, by the
            waiting group it sits in, not on every row of it. */}
        {entry.provenance === "model" && entry.evidence_role === "assistant" && (
          <p className="text-label leading-relaxed text-muted-foreground">
            {NOTE_STATE_WORDS.fromReply}
          </p>
        )}
        {/* Who wrote it, truthfully after an edit too. `provenance` stays
            `model` (an edit cannot launder the model's words into the
            reader's), so the line says both halves. Read from `edited_at`,
            which the store writes only when a save really changed the words:
            it is on disk, so it survives a restart. A note the reader wrote
            has no such line, edited or not. */}
        {entry.provenance === "model" && (
          <p
            className="text-label leading-relaxed text-muted-foreground"
            data-testid={`note-${entry.id}-author`}
          >
            {entry.edited_at ? MODEL_NOTE_WORDS.edited : MODEL_NOTE_WORDS.wrote}
          </p>
        )}
        {original && (
          <p className="break-words text-label leading-relaxed text-muted-foreground">
            {TURKISH_LETTERS.test(original) ? "Türkçe aslı: " : "From: "}
            <span style={{ color: "var(--color-es-text-light)" }}>
              {"“"}{original}{"”"}
            </span>
          </p>
        )}
        {/* The delete question, under the words. "Delete" with the Trash
            glyph the trigger has (never the Check that means keep), and
            "Cancel", which takes the keyboard. */}
        <Collapse open={!proposed && confirming} className="min-w-0">
          {confirming && (
          <FadeIn className="flex items-center gap-2 pt-1">
            <>
              <PendingButton
                type="button"
                size="sm"
                variant="ghost"
                pending={busy}
                onClick={onDelete}
                className="persona-danger-action h-7 gap-1.5 px-2 text-ui"
              >
                <Trash2 size={12} className="size-3" />
                {NOTE_ROW_WORDS.confirmDelete}
              </PendingButton>
              <PendingButton
                ref={keepButtonRef}
                type="button"
                size="sm"
                variant="ghost"
                pending={busy}
                onClick={onCancelDelete}
                className="persona-ghost-action h-7 px-2 text-ui"
              >
                {NOTE_ROW_WORDS.cancelDelete}
              </PendingButton>
            </>
          </FadeIn>
          )}
        </Collapse>
      </div>

      {/* THE ROW'S BUTTONS, in slots that never move.

            Keep (a suggestion only), Pin, Edit (which IS Save while
            editing: one element, icon and name swapped), and last Discard
            (a suggestion) or Delete (an accepted note), which Cancel
            replaces while editing.

          While editing, Keep and Pin are HIDDEN, NOT REMOVED: `invisible`
          keeps their width, so the words' column does not re-wrap, and
          `inert` plus tabIndex -1 take them out of the tab order and the
          accessibility tree, so Tab goes field, Save, Cancel. One glyph, one
          meaning: Check keeps or saves, X discards, Undo2 takes back (Cancel
          edit), Trash deletes. Every button is `aria-disabled`, not
          `disabled`, while a request is out, so the keyboard stays on it. */}
      {proposed && (
        <FadeIn className={editing ? "invisible" : undefined}>
          <span
            className="contents"
            inert={editing || undefined}
            aria-hidden={editing || undefined}
          >
            <PendingButton
              type="button"
              size="sm"
              variant="ghost"
              pending={busy}
              onClick={() => {
                keepPressed.current = true;
                void onAccept().then((ok) => {
                  // A keep that failed leaves the row a suggestion, and its
                  // Keep button keeps the keyboard; a LATER keep of this row
                  // must not find this press still armed.
                  if (!ok) keepPressed.current = false;
                });
              }}
              tabIndex={editing ? -1 : undefined}
              aria-label={NOTE_ROW_WORDS.keep}
              title={NOTE_ROW_WORDS.keep}
              className="persona-ghost-action h-7 w-7 p-0"
            >
              <Check size={12} className="size-3" />
            </PendingButton>
          </span>
        </FadeIn>
      )}

      <span
        className={editing ? "contents invisible" : "contents"}
        inert={editing || undefined}
        aria-hidden={editing || undefined}
        data-testid={`note-${entry.id}-pin-slot`}
      >
        <PendingButton
          ref={pinRef}
          type="button"
          size="sm"
          variant="ghost"
          pending={busy}
          onClick={onPin}
          tabIndex={editing ? -1 : undefined}
          aria-label={entry.pinned ? NOTE_ROW_WORDS.unpin : NOTE_ROW_WORDS.pin}
          title={entry.pinned ? NOTE_ROW_WORDS.unpin : NOTE_ROW_WORDS.pin}
          className="persona-ghost-action h-7 w-7 p-0"
        >
          {entry.pinned ? <Pin size={12} className="size-3" /> : <PinOff size={12} className="size-3" />}
        </PendingButton>
      </span>

      {/* Edit, then Save: one button, icon and name swapped, never
          remounted. Off (`disabled`) only while the draft is empty, and the
          line under the field says why. */}
      <PendingButton
        ref={editButtonRef}
        type="button"
        size="sm"
        variant="ghost"
        pending={busy || saving}
        disabled={editing && emptyDraft}
        aria-describedby={editing && emptyDraft ? hintId : undefined}
        onClick={editing ? () => void saveEdit() : beginEdit}
        aria-label={editing ? NOTE_ROW_WORDS.save : NOTE_ROW_WORDS.edit}
        title={editing ? NOTE_ROW_WORDS.save : NOTE_ROW_WORDS.edit}
        className="persona-ghost-action h-7 w-7 p-0"
      >
        {saving ? (
          <Loader2 size={12} className="size-3 animate-spin" />
        ) : editing ? (
          <Check size={12} className="size-3" />
        ) : (
          <Pencil size={12} className="size-3" />
        )}
      </PendingButton>

      {editing && (
        <FadeIn>
          <PendingButton
            type="button"
            size="sm"
            variant="ghost"
            pending={saving}
            onClick={cancelEdit}
            aria-label={NOTE_ROW_WORDS.cancel}
            title={NOTE_ROW_WORDS.cancel}
            className="persona-ghost-action h-7 w-7 p-0"
          >
            <Undo2 size={12} className="size-3" />
          </PendingButton>
        </FadeIn>
      )}
      {!editing && proposed && (
        <FadeIn>
          <PendingButton
            ref={takeFocus(`discard-${entry.id}`)}
            type="button"
            size="sm"
            variant="ghost"
            pending={busy}
            // A click that is the SECOND of a double click is ignored:
            // Cancel sits in this same spot while editing. A keyboard press
            // has `detail` 0 and a single click 1; both go through.
            onClick={(e) => {
              if (e.detail > 1) return;
              onDiscard();
            }}
            aria-label={NOTE_ROW_WORDS.discard}
            title={NOTE_ROW_WORDS.discard}
            className="persona-ghost-action h-7 w-7 p-0"
          >
            <X size={12} className="size-3" />
          </PendingButton>
        </FadeIn>
      )}
      {!editing && !proposed && (
        <FadeIn className={confirming ? "invisible" : undefined}>
        <span
          className="contents"
          inert={confirming || undefined}
          aria-hidden={confirming || undefined}
        >
          <PendingButton
            ref={deleteTriggerRef}
            type="button"
            size="sm"
            variant="ghost"
            pending={busy}
            onClick={onAskDelete}
            tabIndex={confirming ? -1 : undefined}
            aria-label={NOTE_ROW_WORDS.delete}
            title={NOTE_ROW_WORDS.delete}
            // `persona-danger-action`, not ghost + an inline colour: the ghost
            // class sets its colour with !important, so the inline style never
            // applied. The token differs too - es-danger is the DARK shell's
            // red and lands at about 3.2:1 on this panel.
            className="persona-danger-action h-7 w-7 p-0"
          >
            <Trash2 size={12} className="size-3" />
          </PendingButton>
        </span>
        </FadeIn>
      )}
    </div>
  );
}
