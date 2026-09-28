import { useMemo, useState } from "react";
import {
  Check,
  Loader2,
  Pencil,
  Plus,
  ShieldCheck,
  Trash2,
  UserCircle,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { SlideIn } from "@/components/motion/SlideIn";
import { parseApiError, useErrorStore } from "@/lib/errors";
import { findActivePersona } from "@/lib/personas";
import {
  useCreatePersona,
  useDeletePersona,
  usePatchPersona,
  usePersonas,
  useSelectPersona,
} from "@/lib/query/personas";
import type { Persona, PersonaPatch } from "@/lib/schemas/personas";
import {
  PANEL_DRAFT_KEYS,
  PERSONA_EDIT_DRAFT_PREFIX,
  panelDraftKeys,
  personaEditDraftKey,
  personaIdFromEditDraftKey,
  useDraftStore,
  usePanelDraft,
} from "@/lib/store/draftStore";

const PRIVACY_NOTE =
  "Only the selected persona is used in generation. Saved inactive personas are not sent.";

interface PersonaFormState {
  displayName: string;
  description: string;
}

/**
 * The two boxes of one persona form, held in the draft store.
 *
 * Shaped as the `useState` pair it replaced so `PersonaForm` below did not
 * have to change at all: it still receives one object and one setter that
 * accepts an updater. It used to carry a third, component-local field for a
 * validation message; the only message it ever held was the one refusing an
 * empty display name, and an empty display name is no longer a refusal.
 *
 * The fallbacks are what the boxes show before anything is typed, which is
 * how the edit form follows the stored persona until the moment the user
 * disagrees with it.
 */
function usePersonaFormDraft(
  nameKey: string,
  descriptionKey: string,
  nameFallback = "",
  descriptionFallback = "",
): [PersonaFormState, (next: PersonaFormState | ((current: PersonaFormState) => PersonaFormState)) => void] {
  const [displayName, setDisplayName] = usePanelDraft(nameKey, nameFallback);
  const [description, setDescription] = usePanelDraft(
    descriptionKey,
    descriptionFallback,
  );
  const form: PersonaFormState = { displayName, description };
  const setForm = (
    next: PersonaFormState | ((current: PersonaFormState) => PersonaFormState),
  ) => {
    // An updater is handed what the STORE holds right now, not what this
    // render read. Two writes inside one event - a paste that fires input
    // twice, a handler that adjusts one field and then another - would
    // otherwise compute the second one from a value that is already one
    // write out of date, and the first keystroke would disappear.
    const stored = useDraftStore.getState().panel;
    const current: PersonaFormState = {
      displayName: stored[nameKey]?.text ?? nameFallback,
      description: stored[descriptionKey]?.text ?? descriptionFallback,
    };
    const value = typeof next === "function" ? next(current) : next;
    // Only what actually MOVED is written. Writing both fields on every call
    // meant that typing one character in the name box copied the stored
    // description - vault content the user never typed - into a store that
    // sits outside the vault and survives a lock. It also froze the untouched
    // box to a snapshot, so a row edited elsewhere stopped showing through.
    // The same restraint keeps "a buffer exists" meaning "the user typed
    // here", which is the fact two open/closed flags are derived from.
    if (value.displayName !== current.displayName) {
      setDisplayName(value.displayName);
    }
    if (value.description !== current.description) {
      setDescription(value.description);
    }
  };
  return [form, setForm];
}

export function PersonaPanel() {
  const { data: personas, isLoading, error } = usePersonas();
  const createPersona = useCreatePersona();
  const patchPersona = usePatchPersona();
  const deletePersona = useDeletePersona();
  const selectPersona = useSelectPersona();
  const pushError = useErrorStore((s) => s.pushError);

  const clearPanelDraft = useDraftStore((s) => s.clearPanelDraft);
  const clearPanelDraftsWithPrefix = useDraftStore(
    (s) => s.clearPanelDraftsWithPrefix,
  );

  // WHICH FORM IS OPEN IS DERIVED FROM THE BUFFERS, not stored beside them.
  //
  // This panel is unmounted every time the reader looks at a neighbouring
  // tab, so keeping only the TEXT would have fixed half the bug: the words
  // would survive in the store while the form that shows them came back
  // closed, and the "New Persona" button below wipes the form on the way
  // open. Both flags are therefore read out of the draft store at mount.
  //
  // Lazy initial values rather than an effect: an effect runs after paint,
  // so the reader would get one frame of a closed form snapping open.
  const [createOpen, setCreateOpen] = useState(
    () =>
      panelDraftKeys().some(
        (key) =>
          key === PANEL_DRAFT_KEYS.personaCreateName ||
          key === PANEL_DRAFT_KEYS.personaCreateDescription,
      ),
  );
  const [editingId, setEditingId] = useState<number | null>(() => {
    // The NEWEST matching key, not the first. Keys come back in insertion
    // order, and only one edit form is ever on screen, so more than one live
    // buffer means an older one was stranded - by a row deleted from another
    // window, say. Taking the first would let that stranded key outrank the
    // form the user was actually in and reopen a row they had walked away
    // from, with the words they had just typed nowhere on screen.
    let newest: number | null = null;
    for (const key of panelDraftKeys()) {
      const id = personaIdFromEditDraftKey(key);
      if (id != null) newest = id;
    }
    return newest;
  });
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [pendingSelectId, setPendingSelectId] = useState<number | null>(null);

  const [createForm, setCreateForm] = usePersonaFormDraft(
    PANEL_DRAFT_KEYS.personaCreateName,
    PANEL_DRAFT_KEYS.personaCreateDescription,
  );

  // The row being edited, so its stored values can be the FALLBACK the two
  // boxes show. Nothing is copied into the store when the pencil is pressed:
  // a buffer appears only once the user disagrees with what is stored, which
  // is also what keeps a reopened form from showing a stale copy of a row
  // that changed underneath it.
  const editingPersona =
    editingId == null
      ? null
      : (personas?.find((p) => p.id === editingId) ?? null);
  const [editForm, setEditForm] = usePersonaFormDraft(
    // A row id is never null here in practice, but hooks cannot be skipped,
    // so the closed state needs a key of its own. Nothing writes to it: the
    // only writer is the form, and the form is not on screen.
    editingId == null ? `${PERSONA_EDIT_DRAFT_PREFIX}closed.name` : personaEditDraftKey(editingId, "name"),
    editingId == null
      ? `${PERSONA_EDIT_DRAFT_PREFIX}closed.description`
      : personaEditDraftKey(editingId, "description"),
    editingPersona?.display_name ?? "",
    editingPersona?.description ?? "",
  );

  // A row can disappear while its id is still held here - deleted from
  // another window against the same vault, since every path in THIS panel
  // that closes the form frees it. An id pointing at no row renders no form
  // at all, so the panel would look like it had simply stopped offering to
  // edit anything.
  //
  // Adjusted during render rather than in an effect, which is the pattern
  // this codebase already uses for exactly this shape: React re-runs the
  // render with the new value before committing, so nobody sees the frame in
  // between. The stranded buffer itself is left where it is - freeing it
  // here would mean writing to an external store mid-render, and it can no
  // longer win anything now that the initializer above takes the newest key.
  // It is a few bytes against a 64 MiB ceiling and a vault wipe frees it.
  if (
    editingId != null &&
    personas != null &&
    !personas.some((persona) => persona.id === editingId)
  ) {
    setEditingId(null);
  }

  const activePersona = useMemo(
    () => findActivePersona(personas),
    [personas],
  );

  const busy =
    createPersona.isPending ||
    patchPersona.isPending ||
    deletePersona.isPending ||
    selectPersona.isPending;

  const safeQueryError = error ? parseApiError(error).message : null;

  const discardCreateDraft = () => {
    clearPanelDraft(PANEL_DRAFT_KEYS.personaCreateName);
    clearPanelDraft(PANEL_DRAFT_KEYS.personaCreateDescription);
  };

  const resetCreate = () => {
    discardCreateDraft();
    setCreateOpen(false);
  };

  const discardEditDraft = (personaId: number) => {
    clearPanelDraftsWithPrefix(`${PERSONA_EDIT_DRAFT_PREFIX}${personaId}.`);
  };

  const openEdit = (persona: Persona) => {
    setConfirmDeleteId(null);
    // Moving straight from one row's pencil to another's, with no Cancel in
    // between, is an ordinary two clicks. Only one form is ever on screen, so
    // leaving the first row's buffer behind would leave TWO rows armed - and
    // the next mount picks whichever key it meets first, which reopened the
    // row the user had walked away from and hid the words they had just
    // typed. Abandoning the first row's text is what the old form did too:
    // its single shared buffer was overwritten at exactly this point.
    if (editingId != null && editingId !== persona.id) {
      discardEditDraft(editingId);
    }
    setEditingId(persona.id);
  };

  const closeEdit = () => {
    // Closing the box is the user saying they are finished with it, so the
    // buffer goes with it. Leaving it would reopen this row's form on the
    // next mount, which is the behaviour a Cancel is meant to prevent.
    if (editingId != null) discardEditDraft(editingId);
    setEditingId(null);
  };

  const handleCreate = async () => {
    const displayName = createForm.displayName.trim();
    const description = createForm.description.trim();
    // No "Display name is required." any more. It is not required: an empty
    // one is sent as an empty string and the server names the persona.

    try {
      await createPersona.mutateAsync({
        display_name: displayName,
        description,
      });
      resetCreate();
    } catch (err) {
      pushError(err);
    }
  };

  const handlePatch = async (persona: Persona) => {
    const displayName = editForm.displayName.trim();
    const description = editForm.description.trim();
    // Same as create: an emptied box is a rename to the next default name,
    // not a refusal. It still has to DIFFER from the stored name to be sent,
    // which is what the comparison below decides.

    const payload: PersonaPatch = {};
    if (displayName !== persona.display_name) payload.display_name = displayName;
    if (description !== persona.description) payload.description = description;

    if (Object.keys(payload).length === 0) {
      closeEdit();
      return;
    }

    try {
      await patchPersona.mutateAsync({ id: persona.id, payload });
      closeEdit();
    } catch (err) {
      pushError(err);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await deletePersona.mutateAsync(id);
      setConfirmDeleteId(null);
      if (editingId === id) closeEdit();
    } catch (err) {
      pushError(err);
    }
  };

  const handleSelect = async (id: number) => {
    setPendingSelectId(id);
    try {
      await selectPersona.mutateAsync(id);
    } catch (err) {
      pushError(err);
    } finally {
      setPendingSelectId(null);
    }
  };

  return (
    <SlideIn className="persona-panel flex h-full flex-col gap-4 overflow-y-auto p-4">
      <section className="space-y-3">
        <div className="flex items-start gap-3">
          <div className="persona-icon" aria-hidden="true">
            <UserCircle size={18} strokeWidth={1.7} />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="text-ui font-semibold">Persona</h3>
            <p className="mt-1 text-label leading-relaxed text-muted-foreground">
              Persona represents your user identity for conversations.
            </p>
          </div>
        </div>

        <div className="persona-privacy-note">
          <ShieldCheck size={13} className="mt-0.5 shrink-0" />
          <span>{PRIVACY_NOTE}</span>
        </div>
      </section>

      <section className="persona-card persona-card-active space-y-2">
        <div className="flex items-center justify-between gap-3">
          <p className="text-micro font-semibold uppercase tracking-widest text-muted-foreground">
            Active Persona
          </p>
          {activePersona && <ActiveBadge />}
        </div>

        {activePersona ? (
          <div>
            <p className="truncate text-ui font-semibold">
              {activePersona.display_name}
            </p>
            <p className="mt-1 line-clamp-2 text-label leading-relaxed text-muted-foreground">
              {activePersona.description || "No description yet."}
            </p>
          </div>
        ) : (
          <p className="text-label leading-relaxed text-muted-foreground">
            No active persona. Select one below to include it in future
            generations.
          </p>
        )}
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <p className="text-micro font-semibold uppercase tracking-widest text-muted-foreground">
            Personas
          </p>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="persona-ghost-action h-7 gap-1.5 px-2 text-label"
            // Wipes the boxes in BOTH directions, exactly as before. Opening
            // a form that still held the last attempt's words was never the
            // intent; what changed is only that a form left open across a
            // tab switch comes back open, so this button is not on the path
            // that restores it.
            onClick={() => {
              setCreateOpen((open) => !open);
              discardCreateDraft();
            }}
            disabled={busy}
            aria-expanded={createOpen}
          >
            {createOpen ? <X size={12} /> : <Plus size={12} />}
            {createOpen ? "Close" : "New Persona"}
          </Button>
        </div>

        {createOpen && (
          <PersonaForm
            mode="create"
            form={createForm}
            setForm={setCreateForm}
            pending={createPersona.isPending}
            onCancel={resetCreate}
            onSubmit={handleCreate}
          />
        )}

        {isLoading && <PersonaLoading />}

        {safeQueryError && (
          <div className="persona-error" role="alert">
            {safeQueryError}
          </div>
        )}

        {!isLoading && !safeQueryError && personas?.length === 0 && (
          <div className="persona-empty-state">
            <p className="text-ui font-semibold">No personas yet</p>
            <p className="mt-1 text-label leading-relaxed text-muted-foreground">
              Create one to define your user identity for generation.
            </p>
          </div>
        )}

        {!isLoading && !safeQueryError && personas && personas.length > 0 && (
          <div className="space-y-2">
            {personas.map((persona) => (
              <PersonaListItem
                key={persona.id}
                persona={persona}
                editing={editingId === persona.id}
                editForm={editForm}
                setEditForm={setEditForm}
                confirmingDelete={confirmDeleteId === persona.id}
                busy={busy}
                pendingSelect={pendingSelectId === persona.id}
                onEdit={() => openEdit(persona)}
                onCancelEdit={closeEdit}
                onSaveEdit={() => handlePatch(persona)}
                onSelect={() => handleSelect(persona.id)}
                // closeEdit, not a bare setEditingId(null): this is the one
                // place the form is shut without the user pressing Cancel,
                // and shutting it without freeing the buffer stranded the
                // row's words with no pencil left to reach them.
                onAskDelete={() => {
                  closeEdit();
                  setConfirmDeleteId(persona.id);
                }}
                onCancelDelete={() => setConfirmDeleteId(null)}
                onDelete={() => handleDelete(persona.id)}
              />
            ))}
          </div>
        )}
      </section>
    </SlideIn>
  );
}

function PersonaListItem({
  persona,
  editing,
  editForm,
  setEditForm,
  confirmingDelete,
  busy,
  pendingSelect,
  onEdit,
  onCancelEdit,
  onSaveEdit,
  onSelect,
  onAskDelete,
  onCancelDelete,
  onDelete,
}: {
  persona: Persona;
  editing: boolean;
  editForm: PersonaFormState;
  setEditForm: (form: PersonaFormState | ((form: PersonaFormState) => PersonaFormState)) => void;
  confirmingDelete: boolean;
  busy: boolean;
  pendingSelect: boolean;
  onEdit: () => void;
  onCancelEdit: () => void;
  onSaveEdit: () => void;
  onSelect: () => void;
  onAskDelete: () => void;
  onCancelDelete: () => void;
  onDelete: () => void;
}) {
  return (
    <article
      className={`persona-card persona-list-item ${
        persona.is_active ? "is-active" : ""
      }`}
      data-testid={`persona-card-${persona.id}`}
    >
      {editing ? (
        <PersonaForm
          mode="edit"
          form={editForm}
          setForm={setEditForm}
          pending={busy}
          onCancel={onCancelEdit}
          onSubmit={onSaveEdit}
        />
      ) : (
        <div className="space-y-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <p className="truncate text-ui font-semibold">
                  {persona.display_name}
                </p>
                {persona.is_active && <ActiveBadge />}
              </div>
              <p className="mt-1 line-clamp-2 text-label leading-relaxed text-muted-foreground">
                {persona.description || "No description yet."}
              </p>
            </div>
          </div>

          {confirmingDelete ? (
            <div className="persona-confirm">
              <p className="text-label font-semibold">Delete this persona?</p>
              <div className="mt-2 flex justify-end gap-1.5">
                <Button
                  type="button"
                  size="xs"
                  variant="ghost"
                  className="persona-ghost-action"
                  onClick={onCancelDelete}
                  disabled={busy}
                >
                  Cancel
                </Button>
                <Button
                  type="button"
                  size="xs"
                  variant="ghost"
                  onClick={onDelete}
                  disabled={busy}
                  // `persona-danger-action`, not variant="destructive" - the
                  // house pattern for a danger button on this panel, and the
                  // same class the TRIGGER two blocks down already uses.
                  // shadcn's destructive variant paints from `--destructive`,
                  // which `:root` sets to #C36A72 for the DARK shell and
                  // which `.glass-right` never overrides. On this one light
                  // surface that measured 3.29:1 at rest and 2.93:1 on hover
                  // (WCAG, against the panel's own gradient with the confirm
                  // box's white over it) - under the 4.5:1 floor for text and
                  // under even the 3:1 floor for graphics. Darkening the
                  // token could not fix it either: `bg-destructive/10` tints
                  // the background with the SAME colour as the text, so the
                  // two move together and the ratio stalls (#AF4650 measured
                  // 4.07:1 on hover). persona-danger-action separates them -
                  // a white-tinted background under #96424E text - and comes
                  // out at 6.51:1. NotebookPanel and BoundaryPanel already
                  // made this exact swap for this exact reason.
                  className="persona-danger-action gap-1"
                >
                  {busy && <Loader2 size={11} className="animate-spin" />}
                  Delete
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap justify-end gap-1.5">
              <Button
                type="button"
                size="xs"
                className="persona-select-action gap-1"
                onClick={onSelect}
                disabled={busy || persona.is_active}
              >
                {pendingSelect ? (
                  <Loader2 size={11} className="animate-spin" />
                ) : persona.is_active ? (
                  <Check size={11} />
                ) : null}
                {persona.is_active ? "Selected" : "Select"}
              </Button>
              <Button
                type="button"
                size="xs"
                variant="ghost"
                className="persona-ghost-action gap-1"
                onClick={onEdit}
                disabled={busy}
              >
                <Pencil size={11} />
                Edit
              </Button>
              <Button
                type="button"
                size="xs"
                variant="ghost"
                className="persona-danger-action gap-1"
                onClick={onAskDelete}
                disabled={busy}
              >
                <Trash2 size={11} />
                Delete
              </Button>
            </div>
          )}
        </div>
      )}
    </article>
  );
}

function PersonaForm({
  mode,
  form,
  setForm,
  pending,
  onCancel,
  onSubmit,
}: {
  mode: "create" | "edit";
  form: PersonaFormState;
  setForm: (form: PersonaFormState | ((form: PersonaFormState) => PersonaFormState)) => void;
  pending: boolean;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  const title = mode === "create" ? "Create Persona" : "Edit Persona";
  return (
    <div className="persona-card persona-form-card space-y-3">
      <p className="text-label font-semibold">{title}</p>
      <label className="block space-y-1.5 text-label font-semibold text-muted-foreground">
        <span>Display name</span>
        <Input
          value={form.displayName}
          onChange={(e) =>
            setForm((current) => ({
              ...current,
              displayName: e.target.value,
            }))
          }
          placeholder="How should your character know you?"
          className="persona-field"
          disabled={pending}
        />
      </label>
      <label className="block space-y-1.5 text-label font-semibold text-muted-foreground">
        <span>Description</span>
        <Textarea
          value={form.description}
          onChange={(e) =>
            setForm((current) => ({
              ...current,
              description: e.target.value,
            }))
          }
          placeholder="A short note about your preferences or identity."
          rows={3}
          className="persona-field min-h-20 resize-none"
          disabled={pending}
        />
      </label>
      <div className="flex justify-end gap-1.5">
        <Button
          type="button"
          size="xs"
          variant="ghost"
          className="persona-ghost-action"
          onClick={onCancel}
          disabled={pending}
        >
          Cancel
        </Button>
        <Button
          type="button"
          size="xs"
          className="persona-select-action gap-1"
          onClick={onSubmit}
          disabled={pending}
        >
          {pending && <Loader2 size={11} className="animate-spin" />}
          {mode === "create" ? "Create" : "Save"}
        </Button>
      </div>
    </div>
  );
}

function PersonaLoading() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 3 }).map((_, index) => (
        <Skeleton
          key={index}
          className="h-24 rounded-xl"
          style={{ backgroundColor: "rgba(28, 38, 50,0.08)" }}
        />
      ))}
    </div>
  );
}

function ActiveBadge() {
  return (
    <Badge variant="outline" className="persona-active-badge">
      Active
    </Badge>
  );
}
