import { useEffect, useId, useRef, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Collapse } from "@/components/motion/Collapse";
import { usePatchCharacter, useDeleteCharacter } from "@/lib/query/characters";
import { useUiStore } from "@/lib/store/uiStore";
import { parseApiError } from "@/lib/errors";
import { CHARACTER_DELETE_CASCADE_WARNING } from "@/lib/characters";
import {
  Pencil,
  Loader2,
  AlertCircle,
  AlertTriangle,
  ChevronDown,
  Trash2,
} from "lucide-react";
import type { Character, CharacterPatch } from "@/lib/schemas/characters";
import type { ReactElement, ReactNode } from "react";
import { cn } from "@/lib/utils";
import { DIALOG_MAX_HEIGHT, DIALOG_WIDTHS } from "@/lib/theme/dialogScale";

interface CharacterEditDialogProps {
  character: Character;
  trigger: ReactElement;
}

function parseTags(input: string): string[] {
  return input
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
}

export function CharacterEditDialog({
  character,
  trigger,
}: CharacterEditDialogProps) {
  const [open, setOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const patch = usePatchCharacter();
  const deleteCharacter = useDeleteCharacter();
  const selectedCharacterId = useUiStore((s) => s.selectedCharacterId);
  const selectCharacter = useUiStore((s) => s.selectCharacter);
  const [error, setError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const confirmDeleteRef = useRef<HTMLButtonElement | null>(null);
  // The confirm step focuses its destructive button, so the cascade warning
  // has to travel WITH that focus - otherwise the only thing announced is the
  // button's own label and the chats about to go with the character are never
  // mentioned. Generated per instance: a card list mounts one of these editors
  // per character, and a hand-written id would collide across them.
  const cascadeWarningId = useId();

  const [name, setName] = useState(character.name);
  const [description, setDescription] = useState(character.description);
  const [personality, setPersonality] = useState(character.personality);
  const [scenario, setScenario] = useState(character.scenario);
  const [firstMes, setFirstMes] = useState(character.first_mes);
  const [mesExample, setMesExample] = useState(character.mes_example);
  const [systemPrompt, setSystemPrompt] = useState(character.system_prompt);
  const [postHistoryInstruction, setPostHistoryInstruction] = useState(
    character.post_history_instruction,
  );
  const [tagsInput, setTagsInput] = useState(character.tags.join(", "));

  // Re-seed the form from the (possibly refetched) character on every open.
  const seedForm = () => {
    setName(character.name);
    setDescription(character.description);
    setPersonality(character.personality);
    setScenario(character.scenario);
    setFirstMes(character.first_mes);
    setMesExample(character.mes_example);
    setSystemPrompt(character.system_prompt);
    setPostHistoryInstruction(character.post_history_instruction);
    setTagsInput(character.tags.join(", "));
    setError(null);
    setConfirmingDelete(false);
    // B7 (PK-08): these two fields go to the model on every turn, so a card
    // that carries either opens with them in view instead of hiding them
    // behind a closed section. An empty card keeps the section closed.
    setAdvancedOpen(
      character.system_prompt.trim() !== "" ||
        character.post_history_instruction.trim() !== "",
    );
  };

  // Destructive confirm a11y: focus the confirm button when the step appears.
  useEffect(() => {
    if (confirmingDelete) {
      confirmDeleteRef.current?.focus();
    }
  }, [confirmingDelete]);

  /** Only fields that differ from the current character are sent (partial PATCH). */
  const buildPatch = (): CharacterPatch => {
    const payload: CharacterPatch = {};
    if (name.trim() !== character.name) payload.name = name.trim();
    if (description.trim() !== character.description)
      payload.description = description.trim();
    if (personality.trim() !== character.personality)
      payload.personality = personality.trim();
    if (scenario.trim() !== character.scenario)
      payload.scenario = scenario.trim();
    if (firstMes.trim() !== character.first_mes)
      payload.first_mes = firstMes.trim();
    if (mesExample.trim() !== character.mes_example)
      payload.mes_example = mesExample.trim();
    if (systemPrompt.trim() !== character.system_prompt)
      payload.system_prompt = systemPrompt.trim();
    if (postHistoryInstruction.trim() !== character.post_history_instruction)
      payload.post_history_instruction = postHistoryInstruction.trim();
    const tags = parseTags(tagsInput);
    if (JSON.stringify(tags) !== JSON.stringify(character.tags))
      payload.tags = tags;
    return payload;
  };

  const handleSave = async () => {
    // No name guard, for the same reason the Save button no longer has one.
    // `buildPatch` already sends the emptied box as `name: ""`, which the
    // server reads as a rename to the next default name.
    setError(null);
    const payload = buildPatch();
    if (Object.keys(payload).length === 0) {
      setOpen(false); // Nothing changed - no request needed
      return;
    }
    try {
      await patch.mutateAsync({ id: character.id, payload });
      setOpen(false);
    } catch (err) {
      setError(parseApiError(err).message);
    }
  };

  const handleDelete = async () => {
    setError(null);
    try {
      await deleteCharacter.mutateAsync(character.id);
      // Cascade: the character's chats/messages are gone; clear the selection
      // if this character was active (also clears the selected chat).
      if (selectedCharacterId === character.id) {
        selectCharacter(null);
      }
      setConfirmingDelete(false);
      setOpen(false);
    } catch (err) {
      setError(parseApiError(err).message);
    }
  };

  const busy = patch.isPending || deleteCharacter.isPending;

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        setOpen(v);
        if (v) seedForm();
      }}
    >
      <DialogTrigger render={trigger} />
      <DialogContent
        className={cn("glass-dialog sidebar-dialog overflow-y-auto", DIALOG_MAX_HEIGHT, DIALOG_WIDTHS.medium)}
      >
        <DialogHeader>
          <DialogTitle
            className="flex items-center gap-2 text-title font-semibold"
            style={{ color: "var(--color-es-text-light)" }}
          >
            <Pencil size={15} style={{ color: "rgba(200, 216, 236, 0.86)" }} />
            Edit Character
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-5">
          {/* ── Section: Basic ── */}
          <section className="space-y-3">
            <SectionLabel>Basic</SectionLabel>
            <Field label="Name">
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Character name"
                disabled={busy}
                className="sidebar-dialog-field text-control"
                aria-label="Character name"
              />
            </Field>
            <Field label="Description">
              <Textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="A brief description…"
                disabled={busy}
                rows={2}
                className="sidebar-dialog-field resize-none text-control"
                aria-label="Character description"
              />
            </Field>
            <Field label="Tags">
              <Input
                value={tagsInput}
                onChange={(e) => setTagsInput(e.target.value)}
                placeholder="tag1, tag2, tag3"
                disabled={busy}
                className="sidebar-dialog-field text-control"
                aria-label="Character tags"
              />
            </Field>
          </section>

          {/* ── Section: Voice & Scenario ── */}
          <section className="space-y-3">
            <SectionLabel>Voice & Scenario</SectionLabel>
            <Field label="Personality">
              <Textarea
                value={personality}
                onChange={(e) => setPersonality(e.target.value)}
                placeholder="Character personality traits…"
                disabled={busy}
                rows={3}
                className="sidebar-dialog-field resize-none text-control"
                aria-label="Character personality"
              />
            </Field>
            <Field label="Scenario">
              <Textarea
                value={scenario}
                onChange={(e) => setScenario(e.target.value)}
                placeholder="Context / scenario…"
                disabled={busy}
                rows={2}
                className="sidebar-dialog-field resize-none text-control"
                aria-label="Character scenario"
              />
            </Field>
            <Field label="First Message">
              <Textarea
                value={firstMes}
                onChange={(e) => setFirstMes(e.target.value)}
                placeholder="Opening message…"
                disabled={busy}
                rows={2}
                className="sidebar-dialog-field resize-none text-control"
                aria-label="Character first message"
              />
            </Field>
            <Field label="Example Dialogue">
              <Textarea
                value={mesExample}
                onChange={(e) => setMesExample(e.target.value)}
                placeholder="Example dialogue format…"
                disabled={busy}
                rows={2}
                className="sidebar-dialog-field resize-none text-control"
                aria-label="Character example dialogue"
              />
            </Field>
          </section>

          {/* ── Section: Advanced (collapsible) ── */}
          <div>
            <button
              type="button"
              onClick={() => setAdvancedOpen((o) => !o)}
              aria-expanded={advancedOpen}
              className="flex w-full cursor-pointer items-center gap-1.5 text-label font-semibold select-none"
              style={{ color: "var(--color-es-text-muted)" }}
            >
              <ChevronDown
                size={12}
                className={`transition-transform ${advancedOpen ? "rotate-180" : ""}`}
              />
              Advanced (System Prompt, Post-History Instruction)
            </button>
            <Collapse open={advancedOpen}>
              <div className="mt-3 space-y-3">
                <Field label="System Prompt">
                  <Textarea
                    value={systemPrompt}
                    onChange={(e) => setSystemPrompt(e.target.value)}
                    placeholder="System-level instructions…"
                    disabled={busy}
                    rows={3}
                    className="sidebar-dialog-field resize-none text-control"
                    aria-label="Character system prompt"
                  />
                </Field>
                <Field label="Post-History Instruction">
                  <Textarea
                    value={postHistoryInstruction}
                    onChange={(e) => setPostHistoryInstruction(e.target.value)}
                    placeholder="Instruction after chat history…"
                    disabled={busy}
                    rows={2}
                    className="sidebar-dialog-field resize-none text-control"
                    aria-label="Character post-history instruction"
                  />
                </Field>
              </div>
            </Collapse>
          </div>

          {/* ── Danger zone: delete with two-step confirm ── */}
          {!confirmingDelete ? (
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={() => {
                setError(null);
                setConfirmingDelete(true);
              }}
              className="gap-1.5 text-label"
              style={{ color: "var(--color-es-danger)" }}
            >
              <Trash2 size={12} />
              Delete character
            </Button>
          ) : (
            <div
              className="space-y-2 rounded-lg px-3 py-2.5"
              role="dialog"
              aria-label="Confirm delete character"
              style={{
                backgroundColor: "rgba(195, 106, 114, 0.10)",
                border: "1px solid rgba(195, 106, 114, 0.18)",
              }}
            >
              <div
                className="flex items-start gap-2 text-label leading-relaxed"
                style={{ color: "var(--color-es-danger)" }}
              >
                <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                {/* Handoff mandate: render the cascade warning verbatim */}
                <span id={cascadeWarningId}>
                  {CHARACTER_DELETE_CASCADE_WARNING}
                </span>
              </div>
              <div className="flex justify-end gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setConfirmingDelete(false)}
                  disabled={busy}
                  className="sidebar-dialog-cancel text-label"
                >
                  Cancel
                </Button>
                <Button
                  ref={confirmDeleteRef}
                  size="sm"
                  disabled={busy}
                  onClick={handleDelete}
                  aria-describedby={cascadeWarningId}
                  className="gap-1.5 text-label"
                  style={{
                    backgroundColor: "var(--color-es-danger)",
                    color: "var(--color-es-text-dark)",
                  }}
                >
                  {deleteCharacter.isPending && (
                    <Loader2 size={12} className="animate-spin" />
                  )}
                  Delete permanently
                </Button>
              </div>
            </div>
          )}

          {error && (
            <div
              className="flex items-center gap-2 rounded-lg px-3 py-2 text-label"
              role="alert"
              style={{
                backgroundColor: "rgba(195, 106, 114, 0.10)",
                color: "var(--color-es-danger)",
                border: "1px solid rgba(195, 106, 114, 0.18)",
              }}
            >
              <AlertCircle size={12} />
              {error}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setOpen(false)}
              disabled={busy}
              className="sidebar-dialog-cancel text-label"
            >
              Cancel
            </Button>
            <Button
              size="sm"
              // No name guard: an emptied name box is a rename to the next
              // default name, which the server picks. Blocking Save here
              // would leave the box the one field a person cannot clear.
              disabled={busy}
              onClick={handleSave}
              className="sidebar-dialog-action gap-1.5 text-label"
            >
              {patch.isPending && <Loader2 size={12} className="animate-spin" />}
              Save
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <p
      className="text-micro font-semibold uppercase tracking-widest"
      style={{ color: "var(--color-es-accent-strong)" }}
    >
      {children}
    </p>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label
        className="text-label font-semibold"
        style={{ color: "var(--color-es-text-muted)" }}
      >
        {label}
      </label>
      {children}
    </div>
  );
}
