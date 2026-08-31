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
 * The owner's rule is that a note never disappears, so none of these is ever
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
import { useEffect, useRef, useState } from "react";
import {
  Pin, PinOff, Plus, Trash2, X, Check, Loader2, Undo2, Search,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SlideIn } from "@/components/motion/SlideIn";
import { useUiStore } from "@/lib/store/uiStore";
import { useErrorStore } from "@/lib/errors/errorStore";
import {
  useNotebook,
  useCreateNote,
  usePatchNote,
  useDeleteNote,
  useAcceptNote,
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

export function NotebookPanel() {
  const chatId = useUiStore((s) => s.selectedChatId);
  const { data, isLoading, isError } = useNotebook(chatId);
  const create = useCreateNote();
  const patch = usePatchNote();
  const remove = useDeleteNote();
  const accept = useAcceptNote();
  const pushError = useErrorStore((s) => s.pushError);

  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  // Undo on this strip deletes every note in it at once, permanently, and it
  // had no confirmation at all - one press of a ghost button. The same
  // screen already asks before deleting ONE note (see `confirmDeleteId` and
  // the Confirm delete / Keep note pair on each row); this is that pattern,
  // not a new one.
  const [confirmUndo, setConfirmUndo] = useState(false);

  // The filter belongs to the chat it was typed in, and this panel does not
  // unmount when the chat changes - so without this the search follows the
  // reader into the next chat. That is not a cosmetic leak. The search box
  // only renders above five notes, so filtering a forty-note chat and then
  // opening a three-note one hides all three behind a filter whose input has
  // itself unmounted: an empty notebook, no visible cause, and no control
  // that would clear it.
  //
  // Adjusted during render rather than in an effect on purpose. An effect
  // runs after the DOM is painted, so the reader would still get one frame of
  // the new chat filtered to nothing - which is the exact frame being fixed.
  // React re-runs this render with the new state before committing anything.
  //
  // `confirmDeleteId` is deliberately NOT reset with it: notebook entry ids
  // are AUTOINCREMENT over the whole table, so an id carried in from another
  // chat matches no row here and the open question has nothing to attach to.
  //
  // `confirmUndo` has no such excuse and IS reset. It is a bare boolean, so
  // it carries into the next chat and arms a strip nobody armed: the reader
  // opens chat B and its Undo row is already asking "Confirm deleting N
  // notes" about a set they have never seen. That is the same unconfirmed
  // bulk delete this pair exists to prevent, one chat over.
  const [filterChatId, setFilterChatId] = useState(chatId);
  if (chatId !== filterChatId) {
    setFilterChatId(chatId);
    setQuery("");
    setConfirmUndo(false);
  }

  const busy = create.isPending || patch.isPending || remove.isPending
    || accept.isPending;

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
  const seen = useSeenNotesStore((s) =>
    chatId == null ? undefined : s.byChat[chatId]);
  const markSeen = useSeenNotesStore((s) => s.markSeen);
  const justSaved = all.filter(
    (e) => e.provenance === "model" && e.status === "accepted"
      && !e.retired_at && !(seen ?? []).includes(e.id));

  function acknowledge() {
    if (chatId != null) markSeen(chatId, justSaved.map((e) => e.id));
  }

  async function handleUndo() {
    // Deleted, not retired: the user is saying it should never have been
    // written. A retired row would go on sitting in the panel forever as a
    // fact they explicitly rejected.
    const doomed = justSaved.map((e) => e.id);
    if (chatId == null) return;
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
    if (removed.length) markSeen(chatId, removed);
  }

  async function handleAdd() {
    const text = draft.trim();
    if (!text || chatId == null) return;
    try {
      await create.mutateAsync([chatId, { text }]);
      setDraft("");
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

  async function handleAccept(id: number) {
    try {
      if (chatId == null) return;
      await accept.mutateAsync([id, chatId]);
    } catch (err) {
      pushError(err, "error", { chatId: chatId ?? undefined });
    }
  }

  async function handleDelete(id: number) {
    setConfirmDeleteId(null);
    try {
      if (chatId == null) return;
      await remove.mutateAsync([id, chatId]);
    } catch (err) {
      pushError(err, "error", { chatId: chatId ?? undefined });
    }
  }

  if (chatId == null) {
    return (
      <SlideIn>
        <section className="space-y-3 p-4">
          <p className="text-xs leading-relaxed text-muted-foreground">
            Open a chat to see what it has established.
          </p>
        </section>
      </SlideIn>
    );
  }

  return (
    <SlideIn>
      <section className="space-y-3 p-4" aria-label="Notebook">
        <div className="flex items-baseline justify-between">
          <h4
            className="text-xs font-semibold"
            style={{ color: "var(--color-es-text-light)" }}
          >
            Notebook
          </h4>
          {/* Persistent, not a toast. errorStore merges by code and chat, so a
              per-turn count would announce itself once and then go quiet just
              as the ceiling starts biting every turn. */}
          {/* `role="status"` because this number changes on its own: it is
              rewritten by the `done` frame of a turn the user spent in the
              composer, not by anything they did here. A note quietly going
              from sent to not sent is the one event in this panel a reader
              who is not watching it must still be told about. */}
          <span
            className="text-xs leading-relaxed text-muted-foreground"
            data-testid="notebook-sent-count"
            role="status"
          >
            {sent} of {total} sent
          </span>
        </div>

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
        {justSaved.length > 0 && (
          <div
            className="persona-card space-y-2"
            data-testid="just-saved"
            role="status"
          >
            <p className="text-xs leading-relaxed text-muted-foreground">
              Saved {justSaved.length === 1 ? "a note" : `${justSaved.length} notes`}{" "}
              the model wrote{justSaved.length === 1
                ? `: "${justSaved[0].text}"`
                : "."}
            </p>
            <div className="flex items-center gap-2">
              {confirmUndo ? (
                <>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => void handleUndo()}
                    aria-label={justSaved.length === 1
                      ? "Confirm delete"
                      : `Confirm deleting ${justSaved.length} notes`}
                    className="persona-danger-action h-7 gap-1.5 px-2 text-xs"
                  >
                    <Check size={12} className="size-3" />
                    Delete {justSaved.length === 1
                      ? "it" : `all ${justSaved.length}`}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => setConfirmUndo(false)}
                    aria-label="Keep note"
                    className="persona-ghost-action h-7 px-2 text-xs"
                  >
                    Cancel
                  </Button>
                </>
              ) : (
                <>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => setConfirmUndo(true)}
                    className="persona-danger-action h-7 gap-1.5 px-2 text-xs"
                  >
                    <Undo2 size={12} className="size-3" />
                    Undo
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={acknowledge}
                    className="persona-ghost-action h-7 px-2 text-xs"
                  >
                    Keep {justSaved.length === 1 ? "it" : "them"}
                  </Button>
                </>
              )}
            </div>
          </div>
        )}

        {/* Only once there are enough notes for the eye to lose one. A search
            box over four rows is furniture. */}
        {all.length > 5 && (
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
              aria-label="Search notes"
              onChange={(e) => setQuery(e.target.value)}
              className="persona-field pl-7 text-xs md:text-xs"
            />
            {query !== "" && (
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
            )}
          </div>
        )}

        <div className="flex items-center gap-2">
          <Input
            value={draft}
            maxLength={ENTRY_MAX_CHARS}
            placeholder="Something this story has established..."
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
          // island, and `md:text-xs` because Input's own cva base
          // ends in `md:text-sm` - same specificity, later in the
          // sheet, so a bare `text-xs` loses above 768px and this
          // panel is never narrower than that in practice.
          className="persona-field text-xs md:text-xs"
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
            {create.isPending ? (
              <Loader2 size={12} className="size-3 animate-spin" />
            ) : (
              <Plus size={12} className="size-3" />
            )}
          </Button>
        </div>

        {isLoading && <p className="text-xs leading-relaxed text-muted-foreground">Loading...</p>}

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
        {!isLoading && isError && (
          <p
            className="text-xs leading-relaxed text-muted-foreground"
            role="status"
          >
            Notes could not be loaded. They are still saved - this panel just
            could not read them. Try again in a moment.
          </p>
        )}

        {/* One or the other, never both. `entries` is the FILTERED list, so
            an empty notebook and a filter that matched nothing produce the
            same empty list and used to print both sentences at once: "Nothing
            yet." sitting directly above "No note matches that. 3 are still
            here." The two contradict each other, and one of them is telling
            the reader their notes are gone. The filter decides which is
            true. */}
        {!isLoading && !isError && entries.length === 0 && query === "" && (
          <p className="text-xs leading-relaxed text-muted-foreground">
            Nothing yet. Notes are sent with every message, so the character
            stops forgetting what you write here.
          </p>
        )}

        {/* `role="status"`: the list empties out under the reader's own
            typing, and for anyone not watching the rows the count is the only
            evidence that the notes are still there and only hidden. */}
        {!isLoading && !isError && entries.length === 0 && query !== "" && (
          <p
            className="text-xs leading-relaxed text-muted-foreground"
            role="status"
          >
            No note matches that. {all.length} are still here - the search
            covers the note and the words it was taken from.
          </p>
        )}

        {/* Once, not on every row. The per-row version told a Turkish-speaking
            owner about their own language's model support every few lines,
            which is a lecture rather than a note. */}
        {entries.some((e) => e.provenance === "model") && (
          <p className="text-xs leading-relaxed text-muted-foreground">
            Suggested notes are written in English - the reader is a small
            model and reads English far better. What was actually said is kept
            underneath, word for word.
          </p>
        )}

        <div className="space-y-1">
          {entries.map((entry) => (
            <NoteRow
              key={entry.id}
              entry={entry}
              busy={busy}
              confirming={confirmDeleteId === entry.id}
              onPin={() => void handlePin(entry)}
              onAccept={() => void handleAccept(entry.id)}
              onAskDelete={() => setConfirmDeleteId(entry.id)}
              onCancelDelete={() => setConfirmDeleteId(null)}
              onDelete={() => void handleDelete(entry.id)}
            />
          ))}
        </div>
      </section>
    </SlideIn>
  );
}

function NoteRow({
  entry,
  busy,
  confirming,
  onPin,
  onAccept,
  onAskDelete,
  onCancelDelete,
  onDelete,
}: {
  entry: NotebookEntry;
  busy: boolean;
  confirming: boolean;
  onPin: () => void;
  onAccept: () => void;
  onAskDelete: () => void;
  onCancelDelete: () => void;
  onDelete: () => void;
}) {
  const state = noteState(entry);
  const original = originalOf(entry);

  // The confirm REPLACES its trigger, which means React unmounts the element
  // the keyboard was standing on and focus drops to <body>. From there the
  // question the user just opened is reachable only by tabbing in from the
  // top of the document, and Escape answered nothing. A keyboard user could
  // ask to delete a note and then not practically be able to finish, or back
  // out. MessageBubble's confirm already solves this; this is the same three
  // behaviours - autofocus, Escape closes, focus returns to the trigger.
  //
  // With one deliberate difference: MessageBubble focuses the DESTRUCTIVE
  // button, so a reflexive Enter deletes. The SAFE choice takes the focus
  // here, so the same reflex keeps the note. The destructive button is one
  // Tab away, which is the right amount of work for the irreversible one.
  const deleteTriggerRef = useRef<HTMLButtonElement>(null);
  const keepButtonRef = useRef<HTMLButtonElement>(null);
  // Whether focus is OURS to give back. Without it every row in the list
  // would grab focus on its first render, when nothing has been confirmed.
  const wasConfirming = useRef(false);

  useEffect(() => {
    if (confirming) {
      wasConfirming.current = true;
      keepButtonRef.current?.focus();
    } else if (wasConfirming.current) {
      wasConfirming.current = false;
      // The trigger was remounted by the render this effect follows, so the
      // ref points at a live element again by the time this runs.
      deleteTriggerRef.current?.focus();
    }
  }, [confirming]);

  useEffect(() => {
    if (!confirming) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      // Stopped for the same reason MessageBubble stops it: Composer binds
      // Escape at the WINDOW to "stop generating", and document bubbles to
      // window - so one press would both dismiss this question and kill a
      // reply streaming in the chat behind the panel.
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
    >
      <div className="min-w-0 flex-1">
        <p className="break-words text-sm font-medium">{entry.text}</p>
        {/* The two ways a note stops being sent read the same in a plain list
            and are not the same thing, so each says which it is. */}
        {state === "retired" && (
          <p className="text-xs leading-relaxed text-muted-foreground">Replaced by a newer note - not sent.</p>
        )}
        {state === "over" && (
          <p className="text-xs leading-relaxed text-muted-foreground">
            Did not fit this turn - kept, not sent. Pin it to protect it.
          </p>
        )}
        {state === "pinned_over" && (
          <p className="text-xs leading-relaxed text-muted-foreground">
            Did not fit this turn - kept, not sent. The pinned notes alone
            are over the limit, so unpin one to make room.
          </p>
        )}
        {state === "proposed" && (
          <p className="text-xs leading-relaxed text-muted-foreground">
            Waiting for you - not sent until you keep it.
          </p>
        )}
        {/* The distinction that matters inside "written by the model": a note
            taken from what the USER said is a paraphrase of something that was
            really said, and one taken from the model's own reply may be the
            model's own invention - the verbatim check cannot tell, because it
            passes by construction when the model quotes itself. Marked, not
            withheld: the research on review queues says an honest label
            somebody can see beats a queue nobody reads. */}
        {entry.provenance === "model" && entry.evidence_role === "assistant" && (
          <p className="text-xs leading-relaxed text-muted-foreground">
            Taken from the model's own reply, not from something you wrote.
          </p>
        )}
        {entry.provenance === "model" && (
          <p className="text-xs leading-relaxed text-muted-foreground">
            Written by the model.
          </p>
        )}
        {/* What was actually said, verbatim, in whichever language it was
            said in. Without it an English paraphrase of a Turkish sentence
            cannot be checked against anything. */}
        {original && (
          <p className="break-words text-xs leading-relaxed text-muted-foreground">
            {TURKISH_LETTERS.test(original) ? "Türkçe aslı: " : "From: "}
            <span style={{ color: "var(--color-es-text-light)" }}>
              {"“"}{original}{"”"}
            </span>
          </p>
        )}
      </div>

      {/* Only for a proposal, and it is the only thing that promotes one:
          `provenance` stays `model` forever, so the row keeps saying who
          wrote it after the user keeps it. */}
      {state === "proposed" && (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={busy}
          onClick={onAccept}
          aria-label="Keep suggestion"
          className="persona-ghost-action h-7 w-7 p-0"
        >
          <Check size={12} className="size-3" />
        </Button>
      )}

      {/* One button per action, icon swapped in place - no second control
          appears and nothing remounts when the state flips. */}
      <Button
        type="button"
        size="sm"
        variant="ghost"
        disabled={busy}
        onClick={onPin}
        aria-label={entry.pinned ? "Unpin note" : "Pin note"}
        className="persona-ghost-action h-7 w-7 p-0"
      >
        {entry.pinned ? <Pin size={12} className="size-3" /> : <PinOff size={12} className="size-3" />}
      </Button>

      {!confirming && (
        <Button
          ref={deleteTriggerRef}
          type="button"
          size="sm"
          variant="ghost"
          disabled={busy}
          onClick={onAskDelete}
          aria-label="Delete note"
          // `persona-danger-action`, not ghost + an inline colour: the ghost
          // class sets its colour with !important, so the inline style never
          // applied and the trash rendered grey here while the identical one
          // in Limits rendered maroon. The token differs too - es-danger is
          // the DARK shell's red and lands at about 3.2:1 on this panel.
          className="persona-danger-action h-7 w-7 p-0"
        >
          <Trash2 size={12} className="size-3" />
        </Button>
      )}
      {confirming && (
        <>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={onDelete}
            aria-label="Confirm delete"
            className="persona-danger-action h-7 w-7 p-0"
          >
            <Check size={12} className="size-3" />
          </Button>
          <Button
            ref={keepButtonRef}
            type="button"
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={onCancelDelete}
            aria-label="Keep note"
            className="persona-ghost-action h-7 w-7 p-0"
          >
            <X size={12} className="size-3" />
          </Button>
        </>
      )}
    </div>
  );
}
