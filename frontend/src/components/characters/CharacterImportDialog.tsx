import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { useImportCharacter } from "@/lib/query/characters";
import { isApiError } from "@/lib/api/client";
import { FileJson, Loader2, AlertCircle } from "lucide-react";
import type { ReactElement } from "react";

interface CharacterImportDialogProps {
  trigger: ReactElement;
}

export function CharacterImportDialog({ trigger }: CharacterImportDialogProps) {
  const [open, setOpen] = useState(false);
  const importChar = useImportCharacter();
  const [jsonText, setJsonText] = useState("");
  const [error, setError] = useState<string | null>(null);

  const resetForm = () => {
    setJsonText("");
    setError(null);
  };

  const handleImport = async () => {
    if (!jsonText.trim()) return;
    setError(null);
    try {
      await importChar.mutateAsync(jsonText);
      resetForm();
      setOpen(false);
    } catch (err) {
      const msg = isApiError(err) ? err.detail : "Failed to import character";
      setError(msg);
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
      <DialogContent className="glass-dialog sidebar-dialog sm:max-w-lg">
        <DialogHeader>
          <DialogTitle
            className="flex items-center gap-2 text-base font-semibold"
            style={{ color: "var(--color-es-text-light)" }}
          >
            <FileJson size={15} style={{ color: "rgba(237, 227, 211, 0.86)" }} />
            Import Character (JSON)
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          <p
            className="text-xs leading-relaxed"
            style={{ color: "var(--color-es-text-muted)" }}
          >
            Paste a Character Card V2 JSON or raw character JSON below.
          </p>

          <Textarea
            value={jsonText}
            onChange={(e) => setJsonText(e.target.value)}
            placeholder='{"name": "Character", "description": "...", ...}'
            disabled={importChar.isPending}
            rows={10}
            className="sidebar-dialog-field resize-none font-mono text-xs leading-relaxed"
            aria-label="Character JSON input"
          />

          {error && (
            <div
              className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs"
              role="alert"
              style={{
                backgroundColor: "rgba(201, 110, 91, 0.10)",
                color: "var(--color-es-danger)",
                border: "1px solid rgba(201, 110, 91, 0.18)",
              }}
            >
              <AlertCircle size={12} />
              {error}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setOpen(false)}
              disabled={importChar.isPending}
              className="sidebar-dialog-cancel"
            >
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={importChar.isPending || !jsonText.trim()}
              onClick={handleImport}
              className="sidebar-dialog-action gap-1.5 text-xs"
            >
              {importChar.isPending && (
                <Loader2 size={12} className="animate-spin" />
              )}
              Import
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
