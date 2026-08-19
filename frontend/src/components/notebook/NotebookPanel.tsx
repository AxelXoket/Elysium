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
import { useState } from "react";
import { Pin, PinOff, Plus, Trash2, X, Check, Loader2 } from "lucide-react";

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

function noteState(
  entry: NotebookEntry,
): "live" | "proposed" | "retired" | "over" {
  if (entry.retired_at) return "retired";
  // Before `excluded_reason`: a proposal is not being sent for a much more
  // important reason than not fitting, and reading as "live" would tell the
  // user a note is in force while it is still waiting for them.
  if (entry.status === "proposed") return "proposed";
  if (entry.excluded_reason) return "over";
  return "live";
}

export function NotebookPanel() {
  const chatId = useUiStore((s) => s.selectedChatId);
  const { data, isLoading } = useNotebook(chatId);
  const create = useCreateNote();
  const patch = usePatchNote();
  const remove = useDeleteNote();
  const accept = useAcceptNote();
  const pushError = useErrorStore((s) => s.pushError);

  const [draft, setDraft] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);

  const busy = create.isPending || patch.isPending || remove.isPending
    || accept.isPending;
  const entries = data?.entries ?? [];
  // The number the SERVER computed for the last turn, when there has been
  // one. Counting live notes here is a client-side guess that is right only
  // while nothing was trimmed - and the case where something was is the whole
  // reason this number is on screen. Before a turn has run there is nothing to
  // report, so the count stands in.
  const turn = useContextNotesStore((s) =>
    chatId == null ? undefined : s.byChat[chatId]);
  const live = entries.filter((e) => noteState(e) === "live").length;
  const sent = turn?.notebook_sent ?? live;
  const total = turn?.notebook_total ?? entries.length;

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
      await patch.mutateAsync([entry.id, { pinned: !entry.pinned }]);
    } catch (err) {
      pushError(err, "error", { chatId: chatId ?? undefined });
    }
  }

  async function handleAccept(id: number) {
    try {
      await accept.mutateAsync([id]);
    } catch (err) {
      pushError(err, "error", { chatId: chatId ?? undefined });
    }
  }

  async function handleDelete(id: number) {
    setConfirmDeleteId(null);
    try {
      await remove.mutateAsync([id]);
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
          <span className="text-xs leading-relaxed text-muted-foreground" data-testid="notebook-sent-count">
            {sent} of {total} sent
          </span>
        </div>

        <div className="flex items-center gap-2">
          <Input
            value={draft}
            maxLength={ENTRY_MAX_CHARS}
            placeholder="Something this story has established..."
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

        {!isLoading && entries.length === 0 && (
          <p className="text-xs leading-relaxed text-muted-foreground">
            Nothing yet. Notes are sent with every message, so the character
            stops forgetting what you write here.
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
        {state === "proposed" && (
          <p className="text-xs leading-relaxed text-muted-foreground">
            Waiting for you - not sent until you keep it.
          </p>
        )}
        {entry.provenance === "model" && (
          <p className="text-xs leading-relaxed text-muted-foreground">Written by the model.</p>
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
