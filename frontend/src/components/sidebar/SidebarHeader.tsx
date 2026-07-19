import { Lock } from "lucide-react";
import { ElysiumMark } from "@/components/brand/ElysiumMark";
import { Wordmark } from "@/components/brand/Wordmark";
import { Button } from "@/components/ui/button";
import { useLockVault } from "@/lib/query/vault";
import { requestVaultLockAnimation } from "@/lib/vaultLockUi";
import { useErrorStore } from "@/lib/errors";

export function SidebarHeader() {
  const lock = useLockVault();
  const pushError = useErrorStore((s) => s.pushError);

  const handleLock = () => {
    if (lock.isPending) return;
    // The API call is handed to the overlay as `commit`: it fires at the
    // choreography's click moment, so the lock closes over the visible app
    // BEFORE the gate flips. No overlay listening => commit immediately.
    // On failure the overlay fades back to the app and a toast explains.
    const commit = () =>
      lock.mutate(undefined, { onError: (err) => pushError(err) });
    if (!requestVaultLockAnimation(commit)) commit();
  };

  return (
    <div className="px-4 py-4">
      <div className="sidebar-brand">
        <span className="sidebar-brand-mark">
          <ElysiumMark size={58} />
        </span>
        <div className="flex min-w-0 flex-1 flex-col">
          <Wordmark size={22} tone="onDark" />
          <span
            className="mt-1 truncate text-[11px] leading-none"
            style={{ color: "var(--color-es-text-muted)", opacity: 0.8 }}
          >
            Local-first · Private
          </span>
        </div>
        <Button
          type="button"
          variant="ghost"
          className="sidebar-secondary-action h-9 w-9 shrink-0 p-0"
          aria-label="Lock vault"
          title="Lock vault"
          onClick={handleLock}
          disabled={lock.isPending}
        >
          <Lock size={15} />
        </Button>
      </div>
    </div>
  );
}
