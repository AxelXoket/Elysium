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
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { motion as m } from "motion/react";
import { z } from "zod/v4";
import { Eye, EyeOff, Loader2 } from "lucide-react";
import { ElysiumMark } from "@/components/brand/ElysiumMark";
import { Wordmark } from "@/components/brand/Wordmark";
import { MistCanvas } from "@/components/backdrop/MistCanvas";
import { LockOverlay } from "@/components/vault/LockOverlay";
import { useReducedMotion } from "@/components/motion/ReducedMotion";
import { setVaultLockedHandler, isApiError, request } from "@/lib/api/client";
import { getErrorMessage } from "@/lib/errors/errorMessages";
import { stopVoicePlayback } from "@/lib/voice/playerStore";
import { setVaultLockAnimationHandler } from "@/lib/vaultLockUi";
import { useDraftStore } from "@/lib/store/draftStore";
import { keys } from "@/lib/query/keys";
import {
  useVaultStatus,
  useInitVault,
  useUnlockVault,
} from "@/lib/query/vault";

const MIN_PASSPHRASE_LEN = 12;

/**
 * The phrase a user must type to wipe the vault and start over.
 *
 * The BACKEND is the authority on this wording, not this file. It is checked
 * there, against RESET_CONFIRMATION_PHRASE in backend/routers/vault.py, so a
 * value drifting here cannot weaken the guard - it can only stop the button
 * working, which is the safe direction for a control that deletes everything.
 * This copy exists so the screen can disable the button before the request is
 * made, and it is exported so the test imports it rather than retyping it.
 */
export const RESET_CONFIRM_PHRASE = "DELETE EVERYTHING";

/** POST /vault/reset. The route landed and the shape is confirmed against
 * backend/routers/vault.py's vault_reset -> _reset_vault_sync: `{ ok, left }`
 * (see RESET_CONFIRM_PHRASE above). Kept local to this file rather than added
 * to lib/api/vault.ts and lib/schemas/vault.ts: this component owns only
 * VaultGate.tsx, and those files are shared ground other work touches
 * concurrently. */
const VaultResetOkSchema = z.object({
  ok: z.boolean(),
  /** What the sweep could NOT remove. The route answers 200 even when this
   *  is non-empty, because the request succeeded - it is the DELETION that
   *  was partial. Named here or z.object strips it, and the screen then
   *  tells somebody every trace is gone while files of theirs are still on
   *  disk. On a route whose whole promise is "everything, at once", that is
   *  the worst sentence this app could say. */
  left: z.array(z.string()).default([]),
});

function resetVault(confirm: string): Promise<{ ok: boolean; left: string[] }> {
  return request("/vault/reset", VaultResetOkSchema, {
    method: "POST",
    body: JSON.stringify({ confirm }),
  });
}

function useResetVault() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: resetVault,
    // Same shape as init/unlock: a reset is a boot-state transition too, and
    // the gate has to see a fresh /vault/status (initialized: false) to swing
    // over to the setup screen on its own - see the stage switch in
    // VaultGate. Nothing here invents a fourth stage.
    onSuccess: (data) => {
      // Unsent drafts go with the vault, and this is the ONE place they must.
      //
      // Drafts deliberately outlive a lock and every remount, because that is
      // the bug the store was written to fix. A reset is not a lock: the
      // database is destroyed and a NEW vault is created in the same running
      // renderer. Chat ids are AUTOINCREMENT and the wipe takes the sequence
      // with the file, so the new vault's first chat is id 1 again - and the
      // store is keyed on that bare integer. Without this, the composer of
      // the first chat in the new vault opens holding text from the vault the
      // user just destroyed. Cleared BEFORE the gate swings, and only on a
      // complete wipe, for the same reason the invalidate is.
      //
      // Deliberately unconditional on the draft side even so: `clearAll` on
      // an empty store is free, and a draft outliving a wipe is the failure
      // worth being blunt about.
      useDraftStore.getState().clearAll();
      // Only a COMPLETE wipe moves on by itself. A partial one keeps the
      // panel up so the survivors can be read; the gate would otherwise
      // swing to the setup screen - the database is gone either way - and
      // carry that list off the screen before anybody saw it.
      if (data.ok) void qc.invalidateQueries();
    },
  });
}

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
          maxLength={1024}
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
  // unlocked -> locked transition; only the gate's own vault key survives in
  // the CACHES.
  //
  // Three module-scope stores also outlive the unmount, and one of them is a
  // deliberate change to what "locked" means, so it is recorded rather than
  // left to be discovered: the draft store (lib/store/draftStore.ts) keeps
  // unsent composer and edit text across a lock ON PURPOSE. Locking used to
  // destroy every half-written sentence in the app, which is a data loss the
  // lock screen was never meant to cause.
  //
  // Why that is not a leak, stated so the next reader does not have to redo
  // the reasoning: the app subtree is UNMOUNTED, not hidden, so after a lock
  // the text is a heap value with no DOM node, no accessible node and no
  // pixel - it cannot be read off the lock screen, which is the boundary
  // SECURITY.md actually draws. Process memory is already an accepted limit
  // there. This is unlike the mutation cache below, which held a CREDENTIAL,
  // and unlike the query cache, which would have been re-served on unlock.
  // The one case where drafts must NOT survive is a vault RESET, handled in
  // useResetVault above.
  const wasUnlockedRef = useRef(false);
  useEffect(() => {
    const unlocked = status?.unlocked === true;
    if (wasUnlockedRef.current && !unlocked) {
      // The spoken conversation must not keep playing over the lock screen -
      // the audio element lives outside the DOM, so unmounting the app does
      // not silence it; only an explicit stop does (audit-2).
      stopVoicePlayback();
      qc.removeQueries({
        predicate: (query) => query.queryKey[0] !== keys.vault()[0],
      });
      // The MUTATION cache too, and this was the sharp one. removeQueries
      // sweeps the query cache only; a mutation keeps its `variables` until
      // garbage collection, five minutes after its last observer goes. Those
      // variables are the payload the user sent - and for the settings save
      // that payload is the OpenRouter API key itself, verbatim. So the key
      // outlived the lock by five minutes, in memory, behind a lock screen
      // that said the session was over.
      //
      // Nothing here depends on a mutation surviving a lock: every one of
      // them has already run, and after unlock the tree is rebuilt from the
      // vault anyway.
      qc.getMutationCache().clear();
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
  const [acknowledged, setAcknowledged] = useState(false);

  // The screen above promises "everything ... is encrypted on disk with this
  // passphrase". For an upgrading user that was not the whole truth: the
  // migration keeps the entire pre-vault database as a fully readable
  // app.db.plain.bak-<ts> beside the vault, forever, surviving every later
  // passphrase change. /vault/init has always reported it - the zod schema
  // even keeps the field - and nothing rendered it, so the only notice was a
  // server log line. Correcting the claim belongs on the screen that made it.
  const plaintextBackup =
    init.isSuccess && init.data?.migrated ? init.data.backup : null;

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

  // Through the shared error map, like every other surface in the app. Hard
  // coding two codes and collapsing the rest into "Is the backend running?"
  // blamed a running backend for every other outcome - and hid the one state
  // with a one-file fix: encrypted_db_without_identity means the data is
  // intact and salt.bin is missing, which restoring that file recovers.
  const serverError =
    init.isError && isApiError(init.error)
      ? init.error.detail === "passphrase_too_short"
        ? `Use at least ${MIN_PASSPHRASE_LEN} characters.`
        : getErrorMessage(init.error.detail)
      : null;

  return (
    <VaultFrame>
      {plaintextBackup && !acknowledged ? (
        <div className="vault-card" role="alert" aria-label="Migration notice">
          <div className="vault-head">
            <h1 className="vault-title">One file is still readable</h1>
            <p className="vault-note">
              Your data moved into the encrypted vault. The database from
              before is kept as a safety copy, and it is NOT encrypted:
            </p>
            <p className="vault-note">
              <code>{plaintextBackup}</code>
            </p>
            <p className="vault-note">
              It sits in the Elysium data folder. Nothing needs it once the app
              works normally - delete it whenever you are satisfied everything
              came across.
            </p>
          </div>
          <button
            type="button"
            className="sidebar-dialog-action vault-submit"
            onClick={() => setAcknowledged(true)}
          >
            Got it
          </button>
        </div>
      ) : (
      <form className="vault-card" onSubmit={submit} aria-label="Create passphrase">
        <div className="vault-head">
          <span className="vault-brand">
            <ElysiumMark size={114} />
            <Wordmark size={27} tone="onDark" />
          </span>
          <h1 className="vault-title">Protect your world</h1>
          {/* An exception clause reads as exhaustive, so every item left out
              of it is a claim that item IS encrypted. This one listed the
              wallpaper alone and left out the two that matter more:

              1. SPOKEN REPLIES. tts/host.py writes each reply as a plain wav
                 under the data folder. The backend says it plainly in
                 routers/vault.py: the cache "is the user's conversation in
                 audible form, in the clear, next to a database that is
                 encrypted". It is wiped at lock, launch and shutdown and
                 trimmed at thirty minutes, so it is transient - but while it
                 is there it is the conversation, unencrypted. Transient is
                 not encrypted, and this screen is where the promise is made.
              2. THE CLONING REFERENCE. tts/refs.py keeps the clip the user
                 recorded and a transcript of the words in it as plain files
                 under voice/refs/, and NOTHING purges those - not the lock,
                 not shutdown. Worded as "any voice clip you add", because
                 reference clips only exist for engines that clone: a user
                 whose model cannot must not be told they have such a file.

              The wallpaper stays in Settings rather than here. It is
              decorative, it is the least of the three, and the gate should
              name what is the conversation itself. Settings carries the
              complete list, this card carries the true short one.

              Checked and deliberately NOT listed: the voice models folder
              holds weights the user dropped in, not their content, and
              elysium.log is audited (run_app.py) to carry no chat content,
              keys or passphrases.

              Do not trim this back to one item. That already happened once:
              audit FF15 in v1.1 caught this exact sentence overclaiming
              ("images ... encrypted" while the wallpaper sat plain in
              IndexedDB), narrowed it to the wallpaper, and left the audible
              conversation unmentioned. A second pass on the same sentence
              is what it cost. */}
          <p className="vault-note">
            Everything Elysium stores - chats, characters, personas, images -
            is encrypted on disk with this passphrase. Two things are not:
            spoken replies, written as plain audio and wiped at every lock,
            and any voice clip you add for cloning, which stays on disk with
            its transcript. Settings has the full list.
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
      )}
    </VaultFrame>
  );
}

/* ── Later runs: unlock ────────────────────────────────────────────── */

function LockScreen() {
  const unlock = useUnlockVault();
  const [pass, setPass] = useState("");
  const [shakeKey, setShakeKey] = useState(0);
  const [resetOpen, setResetOpen] = useState(false);
  // The trigger button unmounts while the reset panel is open (the panel
  // REPLACES the form, the same way CreatePassphrase's migration notice
  // replaces its form above) - so focus cannot return to a ref that no
  // longer exists. It has to live here, one level up, where it survives the
  // swap. Effect below (mirroring NotebookPanel's wasConfirming ref) gives it
  // back the moment the ordinary lock screen is back on screen.
  const forgotTriggerRef = useRef<HTMLButtonElement>(null);
  const wasResetOpen = useRef(false);
  useEffect(() => {
    if (resetOpen) {
      wasResetOpen.current = true;
    } else if (wasResetOpen.current) {
      wasResetOpen.current = false;
      forgotTriggerRef.current?.focus();
    }
  }, [resetOpen]);

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

  if (resetOpen) {
    return (
      <VaultFrame>
        <ResetVaultPanel onCancel={() => setResetOpen(false)} />
      </VaultFrame>
    );
  }

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
        {/* Quiet on purpose - reuses vault-note's own token (12px, the
            card's muted text colour), just underlined to read as a control.
            Sits between the field it answers and the error/submit below it,
            never above or beside Unlock, so a mis-click reaching for the
            passphrase box lands on plain text, not a destructive door. Type
            ="button": it cannot submit the unlock form, and opening it below
            only swaps in an explanation + typed confirmation - nothing here
            can wipe anything by itself. */}
        <button
          ref={forgotTriggerRef}
          type="button"
          className="vault-note"
          style={{
            background: "none",
            border: "none",
            padding: 0,
            textAlign: "left",
            textDecoration: "underline",
            cursor: "pointer",
            alignSelf: "flex-start",
          }}
          onClick={() => setResetOpen(true)}
        >
          Forgot your passphrase?
        </button>
        {unlock.isError && (
          <p className="vault-error" role="alert">
            {isApiError(unlock.error)
              ? getErrorMessage(unlock.error.detail)
              : getErrorMessage("vault_unlock_failed")}
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

/* ── Lock screen: forgotten passphrase -> reset the vault ─────────────
   There is no recovery, so this is the one honest door: wipe and start
   over. Shaped to be hard to reach by accident and impossible to trigger
   by a slip - explanation first, then a typed phrase, and the destructive
   call fires from exactly one place, guarded by that phrase matching. */
function ResetVaultPanel({ onCancel }: { onCancel: () => void }) {
  const reset = useResetVault();
  const [confirmText, setConfirmText] = useState("");
  const cancelButtonRef = useRef<HTMLButtonElement>(null);

  // House standard from NotebookPanel's confirm row: the SAFE choice takes
  // focus on open, so a reflexive Enter keeps the vault rather than erasing
  // it. The destructive button is reachable, just never the reflex.
  useEffect(() => {
    cancelButtonRef.current?.focus();
  }, []);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      // Same stop as NotebookPanel's: Composer binds Escape at the window to
      // "stop generating", and this panel can appear while a reply from
      // before the lock is still technically registered. One Escape here
      // must close only this, never reach past it.
      event.stopPropagation();
      event.preventDefault();
      onCancel();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onCancel]);

  const canSubmit = confirmText === RESET_CONFIRM_PHRASE && !reset.isPending;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    // Checked again here, not just via the button's disabled attribute - a
    // near-miss phrase followed by Enter in the text field still reaches
    // this handler, and disabled alone would not have stopped it.
    if (!canSubmit) return;
    reset.mutate(confirmText);
  };

  return (
    <form className="vault-card" onSubmit={submit} aria-label="Reset the vault">
      <div className="vault-head">
        <span className="vault-brand">
          <ElysiumMark size={114} />
          <Wordmark size={27} tone="onDark" />
        </span>
        <h1 className="vault-title">Start over instead</h1>
        {/* This list is read once, right before an irreversible click, so it
            has to be exact rather than approximate. Checked artefact by
            artefact against _reset_vault_sync in backend/routers/vault.py:

              _reset_database + _reset_backup_families + _reset_premigrate_family
                -> chats, characters, personas, notes and the saved API key
                   (all rows/blobs in app.db, plus every backup family
                   /vault/status already tracks by name - none of that is a
                   new category, so none gets its own bullet)
              _reset_directory_tree(UPLOADS_DIR)   -> uploads
              _reset_directory_tree(TTS_REFS_DIR)  -> saved voice (the
                   cloning reference clip + transcript)
              _reset_directory_tree(TTS_CACHE_DIR) -> cached spoken replies.
                   Usually empty by the time the vault is locked, which is
                   why it had no bullet before - but "usually" is not "never"
                   and that cache is decoded speech of real replies, so it
                   gets named rather than assumed away.
              _reset_legacy_keyring -> the saved API key and proxy address.
                   Both also live in app.db's secrets table (settings.py
                   set_secret), so the database bullet already destroys the
                   live copies; this family destroys the pre-vault OS-keyring
                   copies migrate_legacy_secrets would otherwise hand to the
                   NEXT vault's first unlock.
              _reset_directory_tree(DATA_DIR/webview) -> the local browser
                   profile: the chat wallpaper (IndexedDB), its framing and
                   text-size settings and which chat/character were last open
                   (all persisted there per uiStore's partialize allowlist).
                   Missing from this sentence before now - the one artefact
                   named explicitly in the fix that added this comment.
              (generated images have no separate bullet: attachments_service
               stores every image, uploaded or generated, as a blob inside
               app.db itself - the database bullet already covers them, per
               that function's own docstring)

              _reset_runtime_files -> elysium.log, its one rotated twin
                   (elysium.log.1, all backupCount=1 allows) and the `port`
                   file. These get their own paragraph rather than a place in
                   the list above, because they are not "what the vault
                   holds": they are the app's own trail, and somebody reading
                   this screen is asking for that to go as well.

            THE SENTENCE THIS COMMENT USED TO JUSTIFY WAS FALSE. It said
            elysium.log was "untouched by _reset_vault_sync" and survived the
            reset. _reset_runtime_files shreds it, and the sweep and the
            denial landed in the SAME commit (075506f) - the screen was
            never right about this, not even for a day. The log is audited to
            carry no chat content, keys or passphrases, but notebook_worker
            .py logs `chat_id` on an extraction failure, which is a record of
            which chats had note-taking activity; that is why the log is
            worth naming at all, and now it is named as destroyed.

            WHAT ACTUALLY SURVIVES, per _reset_vault_sync's own docstring:
            TTS_MODELS_DIR (the model drop folder), TTS_BIN_DIR/TTS_ENVS_DIR/
            TTS_PY_DIR (the engine runtimes) and TTS_UV_CACHE_DIR (the
            uv/python install caches). Downloaded ENGINE software, multi-
            gigabyte, hours to reprovision, and carrying no conversation.
            Note what is NOT in that set: TTS_REFS_DIR, the user's own
            recorded voice, and TTS_CACHE_DIR, decoded speech of real
            replies. Both are swept, and both are named above.

            One survivor is deliberately left out of the copy: when app.db
            cannot be shredded, _reset_vault_sync holds salt.bin/verifier
            .bin/kdf.json back on purpose rather than bricking a surviving
            database. That is a FAILURE path, not a promise, and the screen
            already reports it after the fact through `left` (reset-left
            below) instead of hedging the sentence somebody reads before
            they click. */}
        <p className="vault-note">
          This does not recover your passphrase - nothing does. It deletes
          everything the vault holds: chats, characters, personas, notes,
          uploads, generated images, saved voice, cached spoken replies, and
          the saved API key and proxy address. It also deletes the local
          browser profile: the chat wallpaper, its framing, text-size
          settings, and which chat and character were last open. All of it,
          at once, and it cannot be undone.
        </p>
        <p className="vault-note">
          The app's own trail goes with it: <code>elysium.log</code>, its
          rotated copy, and the file that remembers which port Elysium
          listened on. That log never carried chat content, keys or
          passphrases, but it did record which chats triggered a note-taking
          pass, and that record is destroyed here too.
        </p>
        <p className="vault-note">
          One thing survives, and it is engine software rather than anything
          of yours: the downloaded voice runtime, its install caches, and any
          voice models you added. Those are gigabytes and hours to fetch
          again, and they hold nothing about you or your conversations. Your
          own recorded voice is not among them; it is deleted with the rest.
        </p>
        <p className="vault-note">
          Afterwards Elysium opens on the same setup screen first run used.
          You choose a new passphrase there, and the app starts empty.
        </p>
      </div>
      <label className="vault-label">
        {`Type "${RESET_CONFIRM_PHRASE}" to continue`}
        <input
          type="text"
          className="sidebar-dialog-field vault-input"
          value={confirmText}
          onChange={(e) => setConfirmText(e.target.value)}
          autoComplete="off"
          spellCheck={false}
          disabled={reset.isPending}
        />
      </label>
      {/* The request succeeded and the deletion did not. Not an error state,
          and it must not be silent either: these are the user's own files,
          still readable, after a screen promised all of it was gone. */}
      {reset.data && !reset.data.ok && (
        <div className="vault-error" role="alert" data-testid="reset-left">
          <p>
            Some of it could not be deleted, so this is not finished. Elysium
            removed what it could and left the rest exactly as it was:
          </p>
          <ul>
            {reset.data.left.map((what) => (
              <li key={what}>{what}</li>
            ))}
          </ul>
          <p>
            Close anything that might be holding them open and try again, or
            delete them yourself. Until they are gone, what was in them can
            still be read by anyone who has your passphrase.
          </p>
        </div>
      )}
      {reset.isError && (
        <p className="vault-error" role="alert">
          {/* No vault_reset_failed entry exists yet in errorMessages.ts (out
              of scope here - that catalogue is shared ground). An unmapped
              code falls through to its own honest generic sentence rather
              than borrowing unlock's, which would name the wrong failure. */}
          {getErrorMessage(isApiError(reset.error) ? reset.error.detail : null)}
        </p>
      )}
      <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.3rem" }}>
        <button
          ref={cancelButtonRef}
          type="button"
          className="sidebar-dialog-cancel vault-submit"
          style={{ flex: 1 }}
          onClick={onCancel}
        >
          Cancel
        </button>
        <button
          type="submit"
          className="sidebar-dialog-action vault-submit"
          style={{ flex: 1 }}
          disabled={!canSubmit}
        >
          {reset.isPending ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            "Delete everything"
          )}
        </button>
      </div>
    </form>
  );
}
