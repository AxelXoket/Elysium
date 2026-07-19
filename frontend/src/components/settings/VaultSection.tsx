/**
 * VaultSection - passphrase management inside the Secrets tab.
 *
 * Change-passphrase only (locking is implicit: closing the app locks the
 * vault, since the key lives in backend RAM). Passphrases exist ONLY in
 * component state; cleared on success. Wire styles reuse the right-panel
 * form idiom (bordered card + small labeled inputs).
 */
import { useState, type FormEvent } from "react";
import { KeyRound, Loader2 } from "lucide-react";
import { useChangeVaultPassphrase } from "@/lib/query/vault";
import { isApiError } from "@/lib/api/client";

const MIN_PASSPHRASE_LEN = 8;

export function VaultSection() {
  const change = useChangeVaultPassphrase();
  const [oldPass, setOldPass] = useState("");
  const [newPass, setNewPass] = useState("");
  const [confirm, setConfirm] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setDone(false);
    if (newPass.length < MIN_PASSPHRASE_LEN) {
      setLocalError(`New passphrase needs at least ${MIN_PASSPHRASE_LEN} characters.`);
      return;
    }
    if (newPass !== confirm) {
      setLocalError("The new entries do not match.");
      return;
    }
    setLocalError(null);
    change.mutate(
      { oldPassphrase: oldPass, newPassphrase: newPass },
      {
        onSuccess: () => {
          setOldPass("");
          setNewPass("");
          setConfirm("");
          setDone(true);
        },
      },
    );
  };

  const serverError =
    change.isError && isApiError(change.error)
      ? change.error.detail === "wrong_passphrase"
        ? "Current passphrase is wrong."
        : "Change failed. Is the backend running?"
      : null;

  const inputStyle = {
    backgroundColor: "rgba(255,255,255,0.5)",
    border: "1px solid rgba(28, 38, 50, 0.16)",
    color: "var(--color-es-text-light)",
  } as const;

  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2">
        <KeyRound size={13} style={{ color: "var(--color-es-primary-sage)" }} />
        <h4
          className="text-xs font-semibold"
          style={{ color: "var(--color-es-text-light)" }}
        >
          Vault Passphrase
        </h4>
      </div>
      <p className="text-[11px]" style={{ color: "var(--color-es-text-muted)" }}>
        Everything on disk is encrypted with this passphrase. Changing it
        re-encrypts the database in place.
      </p>
      <form className="space-y-2" onSubmit={submit}>
        <label className="block space-y-1 text-[11px] font-medium" style={{ color: "var(--color-es-text-muted)" }}>
          <span>Current passphrase</span>
          <input
            type="password"
            value={oldPass}
            onChange={(e) => setOldPass(e.target.value)}
            autoComplete="current-password"
            disabled={change.isPending}
            className="vault-secrets-input h-8 w-full rounded-lg px-2.5 text-xs"
            style={inputStyle}
          />
        </label>
        <label className="block space-y-1 text-[11px] font-medium" style={{ color: "var(--color-es-text-muted)" }}>
          <span>New passphrase</span>
          <input
            type="password"
            value={newPass}
            onChange={(e) => setNewPass(e.target.value)}
            autoComplete="new-password"
            disabled={change.isPending}
            className="vault-secrets-input h-8 w-full rounded-lg px-2.5 text-xs"
            style={inputStyle}
          />
        </label>
        <label className="block space-y-1 text-[11px] font-medium" style={{ color: "var(--color-es-text-muted)" }}>
          <span>Repeat new passphrase</span>
          <input
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
            disabled={change.isPending}
            className="vault-secrets-input h-8 w-full rounded-lg px-2.5 text-xs"
            style={inputStyle}
          />
        </label>
        {(localError ?? serverError) && (
          <p className="text-[11px]" role="alert" style={{ color: "var(--color-es-danger)" }}>
            {localError ?? serverError}
          </p>
        )}
        {done && !localError && (
          <p
            className="text-[11px] font-semibold"
            style={{ color: "var(--color-es-primary-sage-deep)" }}
          >
            Passphrase changed.
          </p>
        )}
        <button
          type="submit"
          disabled={
            change.isPending ||
            oldPass.length === 0 ||
            newPass.length === 0 ||
            confirm.length === 0
          }
          className="generation-trigger inline-flex h-8 items-center justify-center rounded-lg px-3 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-50"
        >
          {change.isPending ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            "Change passphrase"
          )}
        </button>
      </form>
    </section>
  );
}
