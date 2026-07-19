import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { keys } from "./keys";
import {
  getVaultStatus,
  initVault,
  unlockVault,
  lockVault,
  changeVaultPassphrase,
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

/** Explicit "lock now": drops the backend's in-RAM key. On success only the
 * vault-status key is invalidated - the gate flips to the lock screen and its
 * lock-hygiene effect purges every cached data query (no invalidateAll here:
 * refetching data against a locked backend would just be a 423 storm). */
export function useLockVault() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: lockVault,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.vault() });
    },
  });
}

export function useChangeVaultPassphrase() {
  return useMutation({
    mutationFn: (vars: { oldPassphrase: string; newPassphrase: string }) =>
      changeVaultPassphrase(vars.oldPassphrase, vars.newPassphrase),
  });
}
