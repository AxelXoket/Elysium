import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useCreateChat } from "@/lib/query/chats";
import { useUiStore } from "@/lib/store/uiStore";
import { buildStartChatInput } from "@/lib/characters";
import { parseApiError } from "@/lib/errors";
import { MessageSquarePlus, Loader2, AlertCircle } from "lucide-react";
import type { ReactElement } from "react";

interface ChatCreateDialogProps {
  trigger: ReactElement;
}

export function ChatCreateDialog({ trigger }: ChatCreateDialogProps) {
  const [open, setOpen] = useState(false);
  const create = useCreateChat();
  const selectedCharacterId = useUiStore((s) => s.selectedCharacterId);
  const selectChat = useUiStore((s) => s.selectChat);
  const [titleInput, setTitleInput] = useState("");
  const [error, setError] = useState<string | null>(null);

  const resetForm = () => {
    setTitleInput("");
    setError(null);
  };

  const handleSubmit = async () => {
    // buildStartChatInput validates the character id and only ever includes
    // character_id + optional trimmed title - nothing else leaves the app.
    const input = buildStartChatInput(selectedCharacterId, titleInput);
    if (!input) return;
    setError(null);
    try {
      const created = await create.mutateAsync(input);
      selectChat(created.id);
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
      <DialogContent className="glass-dialog sidebar-dialog sm:max-w-sm">
        <DialogHeader>
          <DialogTitle
            className="flex items-center gap-2 text-base font-semibold"
            style={{ color: "var(--color-es-text-light)" }}
          >
            <MessageSquarePlus size={15} style={{ color: "rgba(200, 216, 236, 0.86)" }} />
            New Chat
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          {selectedCharacterId == null ? (
            <p
              className="text-xs"
              style={{ color: "var(--color-es-text-muted)" }}
            >
              Select a character first to start a chat.
            </p>
          ) : (
            // A real form so Enter in the title field submits, matching every
            // other text-entry surface (vault, composer, rename).
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (!create.isPending) void handleSubmit();
              }}
              className="space-y-3"
            >
              <div className="space-y-1">
                <label
                  className="text-xs font-medium"
                  style={{ color: "var(--color-es-text-muted)" }}
                >
                  Title (optional)
                </label>
                <Input
                  value={titleInput}
                  onChange={(e) => setTitleInput(e.target.value)}
                  placeholder="Chat title..."
                  disabled={create.isPending}
                  className="sidebar-dialog-field text-sm"
                />
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

              <div className="flex justify-end gap-2 pt-2">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => setOpen(false)}
                  disabled={create.isPending}
                  className="sidebar-dialog-cancel"
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  size="sm"
                  disabled={create.isPending}
                  className="sidebar-dialog-action gap-1.5 text-xs"
                >
                  {create.isPending && (
                    <Loader2 size={12} className="animate-spin" />
                  )}
                  Create
                </Button>
              </div>
            </form>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
