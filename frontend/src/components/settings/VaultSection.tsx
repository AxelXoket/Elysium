/**
 * VaultSection - passphrase management inside the Secrets tab.
 *
 * Change-passphrase only (locking is implicit: closing the app locks the
 * vault, since the key lives in backend RAM). Passphrases exist ONLY in
 * component state; cleared on success. Wire styles reuse the right-panel
 * form idiom (bordered card + small labeled inputs).
 */
import { useState, type FormEvent } from "react";
import { KeyRound, Loader2, AlertCircle } from "lucide-react";
import { useChangeVaultPassphrase } from "@/lib/query/vault";
import { PlaintextBackupNotice } from "./PlaintextBackupNotice";
import { OrphanedCopyNotice } from "./OrphanedCopyNotice";
import { RotationBackupNotice } from "./RotationBackupNotice";
import { EmptyStubNotice } from "./EmptyStubNotice";
import { AutoLockControl } from "./AutoLockControl";
import { isApiError } from "@/lib/api/client";
import { getErrorMessage } from "@/lib/errors/errorMessages";

const MIN_PASSPHRASE_LEN = 12;

export function VaultSection() {
  const change = useChangeVaultPassphrase();
  const [oldPass, setOldPass] = useState("");
  const [newPass, setNewPass] = useState("");
  const [confirm, setConfirm] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  // Sidecar copies the rotation could NOT re-key: still openable with the
  // passphrase this form just promised to revoke. Comes back on the SAME
  // response as `ok: true`, nowhere else - change-passphrase is the only
  // place this value ever appears, so dropping it here loses it for good.
  const [unrevoked, setUnrevoked] = useState<string[]>([]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setDone(false);
    // Cleared on every new attempt, success or not - a stale warning from a
    // PREVIOUS change must not sit under a form the user is filling in again.
    setUnrevoked([]);
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
        onSuccess: (data) => {
          setOldPass("");
          setNewPass("");
          setConfirm("");
          setDone(true);
          setUnrevoked(data.unrevoked ?? []);
        },
      },
    );
  };

  const serverError =
    change.isError && isApiError(change.error)
      ? change.error.detail === "wrong_passphrase"
        ? "Current passphrase is wrong."
        : getErrorMessage(change.error.detail)
      : null;

  const inputStyle = {
    backgroundColor: "rgba(255,255,255,0.5)",
    border: "1px solid rgba(28, 38, 50, 0.16)",
    color: "var(--color-es-text-light)",
  } as const;

  return (
    <section className="space-y-3">
      {/* Above the passphrase form on purpose: an unencrypted copy of the
          whole database outranks anything else on this screen, and the form
          under it is the one that promises encryption. */}
      <PlaintextBackupNotice />
      <OrphanedCopyNotice />
      {/* Beside the orphaned copy, because it is the same size of problem -
          a whole second vault - with one difference that matters more than
          the similarity: this one opens with a passphrase the user believes
          they revoked. */}
      <RotationBackupNotice />
      {/* Last of the four, because it is the least: the other two are copies
          of the user's data and this one is provably an empty file. */}
      <EmptyStubNotice />
      <AutoLockControl />
      <div className="flex items-center gap-2">
        <KeyRound size={13} style={{ color: "var(--color-es-primary-sage)" }} />
        <h4
          className="text-xs font-semibold"
          style={{ color: "var(--color-es-text-light)" }}
        >
          Vault passphrase
        </h4>
      </div>
      {/* The complete list lives here; the setup card in VaultGate carries a
          shorter true version, because a lock screen is not a policy document
          and the wallpaper is the least of the three. Both must stay honest.

          Why each one is listed - do not trim this back to the wallpaper:
          1. Spoken replies: tts/host.py writes every reply as a plain wav
             under the data folder. routers/vault.py calls that cache "the
             user's conversation in audible form, in the clear, next to a
             database that is encrypted". Wiped at lock, launch and shutdown
             and trimmed at thirty minutes, so it is transient, not encrypted.
          2. The cloning reference: tts/refs.py stores the recorded clip and
             its transcript as plain files under voice/refs/ and nothing ever
             purges them. Conditional wording on purpose - reference clips
             only exist for engines that clone, so a user without such a model
             must not be told they have files they do not have.
          3. The wallpaper: it lives as a blob in the browser profile's
             IndexedDB, outside the vault entirely.

          Checked and deliberately not listed: the voice models folder (the
          user's own weights, not their content) and elysium.log (audited in
          run_app.py to carry no chat content, keys or passphrases).

          This sentence has been wrong once before and was only half fixed:
          audit FF15 in v1.1 caught it claiming images were encrypted while
          the wallpaper was not, added the wallpaper, and stopped there. */}
      {/* `settings-hint` again - see ScreenPrivacySection for the measured
          numbers. `text-muted-foreground` is the sibling panels' idiom. */}
      <p data-testid="vault-disclosure-hint" className="text-xs leading-relaxed text-muted-foreground">
        Everything on disk is encrypted with this passphrase, with three
        exceptions. Spoken replies are written as plain audio files, your
        conversation in audible form, and are wiped at every lock, launch and
        shutdown. Any voice clip you add for cloning stays as a plain file
        with its transcript, and is not wiped. The decorative chat wallpaper
        is not encrypted. Changing the passphrase re-encrypts the database in
        place.
      </p>
      {/* `settings-label` has no colour rule of its own on the dark dialog -
          it just inherits. `color` is an inherited property fixed once at
          `body`, and nothing between
          `body` and this label re-declares it, so `.glass-right`'s
          redefined token never gets a chance to apply. Measured at
          1.05-1.14:1 here - effectively invisible. There is no
          `persona-label` counterpart, so this borrows the nearest idiom
          that IS proven on this surface: the same `text-xs font-semibold`
          + explicit `--color-es-text-light` the sibling panels use for
          their headings, applied to the caption span instead of an h4. */}
      <form className="space-y-2" onSubmit={submit}>
        <label className="block space-y-1">
          <span
            data-testid="vault-label-current"
            className="text-xs font-semibold"
            style={{ color: "var(--color-es-text-light)" }}
          >
            Current passphrase
          </span>
          <input
            type="password"
            maxLength={1024}
            value={oldPass}
            onChange={(e) => setOldPass(e.target.value)}
            autoComplete="current-password"
            disabled={change.isPending}
            className="vault-secrets-input h-8 w-full rounded-lg px-2.5 text-xs"
            style={inputStyle}
          />
        </label>
        <label className="block space-y-1">
          <span
            data-testid="vault-label-new"
            className="text-xs font-semibold"
            style={{ color: "var(--color-es-text-light)" }}
          >
            New passphrase
          </span>
          <input
            type="password"
            maxLength={1024}
            value={newPass}
            onChange={(e) => setNewPass(e.target.value)}
            autoComplete="new-password"
            disabled={change.isPending}
            className="vault-secrets-input h-8 w-full rounded-lg px-2.5 text-xs"
            style={inputStyle}
          />
        </label>
        <label className="block space-y-1">
          <span
            data-testid="vault-label-repeat"
            className="text-xs font-semibold"
            style={{ color: "var(--color-es-text-light)" }}
          >
            Repeat new passphrase
          </span>
          <input
            type="password"
            maxLength={1024}
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="new-password"
            disabled={change.isPending}
            className="vault-secrets-input h-8 w-full rounded-lg px-2.5 text-xs"
            style={inputStyle}
          />
        </label>
        {/* `persona-local-error`, the notebook panels' refusal class on
            this exact surface, in place of `settings-error`. */}
        {(localError ?? serverError) && (
          <p className="persona-local-error" role="alert">
            {localError ?? serverError}
          </p>
        )}
        {/* `settings-value` carried the same broken hardcoded colour as
            `settings-hint` (see above) underneath its own inline override -
            the inline `color` here already wins the cascade, since neither
            rule uses `!important`, so this swap is about the size/line-height
            the class also carried, not the colour. `text-xs leading-relaxed`
            is the sibling panels' base for a line of this kind. */}
        {done && !localError && (
          <p
            data-testid="vault-success-message"
            className="text-xs leading-relaxed font-semibold"
            style={{ color: "var(--color-es-primary-sage-deep)" }}
          >
            Passphrase changed.
          </p>
        )}
        {/* A clean rotation says nothing beyond "Passphrase changed." - this
            only appears when the backend named a file it could not re-key.
            Same danger recipe as RotationBackupNotice above (this screen's
            one danger colour, same border/background), because it is the
            same size of problem: a complete copy of the vault, readable
            under a passphrase the user just tried to revoke. */}
        {done && !localError && unrevoked.length > 0 && (
          <section
            aria-label="A copy still opens with the old passphrase"
            data-testid="vault-unrevoked-notice"
            className="space-y-2 rounded-lg p-3"
            style={{
              border: "1px solid rgba(195, 106, 114, 0.24)",
              backgroundColor: "rgba(195, 106, 114, 0.10)",
            }}
          >
            <div className="flex items-center gap-2">
              <AlertCircle size={13} style={{ color: "var(--color-es-danger)" }} />
              <h4
                className="text-xs font-semibold"
                style={{ color: "var(--color-es-text-light)" }}
              >
                A copy still opens with the old passphrase
              </h4>
            </div>
            <p data-testid="vault-unrevoked-hint-1" className="text-xs leading-relaxed text-muted-foreground">
              Changing your passphrase should close every copy under the old
              one. One full copy of your vault could not be re-encrypted, so
              it is still readable with the passphrase you just replaced.
            </p>
            <p data-testid="vault-unrevoked-hint-2" className="text-xs leading-relaxed text-muted-foreground">
              Elysium cannot rewrite it automatically. Delete it yourself, or
              move it somewhere safe, from the Elysium data folder:
            </p>
            <ul className="space-y-1">
              {unrevoked.map((name) => (
                <li key={name}>
                  <code data-testid={`vault-unrevoked-name-${name}`} className="text-xs leading-relaxed text-muted-foreground">{name}</code>
                </li>
              ))}
            </ul>
          </section>
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
