import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { create } from "zustand";
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
export function useDiscardPlaintextBackup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: discardPlaintextBackup,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.vault() });
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
  });
}
