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

const PRIVACY_NOTE =
  "Only the selected persona is used in generation. Saved inactive personas are not sent.";

interface PersonaFormState {
  displayName: string;
  description: string;
  error: string | null;
}

const emptyForm: PersonaFormState = {
  displayName: "",
  description: "",
  error: null,
};

export function PersonaPanel() {
  const { data: personas, isLoading, error } = usePersonas();
  const createPersona = useCreatePersona();
  const patchPersona = usePatchPersona();
  const deletePersona = useDeletePersona();
  const selectPersona = useSelectPersona();
  const pushError = useErrorStore((s) => s.pushError);

  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState<PersonaFormState>(emptyForm);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<PersonaFormState>(emptyForm);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [pendingSelectId, setPendingSelectId] = useState<number | null>(null);

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

  const resetCreate = () => {
    setCreateForm(emptyForm);
    setCreateOpen(false);
  };

  const openEdit = (persona: Persona) => {
    setConfirmDeleteId(null);
    setEditingId(persona.id);
    setEditForm({
      displayName: persona.display_name,
      description: persona.description,
      error: null,
    });
  };

  const closeEdit = () => {
    setEditingId(null);
    setEditForm(emptyForm);
  };

  const handleCreate = async () => {
    const displayName = createForm.displayName.trim();
    const description = createForm.description.trim();
    if (!displayName) {
      setCreateForm((form) => ({
        ...form,
        error: "Display name is required.",
      }));
      return;
    }

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
    if (!displayName) {
      setEditForm((form) => ({
        ...form,
        error: "Display name is required.",
      }));
      return;
    }

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
            <h3 className="text-sm font-semibold">Persona</h3>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
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
          <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">
            Active Persona
          </p>
          {activePersona && <ActiveBadge />}
        </div>

        {activePersona ? (
          <div>
            <p className="truncate text-sm font-semibold">
              {activePersona.display_name}
            </p>
            <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
              {activePersona.description || "No description yet."}
            </p>
          </div>
        ) : (
          <p className="text-xs leading-relaxed text-muted-foreground">
            No active persona. Select one below to include it in future
            generations.
          </p>
        )}
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">
            Personas
          </p>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="persona-ghost-action h-7 gap-1.5 px-2 text-xs"
            onClick={() => {
              setCreateOpen((open) => !open);
              setCreateForm(emptyForm);
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
            <p className="text-sm font-medium">No personas yet</p>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
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
                onAskDelete={() => {
                  setEditingId(null);
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
                <p className="truncate text-sm font-semibold">
                  {persona.display_name}
                </p>
                {persona.is_active && <ActiveBadge />}
              </div>
              <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
                {persona.description || "No description yet."}
              </p>
            </div>
          </div>

          {confirmingDelete ? (
            <div className="persona-confirm">
              <p className="text-xs font-medium">Delete this persona?</p>
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
                  variant="destructive"
                  onClick={onDelete}
                  disabled={busy}
                  className="gap-1"
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
      <p className="text-xs font-semibold">{title}</p>
      <label className="block space-y-1.5 text-xs font-medium text-muted-foreground">
        <span>Display name</span>
        <Input
          value={form.displayName}
          onChange={(e) =>
            setForm((current) => ({
              ...current,
              displayName: e.target.value,
              error: null,
            }))
          }
          placeholder="How should Elysium know you?"
          className="persona-field"
          disabled={pending}
        />
      </label>
      <label className="block space-y-1.5 text-xs font-medium text-muted-foreground">
        <span>Description</span>
        <Textarea
          value={form.description}
          onChange={(e) =>
            setForm((current) => ({
              ...current,
              description: e.target.value,
              error: null,
            }))
          }
          placeholder="A short note about your preferences or identity."
          rows={3}
          className="persona-field min-h-20 resize-none"
          disabled={pending}
        />
      </label>
      {form.error && (
        <p className="persona-local-error" role="alert">
          {form.error}
        </p>
      )}
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
          style={{ backgroundColor: "rgba(47,49,45,0.08)" }}
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
