/**
 * EmptyStubNotice - the leftover crash recovery moves aside.
 *
 * When a migration crashed between its two renames, the live app.db could be
 * left as a 0-byte stub with the real database sitting in app.db.enc-tmp.
 * Recovery swaps the good one back into place and renames the stub to
 * app.db.empty-stub-bak rather than unlinking it, so that nobody debugging a
 * recovery ever has to read the sentence "the recovery path deleted a file".
 *
 * That was the right call and it stopped one step short: the name then
 * appeared in no route, no response field and no screen, and there was no way
 * to remove it from inside the app. It just sat there.
 *
 * Deliberately the quietest of the three notices in this folder. The other two
 * are about copies of the user's data - one readable by anyone, one possibly
 * unopenable. This file is provably empty, so the tone is housekeeping, not
 * warning, and the delete button needs no confirmation step: there is nothing
 * in it to lose.
 */
import { Info, Loader2, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useVaultStatus, useDiscardEmptyStub } from "@/lib/query/vault";

export function EmptyStubNotice() {
  const status = useVaultStatus();
  const discard = useDiscardEmptyStub();

  if (!status.data?.empty_stub) return null;

  return (
    <section
      aria-label="Leftover recovery file"
      data-testid="empty-stub-notice"
      className="space-y-2 rounded-lg p-3"
      style={{
        border: "1px solid rgba(255, 255, 255, 0.10)",
        backgroundColor: "rgba(255, 255, 255, 0.03)",
      }}
    >
      <div className="flex items-center gap-2">
        <Info size={13} style={{ color: "var(--color-es-text-muted)" }} />
        <h4
          className="text-xs font-semibold"
          style={{ color: "var(--color-es-text-light)" }}
        >
          A leftover file from a recovery
        </h4>
      </div>
      <p className="settings-hint">
        Elysium once recovered your database after an interrupted move, and set
        the empty file it replaced aside as{" "}
        <code>app.db.empty-stub-bak</code> instead of deleting it. It is empty
        and nothing uses it. You can remove it whenever you like.
      </p>

      {discard.data?.removed === false && discard.data.reason === "not_empty" && (
        <p className="settings-error" role="alert">
          Left alone: that file is not empty any more, so something other than
          the recovery wrote to it. Look at it before removing it by hand.
        </p>
      )}
      {discard.data?.removed === false && discard.data.reason === "not_removed" && (
        <p className="settings-error" role="alert">
          Still on disk: something else has the file open.
        </p>
      )}

      <Button
        type="button"
        variant="ghost"
        size="sm"
        disabled={discard.isPending}
        onClick={() => discard.mutate()}
        className="gap-1 text-xs"
        style={{ color: "var(--color-es-text-muted)" }}
      >
        {discard.isPending ? (
          <Loader2 size={12} className="animate-spin" />
        ) : (
          <Trash2 size={12} />
        )}
        Remove it
      </Button>
    </section>
  );
}
