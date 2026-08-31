import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { create } from "zustand";
import { useErrorStore } from "@/lib/errors";
import { keys } from "./keys";
import {
  getVaultStatus,
  initVault,
  unlockVault,
  lockVault,
  changeVaultPassphrase,
  discardPlaintextBackup,
  discardOrphanedCopy,
  discardEmptyStub,
} from "@/lib/api/vault";

/** Vault status drives the boot gate (create → unlock → app). */
export function useVaultStatus() {
  return useQuery({
    queryKey: keys.vault(),
    queryFn: getVaultStatus,
    // The gate must react promptly; status is a tiny local call.
    staleTime: 0,
    // While the backend is unreachable the gate shows a waiting card that
    // promises to retry - this interval IS that retry.
    refetchInterval: (query) => (query.state.status === "error" ? 2500 : false),
  });
}

/** After any successful unlock-ish transition the whole data layer becomes
 * reachable - refetch everything, not just the vault status. */
function useInvalidateAllOnSuccess() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries();
  };
}

export function useInitVault() {
  const onUnlocked = useInvalidateAllOnSuccess();
  return useMutation({
    mutationFn: (passphrase: string) => initVault(passphrase),
    onSuccess: onUnlocked,
  });
}

export function useUnlockVault() {
  const onUnlocked = useInvalidateAllOnSuccess();
  return useMutation({
    mutationFn: (passphrase: string) => unlockVault(passphrase),
    onSuccess: onUnlocked,
  });
}

/**
 * Whether the CURRENT lock attempt left generated speech readable on disk.
 *
 * /vault/lock answers this in its own response body, not in /vault/status,
 * so nothing that only polls status ever sees it - it exists for exactly as
 * long as this one mutation's result does. LockOverlay is not the component
 * that calls the mutation (SidebarHeader owns that call and hands the
 * animation only a bare `commit` callback - see lib/vaultLockUi.ts), so this
 * store is the one place both sides can reach: useLockVault's onSuccess/
 * onError settle it, LockOverlay reads it to decide whether the closing
 * animation stays silent or has to speak.
 *
 * `pending` lets a reader tell "nothing survived" (settled, empty) apart
 * from "the call has not answered yet" (still pending) - collapsing those
 * would let a slow lock read as a clean one before the backend has actually
 * said so.
 */
interface LockAudioWarningState {
  pending: boolean;
  audioLeft: string[];
  begin: () => void;
  settle: (audioLeft: string[]) => void;
}

export const useLockAudioWarningStore = create<LockAudioWarningState>((set) => ({
  pending: false,
  audioLeft: [],
  begin: () => set({ pending: true, audioLeft: [] }),
  settle: (audioLeft) => set({ pending: false, audioLeft }),
}));

/** Explicit "lock now": drops the backend's in-RAM key. On success only the
 * vault-status key is invalidated - the gate flips to the lock screen and its
 * lock-hygiene effect purges every cached data query (no invalidateAll here:
 * refetching data against a locked backend would just be a 423 storm). */
export function useLockVault() {
  const qc = useQueryClient();
  const beginAudioWarning = useLockAudioWarningStore((s) => s.begin);
  const settleAudioWarning = useLockAudioWarningStore((s) => s.settle);
  return useMutation({
    mutationFn: lockVault,
    onMutate: () => {
      beginAudioWarning();
    },
    onSuccess: (data) => {
      settleAudioWarning(data.audio_left ?? []);
      void qc.invalidateQueries({ queryKey: keys.vault() });
    },
    // A refused lock left nothing new on disk - settle empty so the overlay
    // never sits waiting on a mutation that already failed. SidebarHeader's
    // own onError still reports the failure itself; this only unblocks the
    // animation.
    onError: () => {
      settleAudioWarning([]);
    },
  });
}

export function useChangeVaultPassphrase() {
  return useMutation({
    mutationFn: (vars: { oldPassphrase: string; newPassphrase: string }) =>
      changeVaultPassphrase(vars.oldPassphrase, vars.newPassphrase),
  });
}

/** Removing the plaintext copy changes what /vault/status reports, so the
 *  status query has to be refetched or the warning stays on screen after the
 *  file is gone. */
//: How many times each discard button has FAILED, one counter per button.
//
// Module-level rather than a ref: the counter's job is to make each press a
// distinct toast identity, and it has to survive the notice unmounting and
// remounting (which is exactly what happens when /vault/status refetches
// after a failure). Monotonic, never read for anything else, and it means
// nothing to anyone but the dedupe.
let plaintextPresses = 0;
let orphanPresses = 0;
let emptyStubPresses = 0;

export function useDiscardPlaintextBackup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: discardPlaintextBackup,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.vault() });
    },
    // K-22's idiom, four irreversible deletions later. There was no
    // `onError` anywhere on these, no MutationCache to catch them, and the
    // `role="alert"` paragraphs in the notices report fields of a SUCCESSFUL
    // response - so a 500 or a 423 said nothing at all, and "the file is
    // gone" looked exactly like "nothing was even attempted" to the one
    // person who cannot check.
    //
    // Per hook rather than a MutationCache default: `useUnlockVault` and
    // `useChangeVaultPassphrase` show their failures INSIDE the screen on
    // purpose, and a global handler would double every one of those.
    // A SOURCE, because the dedupe could not tell these apart.
    //
    // All four discards push the same codes with no chat id, so
    // their identity was the code alone: lock the vault with
    // Settings open, press Remove on the first notice, press it on
    // the second inside the toast's window, and the second failure
    // was dropped entirely - silence, in the panel these hooks were
    // added to stop being silent. A per-press counter rides along
    // too: every one of these is one deliberate button press, so a
    // retry after a 'please try again' toast is a new event and
    // must be answered, while one failure reported twice is still
    // one toast.
    onError: (err: unknown) => {
      plaintextPresses += 1;
      useErrorStore.getState().pushError(err, "error", {
        source: `vault:discard-plaintext#${plaintextPresses}`,
      });
    },
  });
}

/** Same refetch reason as the plaintext discard: the warning has to disappear
 *  once the file does. */
export function useDiscardOrphanedCopy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: discardOrphanedCopy,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.vault() });
    },
    // K-22's idiom, four irreversible deletions later. There was no
    // `onError` anywhere on these, no MutationCache to catch them, and the
    // `role="alert"` paragraphs in the notices report fields of a SUCCESSFUL
    // response - so a 500 or a 423 said nothing at all, and "the file is
    // gone" looked exactly like "nothing was even attempted" to the one
    // person who cannot check.
    //
    // Per hook rather than a MutationCache default: `useUnlockVault` and
    // `useChangeVaultPassphrase` show their failures INSIDE the screen on
    // purpose, and a global handler would double every one of those.
    // A SOURCE, because the dedupe could not tell these apart.
    //
    // All four discards push the same codes with no chat id, so
    // their identity was the code alone: lock the vault with
    // Settings open, press Remove on the first notice, press it on
    // the second inside the toast's window, and the second failure
    // was dropped entirely - silence, in the panel these hooks were
    // added to stop being silent. A per-press counter rides along
    // too: every one of these is one deliberate button press, so a
    // retry after a 'please try again' toast is a new event and
    // must be answered, while one failure reported twice is still
    // one toast.
    onError: (err: unknown) => {
      orphanPresses += 1;
      useErrorStore.getState().pushError(err, "error", {
        source: `vault:discard-orphaned#${orphanPresses}`,
      });
    },
  });
}

/** Same refetch reason again. */
export function useDiscardEmptyStub() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: discardEmptyStub,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.vault() });
    },
    // K-22's idiom, four irreversible deletions later. There was no
    // `onError` anywhere on these, no MutationCache to catch them, and the
    // `role="alert"` paragraphs in the notices report fields of a SUCCESSFUL
    // response - so a 500 or a 423 said nothing at all, and "the file is
    // gone" looked exactly like "nothing was even attempted" to the one
    // person who cannot check.
    //
    // Per hook rather than a MutationCache default: `useUnlockVault` and
    // `useChangeVaultPassphrase` show their failures INSIDE the screen on
    // purpose, and a global handler would double every one of those.
    // A SOURCE, because the dedupe could not tell these apart.
    //
    // All four discards push the same codes with no chat id, so
    // their identity was the code alone: lock the vault with
    // Settings open, press Remove on the first notice, press it on
    // the second inside the toast's window, and the second failure
    // was dropped entirely - silence, in the panel these hooks were
    // added to stop being silent. A per-press counter rides along
    // too: every one of these is one deliberate button press, so a
    // retry after a 'please try again' toast is a new event and
    // must be answered, while one failure reported twice is still
    // one toast.
    onError: (err: unknown) => {
      emptyStubPresses += 1;
      useErrorStore.getState().pushError(err, "error", {
        source: `vault:discard-empty-stub#${emptyStubPresses}`,
      });
    },
  });
}
