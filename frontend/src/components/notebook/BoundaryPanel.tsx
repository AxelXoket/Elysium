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
import { Collapse } from "@/components/motion/Collapse";
import { FadeIn } from "@/components/motion/FadeIn";
import { useEffect, useId, useRef, useState } from "react";
import { Plus, Trash2, Check, X, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SwitchRow } from "@/components/settings/SwitchRow";
import { settledAt } from "@/components/notebook/useRetryPress";
import { SettingUnread } from "@/components/notebook/SettingUnread";
import { LIMITS_UNREAD, SETTING_LOADING } from "@/components/notebook/noteProse";
import { Disclosure } from "@/components/notebook/Disclosure";
import {
  DISCLOSURE_WORDS,
  SAFEWORD_HOW,
  SAFEWORD_LABEL,
  SAFEWORD_LINE,
  SAFEWORD_OFF,
  SAFEWORD_PROMPT,
  LIMITS_LINE,
  NEW_LIMIT_PROMPT,
} from "@/components/notebook/limitsProse";
import { useUiStore } from "@/lib/store/uiStore";
import {
  LIMIT_DRAFT_FIELDS,
  PANEL_DRAFT_KEYS,
  chatScopedPanelDraftKey,
  chatScopedPanelDraftPrefix,
  useDraftStore,
  usePanelDraft,
} from "@/lib/store/draftStore";
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
import {
  BOUNDARY_ON_VIOLATION_ITEMS,
  BOUNDARY_RATING_ITEMS,
  BOUNDARY_SCOPE_ITEMS,
  BOUNDARY_SELECT_LABELS,
  BOUNDARY_SEVERITY_ITEMS,
} from "@/lib/notebook/labels";
import type { Boundary } from "@/lib/schemas/notebook";

/** The safeword box's id, so "Try again" can hand it focus once the saved
 *  word has been read. One panel, one safeword box, so one id. */
const SAFEWORD_FIELD = "safeword-field";

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

/**
 * One labelled qualifier.
 *
 * The label wraps the trigger so clicking the words opens it, and it names
 * the trigger through `aria-labelledby` rather than through the wrapping
 * alone: the trigger is a `<button role="combobox">`, and whether a button
 * takes its name from a wrapping label is engine-dependent - the fallback is
 * the button's own contents, which here would be the chosen value. Pointing
 * at the words removes the question. It is `aria-labelledby` and not
 * `aria-label` for the same reason in reverse: `aria-label` would replace
 * the words on screen with a second copy that can drift from them.
 */
function LimitQualifier({
  label,
  value,
  items,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  items: Record<string, string>;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const labelId = useId();
  return (
    <label className="block min-w-0 space-y-1">
      <span
        id={labelId}
        className="block text-ui leading-relaxed text-muted-foreground"
      >
        {label}
      </span>
      <Select
        value={value}
        disabled={disabled}
        onValueChange={(v) => {
          if (v != null) onChange(String(v));
        }}
        items={items}
      >
        <SelectTrigger
          aria-labelledby={labelId}
          className="persona-field h-8 w-full min-w-0 rounded-md px-2 text-ui"
        >
          {/* The words sit in a span that truncates and carries the whole
              wording as its title, the way the strip's scope field does. At
              the 300px floor a qualifier's field has about 81px for its
              words, and the longest action, "go back and take it another
              way", is 184px of them: cut with no ellipsis and nothing left to
              read it by (KURAL 4.4). The base string makes the value itself a
              flex box, and text inside a flex box cannot end in an ellipsis,
              so the span is what can. */}
          <SelectValue>
            {(chosen: string) => (
              <span className="truncate" title={items[chosen] ?? chosen}>
                {items[chosen] ?? chosen}
              </span>
            )}
          </SelectValue>
        </SelectTrigger>
        <SelectContent>
          {Object.entries(items).map(([itemValue, itemLabel]) => (
            <SelectItem key={itemValue} value={itemValue}>
              {itemLabel}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </label>
  );
}


export function BoundaryPanel() {
  const chatId = useUiStore((s) => s.selectedChatId);
  const chatBoundaries = useChatBoundaries(chatId);
  const { data, isError: chatFailed } = chatBoundaries;
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

  // Store-backed for the same reason the note box next door is: this panel
  // is unmounted by an ordinary tab click, and a half-written limit is text
  // the user typed. The selects below stay local - they have defaults, not
  // contents.
  // Keyed on the chat for the same reason the note box is: the scope select
  // below can file this limit into the open chat, so the buffer must not
  // follow the reader into a different one.
  //
  // The four qualifiers are held with the label rather than beside it. They
  // are what the limit MEANS, and a label restored next to four controls
  // that had snapped back to their defaults would put a hard global limit
  // where the user had written a soft one for this chat - with their own
  // words on screen saying it was theirs. This panel is the one place in the
  // app where that particular lie costs the most.
  const limitKey = (field: string) =>
    chatScopedPanelDraftKey(PANEL_DRAFT_KEYS.newLimit, chatId, field);
  const [label, setLabel] = usePanelDraft(limitKey(LIMIT_DRAFT_FIELDS.label));
  const clearPanelDraftsWithPrefix = useDraftStore(
    (s) => s.clearPanelDraftsWithPrefix,
  );
  const [severity, setSeverity] = usePanelDraft(
    limitKey(LIMIT_DRAFT_FIELDS.severity),
    "hard",
  );
  // What should happen when the limit is crossed, and how far the scene may
  // go at all. Both columns have been collected, validated and stored since
  // the table was written, and neither had a control here or a line in the
  // prompt - so the app could store "stop the scene" and tell nobody.
  const [onViolation, setOnViolation] = usePanelDraft(
    limitKey(LIMIT_DRAFT_FIELDS.onViolation),
    "pause",
  );
  const [rating, setRating] = usePanelDraft(limitKey(LIMIT_DRAFT_FIELDS.rating));
  // Global by default: a limit is usually about the person, not the scene.
  const [storedScope, setScope] = usePanelDraft(
    limitKey(LIMIT_DRAFT_FIELDS.scope),
    "global",
  );
  // The buffer is a string, and only two of them mean anything here. Anything
  // else is read as the default rather than trusted into the payload.
  const scope: "global" | "chat" = storedScope === "chat" ? "chat" : "global";

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
        // The scope the picker above sets. `chat_id` was never passed, so every
        // limit the app could create was GLOBAL - the row below rendered
        // "- this chat" for something it had no way to produce, and turning
        // "use my global limits here" off hid every limit the user had ever
        // written.
        ...(scope === "chat" && chatId != null ? { chat_id: chatId } : {}),
      }]);
      // The whole set goes, not just the label: the qualifiers described a
      // limit that now exists, and leaving them would arm the next one with
      // settings nobody chose for it.
      clearPanelDraftsWithPrefix(
        chatScopedPanelDraftPrefix(PANEL_DRAFT_KEYS.newLimit, chatId),
      );
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
        className="text-label font-semibold"
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
          <span className="text-label leading-relaxed text-muted-foreground">
            {SAFEWORD_LABEL}
          </span>
          {/* NO BOX UNTIL THE SAVED WORD IS KNOWN. An empty box reads as
              "no safeword", which is a value, and a guess. Until the word
              is read the box is not drawn; a line says it is loading or
              could not be read, with Try again. */}
          {safewordQuery.isSuccess && (
            <Input
              id={SAFEWORD_FIELD}
              autoComplete="off"
              value={shown}
              maxLength={64}
              placeholder={SAFEWORD_PROMPT}
              title={SAFEWORD_PROMPT}
              disabled={busy}
              onChange={(e) => setSafeword(e.target.value)}
              onBlur={() => void saveSafeword()}
              onKeyDown={(e) => {
                if (e.key === "Enter") void saveSafeword();
              }}
              aria-label="Safeword"
              className="persona-field text-label"
            />
          )}
        </label>
        <Collapse
          open={safewordQuery.isPending && safewordQuery.errorUpdateCount === 0}
          className="min-w-0"
        >
          <p className="text-label leading-relaxed text-muted-foreground" role="status">
            {SETTING_LOADING}
          </p>
        </Collapse>
        <SettingUnread
          open={safewordQuery.isError}
          retrying={safewordQuery.isFetching}
          settled={settledAt(safewordQuery)}
          onRetry={() => void safewordQuery.refetch()}
          focusAfter={() => document.getElementById(SAFEWORD_FIELD)}
        />
        {/* ONE LINE ON SCREEN, THE REST ONE PRESS AWAY. The line
            is the promise a reader needs before typing a word; the details
            behind "How it works" keep every other promise the three old
            paragraphs made (limitsProse.ts lists them), only shorter.

            The second half has to be said rather than left out. The line
            above is true about the message being SENT and only about that.
            A copy of the word in something older travels on turns the
            reader asked for now, and stopping those would turn one word
            into a permanent stop with nothing on screen to explain it, so
            that copy is cut out instead. Regenerate is named because it is
            where the two halves meet and the answer is the surprising one:
            it re-sends the stored user message as the turn being sent, so a
            word in THAT line takes the stop branch. */}
        <p className="text-label leading-relaxed text-muted-foreground">
          {SAFEWORD_LINE}
          {/* Only beside a box there is: with the saved word unread, no
              box is drawn and there is nothing to leave empty. */}
          {safewordQuery.isSuccess && ` ${SAFEWORD_OFF}`}
        </p>
        <Disclosure label={DISCLOSURE_WORDS.how} testId="safeword-how">
          <p className="text-label leading-relaxed text-muted-foreground">
            {SAFEWORD_HOW.covers} {SAFEWORD_HOW.where}
          </p>
          <p className="text-label leading-relaxed text-muted-foreground">
            {SAFEWORD_HOW.older} {SAFEWORD_HOW.cut}{" "}
            {SAFEWORD_HOW.emptyLine} {SAFEWORD_HOW.regenerate}
          </p>
          <p className="text-label leading-relaxed text-muted-foreground">
            {SAFEWORD_HOW.ownCopy} {SAFEWORD_HOW.ownCopyWhere}
          </p>
        </Disclosure>
      </div>


      {/* Its own card, apart from the six controls below: this switch sets
          every other limit aside for one chat, which is a different kind of
          decision from writing one, and it read as a seventh control in the
          row before. */}
      <Collapse open={chatId != null} className="min-w-0">
        <div className="persona-card">
          <SwitchRow
            checked={useGlobal}
            disabled={busy}
            onCheckedChange={(v) => void handleToggleGlobal(v)}
          >
            Use my global limits here
          </SwitchRow>
        </div>
      </Collapse>

      {/* Two rows, not one. Six controls in a single flex row did not fit
          the panel's 300px floor: the four selects were squeezed to their
          minimum and the longest option, "go back and take it another way",
          was clipped inside its own box. The words go on the first row with
          the add button, and the four qualifiers take a two-column grid
          below, each under a visible label. A column of that grid is still
          narrower than the longest action at the floor, so each qualifier's
          words end in an ellipsis and keep their whole wording as a title
          (LimitQualifier). At the floor "What to do if it happens" also wraps
          to two lines where "Rating ceiling" beside it keeps one, and with
          the cells laid from the top of their row the two fields sat 22 px
          apart. The cells are laid against the end of their row instead:
          the field is the last thing in each, all four are one height, so
          the fields of a row line up whatever their labels do. */}
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <Input
            value={label}
            maxLength={BOUNDARY_MAX_CHARS}
            placeholder={NEW_LIMIT_PROMPT}
            title={NEW_LIMIT_PROMPT}
            // The placeholder was the only thing naming this box, and a
            // placeholder stops being read out the moment there is a value -
            // so the one control on this island announced itself by its own
            // contents while the user typed and as nothing once they paused.
            // Same fix, same wording pattern, as the note box next door.
            aria-label="New limit"
            disabled={busy}
            onChange={(e) => setLabel(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleAdd();
            }}
            className="persona-field text-label"
          />
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={busy || !label.trim()}
            onClick={() => void handleAdd()}
            aria-label="Add limit"
            className="persona-ghost-action h-7 w-7 shrink-0 p-0"
          >
            {create.isPending ? (
              <Loader2 size={12} className="size-3 animate-spin" />
            ) : (
              <Plus size={12} className="size-3" />
            )}
          </Button>
        </div>
        <div className="grid grid-cols-2 items-end gap-2">
          <LimitQualifier
            label={BOUNDARY_SELECT_LABELS.scope}
            value={scope}
            items={BOUNDARY_SCOPE_ITEMS}
            disabled={busy || chatId == null}
            onChange={(v) => setScope(v === "chat" ? "chat" : "global")}
          />
          {/* `.persona-field` is this surface's form-control class, focus
              ring included. The hand-written rgba below it was a third
              unmanaged variant of one control, and `settings-value` also
              carried an 11px size that beat the label size beside it - so
              the same select rendered at two sizes in two sibling panels. */}
          <LimitQualifier
            label={BOUNDARY_SELECT_LABELS.severity}
            value={severity}
            items={BOUNDARY_SEVERITY_ITEMS}
            disabled={busy}
            onChange={setSeverity}
          />
          <LimitQualifier
            label={BOUNDARY_SELECT_LABELS.onViolation}
            value={onViolation}
            items={BOUNDARY_ON_VIOLATION_ITEMS}
            disabled={busy}
            onChange={setOnViolation}
          />
          <LimitQualifier
            label={BOUNDARY_SELECT_LABELS.rating}
            value={rating}
            items={BOUNDARY_RATING_ITEMS}
            disabled={busy}
            onChange={setRating}
          />
        </div>
      </div>

      {/* WITH THE LIMITS, not under the safeword: said right above their
          list, it cannot be read as being about the safeword. */}
      <p className="text-label leading-relaxed text-muted-foreground" data-testid="limits-line">
        {LIMITS_LINE}
      </p>

      <SettingUnread
        open={failed}
        tone="muted"
        text={LIMITS_UNREAD}
        retrying={chatId == null ? globalBoundaries.isFetching : chatBoundaries.isFetching}
        settled={settledAt(chatId == null ? globalBoundaries : chatBoundaries)}
        onRetry={() => void (chatId == null
          ? globalBoundaries.refetch() : chatBoundaries.refetch())}
      />

      <Collapse open={!failed && rows.length === 0} className="min-w-0">
        <p className="text-label leading-relaxed text-muted-foreground">No limits set.</p>
      </Collapse>

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
        {/* The panel's own ink, as a note's words carry it: with none, the
            label inherits the dark theme's text from the body. */}
        <p
          className="break-words text-ui font-semibold"
          style={{ color: "var(--color-es-text-light)" }}
        >
          {row.label}
        </p>
        <p className="text-label leading-relaxed text-muted-foreground">
          {BOUNDARY_SEVERITY_ITEMS[
            row.severity as keyof typeof BOUNDARY_SEVERITY_ITEMS
          ] ?? row.severity}
          {row.scope === "global" ? " - everywhere" : " - this chat"}
        </p>
      </div>
      {!confirming && (
        <FadeIn>
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
        </FadeIn>
      )}
      {confirming && (
        <FadeIn className="flex items-start gap-2">
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
        </FadeIn>
      )}
    </div>
  );
}
