import { useState } from "react";
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
import { useCreateCharacter } from "@/lib/query/characters";
import { useUiStore } from "@/lib/store/uiStore";
import { parseApiError } from "@/lib/errors";
import { Plus, Loader2, AlertCircle, ChevronDown } from "lucide-react";
import type { ReactElement, ReactNode } from "react";

interface CharacterCreateDialogProps {
  trigger: ReactElement;
}

export function CharacterCreateDialog({ trigger }: CharacterCreateDialogProps) {
  const [open, setOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const create = useCreateCharacter();
  const selectCharacter = useUiStore((s) => s.selectCharacter);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [personality, setPersonality] = useState("");
  const [scenario, setScenario] = useState("");
  const [firstMes, setFirstMes] = useState("");
  const [mesExample, setMesExample] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [postHistoryInstruction, setPostHistoryInstruction] = useState("");
  const [tagsInput, setTagsInput] = useState("");

  const resetForm = () => {
    setName("");
    setDescription("");
    setPersonality("");
    setScenario("");
    setFirstMes("");
    setMesExample("");
    setSystemPrompt("");
    setPostHistoryInstruction("");
    setTagsInput("");
    setAdvancedOpen(false);
    setError(null);
  };

  const handleSubmit = async () => {
    if (!name.trim()) return;
    setError(null);
    const tags = tagsInput
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    try {
      const created = await create.mutateAsync({
        name: name.trim(),
        description: description.trim(),
        personality: personality.trim(),
        scenario: scenario.trim(),
        first_mes: firstMes.trim(),
        mes_example: mesExample.trim(),
        system_prompt: systemPrompt.trim(),
        post_history_instruction: postHistoryInstruction.trim(),
        tags,
      });
      selectCharacter(created.id);
      resetForm();
      setOpen(false);
    } catch (err) {
      setError(parseApiError(err).message);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        setOpen(v);
        if (!v) resetForm();
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
            <Plus size={15} style={{ color: "rgba(200, 216, 236, 0.86)" }} />
            Create Character
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
                disabled={create.isPending}
                className="sidebar-dialog-field text-sm"
              />
            </Field>
            <Field label="Description">
              <Textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="A brief description…"
                disabled={create.isPending}
                rows={2}
                className="sidebar-dialog-field resize-none text-sm"
              />
            </Field>
            <Field label="Tags">
              <Input
                value={tagsInput}
                onChange={(e) => setTagsInput(e.target.value)}
                placeholder="tag1, tag2, tag3"
                disabled={create.isPending}
                className="sidebar-dialog-field text-sm"
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
                disabled={create.isPending}
                rows={3}
                className="sidebar-dialog-field resize-none text-sm"
              />
            </Field>
            <Field label="Scenario">
              <Textarea
                value={scenario}
                onChange={(e) => setScenario(e.target.value)}
                placeholder="Context / scenario…"
                disabled={create.isPending}
                rows={2}
                className="sidebar-dialog-field resize-none text-sm"
              />
            </Field>
            <Field label="First Message">
              <Textarea
                value={firstMes}
                onChange={(e) => setFirstMes(e.target.value)}
                placeholder="Opening message…"
                disabled={create.isPending}
                rows={2}
                className="sidebar-dialog-field resize-none text-sm"
              />
            </Field>
            <Field label="Example Dialogue">
              <Textarea
                value={mesExample}
                onChange={(e) => setMesExample(e.target.value)}
                placeholder="Example dialogue format…"
                disabled={create.isPending}
                rows={2}
                className="sidebar-dialog-field resize-none text-sm"
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
                    disabled={create.isPending}
                    rows={3}
                    className="sidebar-dialog-field resize-none text-sm"
                  />
                </Field>
                <Field label="Post-History Instruction">
                  <Textarea
                    value={postHistoryInstruction}
                    onChange={(e) => setPostHistoryInstruction(e.target.value)}
                    placeholder="Instruction after chat history…"
                    disabled={create.isPending}
                    rows={2}
                    className="sidebar-dialog-field resize-none text-sm"
                  />
                </Field>
              </div>
            </Collapse>
          </div>

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
              disabled={create.isPending}
              className="sidebar-dialog-cancel text-xs"
            >
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={create.isPending || !name.trim()}
              onClick={handleSubmit}
              className="sidebar-dialog-action gap-1.5 text-xs"
            >
              {create.isPending && (
                <Loader2 size={12} className="animate-spin" />
              )}
              Create
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
