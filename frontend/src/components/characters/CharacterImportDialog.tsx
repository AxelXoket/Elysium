import { useRef, useState } from "react";
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
import { parseApiError } from "@/lib/errors";
import { FileJson, Loader2, AlertCircle } from "lucide-react";
import type { ReactElement } from "react";
import { cn } from "@/lib/utils";
import { DIALOG_MAX_HEIGHT, DIALOG_WIDTHS } from "@/lib/theme/dialogScale";

interface CharacterImportDialogProps {
  trigger: ReactElement;
}

export function CharacterImportDialog({ trigger }: CharacterImportDialogProps) {
  const [open, setOpen] = useState(false);
  const importChar = useImportCharacter();
  const [jsonText, setJsonText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const resetForm = () => {
    setJsonText("");
    setError(null);
  };

  // FF14: a picked .json file fills the SAME jsonText state, so validation +
  // submit stay on the existing paste path.
  const handleFilePicked = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-picking the same file
    if (!file) return;
    setError(null);
    try {
      setJsonText(await file.text());
    } catch {
      setError("Could not read that file.");
    }
  };

  const handleImport = async () => {
    if (!jsonText.trim()) return;
    setError(null);
    try {
      await importChar.mutateAsync(jsonText);
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
      <DialogContent className={cn("glass-dialog sidebar-dialog overflow-y-auto", DIALOG_MAX_HEIGHT, DIALOG_WIDTHS.medium)}>
        <DialogHeader>
          <DialogTitle
            className="flex items-center gap-2 text-title font-semibold"
            style={{ color: "var(--color-es-text-light)" }}
          >
            <FileJson size={15} style={{ color: "rgba(200, 216, 236, 0.86)" }} />
            Import Character (JSON)
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          <div className="flex items-center justify-between gap-2">
            <p
              className="text-label leading-relaxed"
              style={{ color: "var(--color-es-text-muted)" }}
            >
              Paste a Character Card V2 JSON, or pick a .json file.
            </p>
            <Button
              variant="ghost"
              size="sm"
              className="sidebar-dialog-cancel gap-1.5 text-label"
              disabled={importChar.isPending}
              onClick={() => fileInputRef.current?.click()}
            >
              Choose file
            </Button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".json,application/json"
              className="hidden"
              aria-label="Character JSON file"
              onChange={handleFilePicked}
            />
          </div>

          <Textarea
            value={jsonText}
            onChange={(e) => setJsonText(e.target.value)}
            placeholder='{"name": "Character", "description": "...", ...}'
            disabled={importChar.isPending}
            rows={10}
            // The label step, and the control step from 768 px on: what this field
            // drew before the base lost its own breakpoint size.
            className="sidebar-dialog-field resize-none font-mono text-label leading-relaxed md:text-control"
            aria-label="Character JSON input"
          />

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
              className="sidebar-dialog-action gap-1.5 text-label"
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
