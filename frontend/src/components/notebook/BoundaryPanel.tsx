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
import { useState } from "react";
import { Plus, Trash2, Check, X, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useUiStore } from "@/lib/store/uiStore";
import { useErrorStore } from "@/lib/errors/errorStore";
import {
  useChatBoundaries,
  useCreateBoundary,
  useDeleteBoundary,
  useSetUseGlobalBoundaries,
} from "@/lib/query/notebook";
import type { Boundary } from "@/lib/schemas/notebook";

const SEVERITY_LABEL: Record<string, string> = {
  hard: "never",
  veiled: "off the page",
  soft: "prefer not",
};

export function BoundaryPanel() {
  const chatId = useUiStore((s) => s.selectedChatId);
  const { data } = useChatBoundaries(chatId);
  const create = useCreateBoundary();
  const remove = useDeleteBoundary();
  const setUseGlobal = useSetUseGlobalBoundaries();
  const pushError = useErrorStore((s) => s.pushError);

  const [label, setLabel] = useState("");
  const [severity, setSeverity] = useState("hard");
  const [confirmId, setConfirmId] = useState<number | null>(null);
  // Optimistic only while a save is in flight; the server's answer is the
  // truth the rest of the time.
  const [pendingGlobal, setPendingGlobal] = useState<boolean | null>(null);
  const useGlobal = pendingGlobal ?? data?.use_global ?? true;

  const busy = create.isPending || remove.isPending || setUseGlobal.isPending;
  const rows: Boundary[] = data?.boundaries ?? [];

  async function handleAdd() {
    const text = label.trim();
    if (!text) return;
    try {
      // One field, used twice: what the person reads and what the model reads
      // start identical. They are separate columns so the wording can diverge
      // later without the screen changing under them.
      await create.mutateAsync([{ label: text, phrasing: text, severity }]);
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
    <section className="space-y-3 p-4" aria-label="Limits">
      <h4 className="settings-section-title">
        Limits
      </h4>

      <p className="text-xs leading-relaxed text-muted-foreground">
        Sent with every message and never trimmed to make room. If they do not
        fit, nothing is sent at all - a limit you believe is in force and is
        not would be worse than none.
      </p>

      {chatId != null && (
        <label className="flex items-center gap-2">
          <Switch
            checked={useGlobal}
            disabled={busy}
            onCheckedChange={(v) => void handleToggleGlobal(v)}
            aria-label="Use my global limits in this chat"
          />
          <span className="text-xs leading-relaxed text-muted-foreground">Use my global limits here</span>
        </label>
      )}

      <div className="flex items-center gap-2">
        <Input
          value={label}
          maxLength={240}
          placeholder="Something to keep out of the story..."
          disabled={busy}
          onChange={(e) => setLabel(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void handleAdd();
          }}
          className="text-xs"
        />
        <select
          value={severity}
          disabled={busy}
          onChange={(e) => setSeverity(e.target.value)}
          aria-label="How strict"
          className="settings-value rounded-md px-2 py-1 text-xs"
          style={{
            backgroundColor: "rgba(255,255,255,0.20)",
            border: "1px solid rgba(28,38,50,0.12)",
            color: "var(--color-es-text-light)",
          }}
        >
          <option value="hard">never</option>
          <option value="veiled">off the page</option>
          <option value="soft">prefer not</option>
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

      {rows.length === 0 && <p className="text-xs leading-relaxed text-muted-foreground">No limits set.</p>}

      <div className="space-y-1">
        {rows.map((row) => (
          <div
            key={row.id}
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
            {confirmId !== row.id && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={() => setConfirmId(row.id)}
                aria-label="Delete limit"
                className="persona-danger-action h-7 w-7 p-0"
              >
                <Trash2 size={12} className="size-3" />
              </Button>
            )}
            {confirmId === row.id && (
              <>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  aria-label="Confirm delete limit"
                  className="persona-danger-action h-7 w-7 p-0"
                  onClick={() => {
                    setConfirmId(null);
                    void remove.mutateAsync([row.id]).catch((err) =>
                      pushError(err, "error", { chatId: chatId ?? undefined }),
                    );
                  }}
                >
                  <Check size={12} className="size-3" />
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => setConfirmId(null)}
                  aria-label="Keep limit"
                  className="persona-ghost-action h-7 w-7 p-0"
                >
                  <X size={12} className="size-3" />
                </Button>
              </>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
