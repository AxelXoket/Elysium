import { useEffect, useRef, useState } from "react";
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
    if (!name.trim()) return;
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
        className="glass-dialog sidebar-dialog max-h-[85vh] overflow-y-auto sm:max-w-lg"
      >
        <DialogHeader>
          <DialogTitle
            className="flex items-center gap-2 text-base font-semibold"
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
            <Field label="Name *">
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Character name"
                disabled={busy}
                className="sidebar-dialog-field text-sm"
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
                className="sidebar-dialog-field resize-none text-sm"
                aria-label="Character description"
              />
            </Field>
            <Field label="Tags">
              <Input
                value={tagsInput}
                onChange={(e) => setTagsInput(e.target.value)}
                placeholder="tag1, tag2, tag3"
                disabled={busy}
                className="sidebar-dialog-field text-sm"
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
                className="sidebar-dialog-field resize-none text-sm"
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
                className="sidebar-dialog-field resize-none text-sm"
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
                className="sidebar-dialog-field resize-none text-sm"
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
                className="sidebar-dialog-field resize-none text-sm"
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
              className="flex w-full cursor-pointer items-center gap-1.5 text-xs font-medium select-none"
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
                    className="sidebar-dialog-field resize-none text-sm"
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
                    className="sidebar-dialog-field resize-none text-sm"
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
              className="gap-1.5 text-xs"
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
                className="flex items-start gap-2 text-xs leading-relaxed"
                style={{ color: "var(--color-es-danger)" }}
              >
                <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                {/* Handoff mandate: render the cascade warning verbatim */}
                <span>{CHARACTER_DELETE_CASCADE_WARNING}</span>
              </div>
              <div className="flex justify-end gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setConfirmingDelete(false)}
                  disabled={busy}
                  className="sidebar-dialog-cancel text-xs"
                >
                  Cancel
                </Button>
                <Button
                  ref={confirmDeleteRef}
                  size="sm"
                  disabled={busy}
                  onClick={handleDelete}
                  className="gap-1.5 text-xs"
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
              className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs"
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
              className="sidebar-dialog-cancel text-xs"
            >
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={busy || !name.trim()}
              onClick={handleSave}
              className="sidebar-dialog-action gap-1.5 text-xs"
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
      className="text-[11px] font-semibold uppercase tracking-widest"
      style={{ color: "var(--color-es-accent-amber)", opacity: 0.85 }}
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
        className="text-xs font-medium"
        style={{ color: "var(--color-es-text-muted)" }}
      >
        {label}
      </label>
      {children}
    </div>
  );
}
