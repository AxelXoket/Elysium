/**
 * BoundaryPanel - the limits, and the switch that sets them aside.
 *
 * Separate from the notebook and deliberately so. A note is something the
 * story established; a limit is something the person decided, and the two have
 * opposite lifetimes: notes belong to one chat and are trimmed when the budget
 * runs short, limits belong everywhere and are never trimmed at all. Putting
 * them in one list would invite the same treatment for both.
 *
 * The severity scale is not invented. Tabletop roleplay solved this decades
 * ago with lines and veils: a LINE never appears, a VEIL happens with the
 * camera turned away. The third level is the soft preference between them.
 */
import { SlideIn } from "@/components/motion/SlideIn";
import { useEffect, useRef, useState } from "react";
import { Plus, Trash2, Check, X, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useUiStore } from "@/lib/store/uiStore";
import { useErrorStore } from "@/lib/errors/errorStore";
import {
  useChatBoundaries,
  useGlobalBoundaries,
  useCreateBoundary,
  useDeleteBoundary,
  useSetUseGlobalBoundaries,
  useSafeword,
  useSetSafeword,
} from "@/lib/query/notebook";
import type { Boundary } from "@/lib/schemas/notebook";

/**
 * notebook_store.BOUNDARY_MAX_CHARS. The field used to stop at 240 - the NOTE
 * ceiling - which was the wrong number twice over: the backend had no ceiling
 * for limits at all, so the cap read as enforcement while being nothing of the
 * kind, and 240 is the figure for a block that gets TRIMMED. Limits are never
 * trimmed, so theirs is smaller, and it is arithmetic: see the derivation on
 * the constant itself.
 *
 * Exported so the test can hold the field to this number instead of hard-coding
 * a copy of it, which is how the two would drift apart without anything saying
 * so. The real enforcement is create_boundary's, and a limit that gets past
 * this field by any other route still comes back as `boundary_too_long`.
 */
export const BOUNDARY_MAX_CHARS = 160;

const SEVERITY_LABEL: Record<string, string> = {
  hard: "never",
  veiled: "off the page",
  soft: "prefer not",
};

export function BoundaryPanel() {
  const chatId = useUiStore((s) => s.selectedChatId);
  const { data, isError: chatFailed } = useChatBoundaries(chatId);
  // Called unconditionally, per the rules of hooks - the hook takes no
  // `enabled` of its own to gate on chatId. With a chat open its answer goes
  // unused below: `data` above is already the MERGED view, global rows
  // included, because that is what list_chat_boundaries returns and what the
  // "use my global limits here" switch controls. With no chat open this is
  // the only source of truth the panel has left, because useChatBoundaries
  // is disabled entirely there - and before this fix nothing stood in its
  // place but an empty array, so the panel said "No limits set." to someone
  // whose global limits were sitting in the database the whole time. A
  // safety feature that reads as gone is worse than one that costs an extra
  // request to prove it is not, so this is called every render rather than
  // only when a chat is closed.
  const globalBoundaries = useGlobalBoundaries();
  const create = useCreateBoundary();
  const remove = useDeleteBoundary();
  const setUseGlobal = useSetUseGlobalBoundaries();
  const pushError = useErrorStore((s) => s.pushError);

  const [label, setLabel] = useState("");
  const [severity, setSeverity] = useState("hard");
  // What should happen when the limit is crossed, and how far the scene may
  // go at all. Both columns have been collected, validated and stored since
  // the table was written, and neither had a control here or a line in the
  // prompt - so the app could store "stop the scene" and tell nobody.
  const [onViolation, setOnViolation] = useState("pause");
  const [rating, setRating] = useState("");
  // Global by default: a limit is usually about the person, not the scene.
  const [scope, setScope] = useState<"global" | "chat">("global");

  // Saved on blur, per A50: the vault can lock mid-thought, and a buffer
  // somebody typed and never committed is a buffer the lock eats.
  const safewordQuery = useSafeword();
  const saveWord = useSetSafeword();
  const [safeword, setSafeword] = useState<string | null>(null);
  const shown = safeword ?? safewordQuery.data?.word ?? "";

  async function saveSafeword() {
    if (safeword === null || safeword === (safewordQuery.data?.word ?? "")) return;
    try {
      await saveWord.mutateAsync([safeword]);
    } catch (err) {
      pushError(err, "error");
    } finally {
      // THE LOCAL VALUE STOPS OVERRIDING THE SERVER'S, both ways.
      //
      // `shown` is `safeword ?? server`, and nothing ever cleared `safeword`
      // - so after a POST that returned 500 the box went on displaying the
      // word somebody typed, indistinguishable from a word that had been
      // saved. For this control that is the worst available failure: a
      // safeword is a thing you believe is protecting you, and believing it
      // is set when it is not is the whole feature inverted.
      //
      // In `finally`, not in the success branch: the failure case is the one
      // that needs it. The mutation invalidates the query on success, so the
      // box then reads the value the server actually holds; on failure it
      // falls back to the value the server still holds, which is the old
      // one, and the difference is visible.
      setSafeword(null);
    }
  }
  const [confirmId, setConfirmId] = useState<number | null>(null);
  // Optimistic only while a save is in flight; the server's answer is the
  // truth the rest of the time.
  const [pendingGlobal, setPendingGlobal] = useState<boolean | null>(null);
  const useGlobal = pendingGlobal ?? data?.use_global ?? true;

  // `saveWord.isPending` belongs here as much as the other three. Without
  // it the field stayed writable while its own POST was in flight, so a
  // second edit could be typed over a value that was still being saved and
  // the two answers raced - and whichever landed last won, silently.
  const busy = create.isPending || remove.isPending || setUseGlobal.isPending
    || saveWord.isPending;
  // With no chat open there is nothing to merge global limits INTO, so the
  // global set is shown as-is rather than as an always-empty per-chat list.
  const rows: Boundary[] = chatId == null
    ? (globalBoundaries.data?.boundaries ?? [])
    : (data?.boundaries ?? []);

  // A failed load is not an absence of limits. Neither query's `isError` was
  // ever read, so a 500 produced `data === undefined`, an empty `rows`, and
  // the sentence "No limits set." - about limits the reader had written down
  // precisely so a model would not cross them. Whichever query the panel is
  // actually reading from is the one that has to be believed.
  const failed = chatId == null ? globalBoundaries.isError : chatFailed;

  async function handleAdd() {
    const text = label.trim();
    if (!text) return;
    try {
      // One field, used twice: what the person reads and what the model reads
      // start identical. They are separate columns so the wording can diverge
      // later without the screen changing under them.
      await create.mutateAsync([{
        label: text, phrasing: text, severity,
        on_violation: onViolation,
        // Omitted rather than sent empty: the column is nullable and its
        // CHECK does not allow "".
        ...(rating ? { rating_ceiling: rating } : {}),
        // The scope the owner asked for. `chat_id` was never passed, so every
        // limit the app could create was GLOBAL - the row below rendered
        // "- this chat" for something it had no way to produce, and turning
        // "use my global limits here" off hid every limit the user had ever
        // written.
        ...(scope === "chat" && chatId != null ? { chat_id: chatId } : {}),
      }]);
      setLabel("");
    } catch (err) {
      pushError(err, "error", { chatId: chatId ?? undefined });
    }
  }

  async function handleToggleGlobal(next: boolean) {
    if (chatId == null) return;
    setPendingGlobal(next);
    try {
      await setUseGlobal.mutateAsync([chatId, next]);
    } catch (err) {
      pushError(err, "error", { chatId });
    } finally {
      // Hand it back to the server either way: on success the refetch already
      // carries the new value, and on failure holding a local guess is how the
      // switch starts disagreeing with the vault.
      setPendingGlobal(null);
    }
  }

  return (
    <SlideIn>
    <section className="space-y-3 p-4" aria-label="Limits">
      {/* `settings-section-title` is painted for the DARK settings dialog -
          rgba(202,212,224,.62) - and this panel lives in `.glass-right`, the
          app's one light island. It rendered at roughly 1.2:1 here: present in
          the DOM, invisible on screen. The heading vocabulary of this surface
          is the one its siblings use. */}
      <h4
        className="text-xs font-semibold"
        style={{ color: "var(--color-es-text-light)" }}
      >
        Limits
      </h4>

      {/* Above the list on purpose. Everything below this is a paragraph the
          model is asked to honour; this one is matched in code before the
          request is built, and when it matches the request never exists. It
          is the only thing on this panel that does not depend on a model
          agreeing, so it says so and it goes first. */}
      <div className="persona-card space-y-1">
        <label className="block space-y-1">
          <span className="text-xs leading-relaxed text-muted-foreground">
            Safeword - stops a message before it is sent
          </span>
          <Input
            value={shown}
            maxLength={64}
            placeholder="A word you would never write by accident..."
            disabled={busy || !safewordQuery.isSuccess}
            onChange={(e) => setSafeword(e.target.value)}
            onBlur={() => void saveSafeword()}
            onKeyDown={(e) => {
              if (e.key === "Enter") void saveSafeword();
            }}
            aria-label="Safeword"
            className="persona-field text-xs md:text-xs"
          />
        </label>
        <p className="text-xs leading-relaxed text-muted-foreground">
          Put it anywhere in a message and nothing is sent - not the message,
          not your notes, not these limits - and nothing is saved. This one is
          not a request to the model; it is checked here. Leave it empty to
          turn it off.
        </p>
      </div>

      <p className="text-xs leading-relaxed text-muted-foreground">
        Sent with every message and never trimmed to make room. If they do not
        fit, nothing is sent at all.
      </p>

      {chatId != null && (
        <label className="flex items-center gap-2">
          <Switch
            checked={useGlobal}
            disabled={busy}
            onCheckedChange={(v) => void handleToggleGlobal(v)}
          />
          <span className="text-xs leading-relaxed text-muted-foreground">Use my global limits here</span>
        </label>
      )}

      <div className="flex items-center gap-2">
        <Input
          value={label}
          maxLength={BOUNDARY_MAX_CHARS}
          placeholder="Something to keep out of the story..."
          disabled={busy}
          onChange={(e) => setLabel(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void handleAdd();
          }}
          className="persona-field text-xs md:text-xs"
        />
        <select
          value={scope}
          disabled={busy || chatId == null}
          onChange={(e) => setScope(e.target.value as "global" | "chat")}
          aria-label="Where this applies"
          className="persona-field h-8 min-w-0 rounded-md px-2 text-xs"
        >
          <option value="global">everywhere</option>
          <option value="chat">this chat</option>
        </select>
        <select
          value={severity}
          disabled={busy}
          onChange={(e) => setSeverity(e.target.value)}
          aria-label="How strict"
          // `.persona-field` is this surface's form-control class, focus ring
          // included. The hand-written rgba below it was a third unmanaged
          // variant of one control, and `settings-value` also carried an 11px
          // size that beat the `text-xs` beside it - so the same select
          // rendered at two sizes in two sibling panels.
          className="persona-field h-8 min-w-0 rounded-md px-2 text-xs"
        >
          <option value="hard">never</option>
          <option value="veiled">off the page</option>
          <option value="soft">prefer not</option>
        </select>
        <select
          value={onViolation}
          disabled={busy}
          onChange={(e) => setOnViolation(e.target.value)}
          aria-label="What to do if it happens"
          data-testid="boundary-on-violation"
          className="persona-field h-8 min-w-0 rounded-md px-2 text-xs"
        >
          <option value="pause">pause there</option>
          <option value="rewind">go back and take it another way</option>
          <option value="fast_forward">skip past it</option>
          <option value="hard_stop">stop the scene</option>
        </select>
        <select
          value={rating}
          disabled={busy}
          onChange={(e) => setRating(e.target.value)}
          aria-label="Rating ceiling"
          data-testid="boundary-rating"
          className="persona-field h-8 min-w-0 rounded-md px-2 text-xs"
        >
          <option value="">no rating limit</option>
          <option value="G">G</option>
          <option value="PG">PG</option>
          <option value="PG-13">PG-13</option>
          <option value="R">R</option>
        </select>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={busy || !label.trim()}
          onClick={() => void handleAdd()}
          aria-label="Add limit"
          className="persona-ghost-action h-7 w-7 p-0"
        >
          {create.isPending ? (
            <Loader2 size={12} className="size-3 animate-spin" />
          ) : (
            <Plus size={12} className="size-3" />
          )}
        </Button>
      </div>

      {failed && (
        <p
          className="text-xs leading-relaxed text-muted-foreground"
          role="status"
        >
          Limits could not be loaded. They are still saved - this panel just
          could not read them. Try again in a moment.
        </p>
      )}

      {!failed && rows.length === 0 && <p className="text-xs leading-relaxed text-muted-foreground">No limits set.</p>}

      <div className="space-y-1">
        {rows.map((row) => (
          <BoundaryRow
            key={row.id}
            row={row}
            busy={busy}
            confirming={confirmId === row.id}
            onAskDelete={() => setConfirmId(row.id)}
            onCancelDelete={() => setConfirmId(null)}
            onDelete={() => {
              setConfirmId(null);
              // A global limit belongs to no chat, so it carries no
              // scope; a chat-scoped one may only be removed from
              // the chat it was written in.
              void remove.mutateAsync([
                row.id, row.scope === "global" ? null : chatId,
              ]).catch((err) =>
                pushError(err, "error", { chatId: chatId ?? undefined }),
              );
            }}
          />
        ))}
      </div>
    </section>
    </SlideIn>
  );
}

function BoundaryRow({
  row,
  busy,
  confirming,
  onAskDelete,
  onCancelDelete,
  onDelete,
}: {
  row: Boundary;
  busy: boolean;
  confirming: boolean;
  onAskDelete: () => void;
  onCancelDelete: () => void;
  onDelete: () => void;
}) {
  // The house confirm pattern, copied from NotebookPanel's NoteRow rather
  // than reinvented: the confirm buttons REPLACE the trigger in place, so
  // React unmounts the element the keyboard was standing on and focus falls
  // silently to <body>. From there the question just opened is reachable
  // only by tabbing in from the top of the document, and Escape answered
  // nothing - a keyboard user asking to delete a limit could not practicably
  // finish, or back out. A limit is the thing this whole panel exists to
  // protect, so its own delete confirm gets no less care than the notebook's.
  //
  // Same three behaviours, same reasoning: focus lands on the SAFE button so
  // a reflexive Enter keeps the limit rather than deleting it, Escape cancels
  // without bubbling into Composer's own binding, and focus returns to the
  // trigger on cancel so the keyboard is left where it was.
  const deleteTriggerRef = useRef<HTMLButtonElement>(null);
  const keepButtonRef = useRef<HTMLButtonElement>(null);
  // Whether focus is ours to give back - without it every row would grab
  // focus on its first render, when nothing has been confirmed yet.
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
      // Stopped for the same reason NoteRow stops it: Composer binds Escape
      // at the WINDOW to "stop generating", and document bubbles to window -
      // so one press would both dismiss this question and kill a reply
      // streaming in the chat behind the panel.
      event.stopPropagation();
      event.preventDefault();
      onCancelDelete();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [confirming, onCancelDelete]);

  return (
    <div
      data-testid={`boundary-${row.id}`}
      className="persona-card flex items-start gap-2"
    >
      <div className="min-w-0 flex-1">
        <p className="break-words text-sm font-medium">{row.label}</p>
        <p className="text-xs leading-relaxed text-muted-foreground">
          {SEVERITY_LABEL[row.severity] ?? row.severity}
          {row.scope === "global" ? " - everywhere" : " - this chat"}
        </p>
      </div>
      {!confirming && (
        <Button
          ref={deleteTriggerRef}
          type="button"
          size="sm"
          variant="ghost"
          disabled={busy}
          onClick={onAskDelete}
          aria-label="Delete limit"
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
            aria-label="Confirm delete limit"
            className="persona-danger-action h-7 w-7 p-0"
            onClick={onDelete}
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
            aria-label="Keep limit"
            className="persona-ghost-action h-7 w-7 p-0"
          >
            <X size={12} className="size-3" />
          </Button>
        </>
      )}
    </div>
  );
}
