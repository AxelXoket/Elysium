/**
 * VaultGate - the boot gate for full-DB passphrase encryption.
 *
 * Flow: vault status loading → quiet splash; not initialized → create
 * passphrase; initialized but locked → unlock; unlocked → the app.
 * Any data endpoint answering 423 later (backend restart) re-engages the
 * gate via setVaultLockedHandler → vault-status refetch.
 *
 * Design law: no new visual language. The screens live on the SAME living
 * shell (mist backdrop) and the card reuses the sidebar-dialog recipes
 * (gradient surface, hairline border, panel radius, field/action classes).
 * Passphrases exist ONLY in component state - never persisted, never logged.
 */
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { motion as m } from "motion/react";
import { Eye, EyeOff, Loader2 } from "lucide-react";
import { ElysiumMark } from "@/components/brand/ElysiumMark";
import { Wordmark } from "@/components/brand/Wordmark";
import { MistCanvas } from "@/components/backdrop/MistCanvas";
import { LockOverlay } from "@/components/vault/LockOverlay";
import { useReducedMotion } from "@/components/motion/ReducedMotion";
import { setVaultLockedHandler, isApiError } from "@/lib/api/client";
import { setVaultLockAnimationHandler } from "@/lib/vaultLockUi";
import { keys } from "@/lib/query/keys";
import {
  useVaultStatus,
  useInitVault,
  useUnlockVault,
} from "@/lib/query/vault";

const MIN_PASSPHRASE_LEN = 8;

/** Passphrase input with a show/hide reveal toggle. The visibility state is
 * per-field and local; the plaintext never leaves component state. */
function PassphraseField({
  label,
  value,
  onChange,
  autoComplete,
  disabled,
  autoFocus,
  ariaInvalid,
  ariaDescribedby,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  autoComplete: string;
  disabled?: boolean;
  autoFocus?: boolean;
  ariaInvalid?: boolean;
  ariaDescribedby?: string;
}) {
  const [visible, setVisible] = useState(false);
  return (
    <label className="vault-label">
      {label}
      <span className="vault-input-wrap">
        <input
          type={visible ? "text" : "password"}
          className="sidebar-dialog-field vault-input"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoComplete={autoComplete}
          disabled={disabled}
          autoFocus={autoFocus}
          aria-invalid={ariaInvalid || undefined}
          aria-describedby={ariaDescribedby}
        />
        <button
          type="button"
          className="vault-eye"
          // preventDefault on mousedown keeps focus (and the caret) in the
          // input while toggling - otherwise the click steals focus and the
          // user must click back into the field mid-passphrase.
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? "Hide passphrase" : "Show passphrase"}
          aria-pressed={visible}
          disabled={disabled}
        >
          {visible ? <EyeOff size={15} /> : <Eye size={15} />}
        </button>
      </span>
    </label>
  );
}

function VaultFrame({ children }: { children: ReactNode }) {
  return (
    <div className="elysium-shell elysium-page">
      <MistCanvas />
      <div className="vault-stage">{children}</div>
    </div>
  );
}

/** Quiet splash while the very first status roundtrip resolves. */
function VaultSplash() {
  return (
    <VaultFrame>
      <div className="vault-splash" role="status" aria-label="Loading Elysium">
        <span style={{ color: "#CFE0F2" }}>
          <ElysiumMark size={118} />
        </span>
        <Loader2 size={15} className="animate-spin" style={{ opacity: 0.7 }} />
      </div>
    </VaultFrame>
  );
}

export function VaultGate({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const reduced = useReducedMotion();
  const { data: status, isLoading, isError } = useVaultStatus();

  // Re-engage the gate when any data call answers 423 (vault locked out
  // from under the app - e.g. the backend restarted).
  useEffect(() => {
    setVaultLockedHandler(() => {
      void qc.invalidateQueries({ queryKey: keys.vault() });
    });
    return () => setVaultLockedHandler(null);
  }, [qc]);

  // The sidebar's lock button hands its API call over as `commit`; the
  // overlay fires it at the choreography's click moment, so the lock snaps
  // shut OVER the still-visible app and the gate flips only under the
  // deepening ink. No handler registered (tests) => the button commits
  // immediately - locking never depends on the animation.
  const [lockCommit, setLockCommit] = useState<(() => void) | null>(null);
  useEffect(() => {
    setVaultLockAnimationHandler((commit) => setLockCommit(() => commit));
    return () => setVaultLockAnimationHandler(null);
  }, []);

  // Lock hygiene: unlocking unmounts nothing, but LOCKING unmounts the app
  // while TanStack keeps every cached chat/message/character in RAM - and
  // would serve them again on re-unlock. Purge all user-data queries on the
  // unlocked -> locked transition; only the gate's own vault key survives.
  const wasUnlockedRef = useRef(false);
  useEffect(() => {
    const unlocked = status?.unlocked === true;
    if (wasUnlockedRef.current && !unlocked) {
      qc.removeQueries({
        predicate: (query) => query.queryKey[0] !== keys.vault()[0],
      });
    }
    wasUnlockedRef.current = unlocked;
  }, [status?.unlocked, qc]);

  // One keyed stage per gate state: the key remount gives every stage an
  // ENTER fade (the app additionally breathes in from scale 0.992). Enter-
  // only on purpose - every stage shares the same mist-on-ink foundation, so
  // the old screen vanishing instantly under the new one's fade reads as one
  // smooth motion, and it sidesteps the AnimatePresence exit bookkeeping
  // that StrictMode+motion is known to wedge (the variant carousel learned
  // this the hard way). The lock overlay rides ABOVE the swap.
  let stage: string;
  let content: ReactNode;
  if (isLoading) {
    stage = "splash";
    content = <VaultSplash />;
  } else if (isError || !status) {
    stage = "unreachable";
    content = (
      <VaultFrame>
        <div className="vault-card" role="alert" aria-label="Backend unreachable">
          <div className="vault-head">
            <span className="vault-brand">
              <ElysiumMark size={104} />
              <Wordmark size={26} tone="onDark" />
            </span>
            <h1 className="vault-title">Cannot reach the backend</h1>
            <p className="vault-note">
              Start the local server, then this screen will retry on its own.
            </p>
          </div>
        </div>
      </VaultFrame>
    );
  } else if (!status.initialized) {
    stage = "create";
    content = <CreatePassphrase />;
  } else if (!status.unlocked) {
    stage = "lock";
    content = <LockScreen />;
  } else {
    stage = "app";
    content = children;
  }

  return (
    <>
      <m.div
        key={stage}
        initial={{ opacity: 0, scale: stage === "app" ? 0.992 : 1 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{
          duration: reduced ? 0 : stage === "app" ? 0.45 : 0.3,
          ease: [0.4, 0, 0.2, 1],
        }}
      >
        {content}
      </m.div>
      {lockCommit != null && (
        <LockOverlay
          onCommit={lockCommit}
          onDone={() => setLockCommit(null)}
        />
      )}
    </>
  );
}

/* ── First run: create the passphrase ──────────────────────────────── */

function CreatePassphrase() {
  const init = useInitVault();
  const [pass, setPass] = useState("");
  const [confirm, setConfirm] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (pass.length < MIN_PASSPHRASE_LEN) {
      setLocalError(`Use at least ${MIN_PASSPHRASE_LEN} characters.`);
      return;
    }
    if (pass !== confirm) {
      setLocalError("The two entries do not match.");
      return;
    }
    setLocalError(null);
    init.mutate(pass);
  };

  const serverError =
    init.isError && isApiError(init.error)
      ? init.error.detail === "passphrase_too_short"
        ? `Use at least ${MIN_PASSPHRASE_LEN} characters.`
        : "Setup failed. Is the backend running?"
      : null;

  return (
    <VaultFrame>
      <form className="vault-card" onSubmit={submit} aria-label="Create passphrase">
        <div className="vault-head">
          <span className="vault-brand">
            <ElysiumMark size={114} />
            <Wordmark size={27} tone="onDark" />
          </span>
          <h1 className="vault-title">Protect your world</h1>
          <p className="vault-note">
            Everything Elysium stores - chats, characters, personas, images -
            is encrypted on disk with this passphrase.
          </p>
        </div>
        <PassphraseField
          label="Passphrase"
          value={pass}
          onChange={setPass}
          autoComplete="new-password"
          disabled={init.isPending}
          autoFocus
          ariaInvalid={(localError ?? serverError) != null}
          ariaDescribedby={
            localError ?? serverError ? "vault-create-error" : undefined
          }
        />
        <PassphraseField
          label="Repeat passphrase"
          value={confirm}
          onChange={setConfirm}
          autoComplete="new-password"
          disabled={init.isPending}
        />
        {(localError ?? serverError) && (
          <p id="vault-create-error" className="vault-error" role="alert">
            {localError ?? serverError}
          </p>
        )}
        <p className="vault-warning">
          There is no recovery. If the passphrase is forgotten, the data is
          gone - by design.
        </p>
        <button
          type="submit"
          className="sidebar-dialog-action vault-submit"
          disabled={init.isPending || pass.length === 0 || confirm.length === 0}
        >
          {init.isPending ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            "Create vault"
          )}
        </button>
      </form>
    </VaultFrame>
  );
}

/* ── Later runs: unlock ────────────────────────────────────────────── */

function LockScreen() {
  const unlock = useUnlockVault();
  const [pass, setPass] = useState("");
  const [shakeKey, setShakeKey] = useState(0);

  const wrongPass =
    unlock.isError &&
    isApiError(unlock.error) &&
    unlock.error.detail === "wrong_passphrase";

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (pass.length === 0 || unlock.isPending) return;
    unlock.mutate(pass, {
      onSuccess: () => setPass(""), // shorten plaintext lifetime in state/DOM
      onError: (err) => {
        // Clear + shake ONLY for a wrong passphrase. A transient network or
        // backend hiccup must not wipe a long, correctly-typed passphrase.
        if (isApiError(err) && err.detail === "wrong_passphrase") {
          setPass("");
          setShakeKey((k) => k + 1);
        }
      },
    });
  };

  return (
    <VaultFrame>
      <form
        key={shakeKey}
        className={`vault-card${shakeKey > 0 ? " vault-card-shake" : ""}`}
        onSubmit={submit}
        aria-label="Unlock Elysium"
      >
        <div className="vault-head">
          <span className="vault-brand">
            <ElysiumMark size={114} />
            <Wordmark size={27} tone="onDark" />
          </span>
          <h1 className="vault-title">Elysium is locked</h1>
          <p className="vault-note">Enter your passphrase to open the vault.</p>
        </div>
        <PassphraseField
          label="Passphrase"
          value={pass}
          onChange={setPass}
          autoComplete="current-password"
          disabled={unlock.isPending}
          autoFocus
          ariaInvalid={wrongPass}
        />
        {unlock.isError && (
          <p className="vault-error" role="alert">
            {wrongPass
              ? "Wrong passphrase."
              : "Unlock failed. Is the backend running?"}
          </p>
        )}
        <button
          type="submit"
          className="sidebar-dialog-action vault-submit"
          disabled={unlock.isPending || pass.length === 0}
        >
          {unlock.isPending ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            "Unlock"
          )}
        </button>
      </form>
    </VaultFrame>
  );
}
