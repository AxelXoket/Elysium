import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { keys } from "./keys";
import {
  getSettings,
  setApiKey,
  deleteApiKey,
  setProxy,
  setProxyAlias,
  setProxyRequired,
  setStopSequences,
  deleteProxy,
  getProxyHealth,
} from "../api/settings";
import type { Settings } from "../schemas/settings";

export function useSettings() {
  return useQuery({
    queryKey: keys.settings(),
    queryFn: getSettings,
    staleTime: 10_000,
  });
}

export function useProxyHealth() {
  return useQuery({
    queryKey: keys.proxyHealth(),
    queryFn: getProxyHealth,
    staleTime: 15_000,
  });
}

export function useSetApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (apiKey: string) => setApiKey(apiKey),
    // Errors and the ok:false (validation_unavailable - key NOT saved) outcome
    // surface inline in ApiKeySection; no toast here (one-surface rule).
    onSuccess: () => {
      // Invalidate regardless of ok: on ok:false the key was NOT stored, so
      // refetching settings/models is harmless and keeps the UI consistent.
      qc.invalidateQueries({ queryKey: keys.settings() });
      qc.invalidateQueries({ queryKey: keys.models() });
    },
  });
}

export function useDeleteApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => deleteApiKey(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.settings() });
      qc.invalidateQueries({ queryKey: keys.models() });
    },
  });
}

export function useSetProxy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: {
      proxyUrl: string;
      proxyRequired: boolean;
      proxyAlias: string | null;
    }) => setProxy(vars.proxyUrl, vars.proxyRequired, vars.proxyAlias),
    onSuccess: (_data, vars) => {
      // Reflect the saved flags in the cache immediately so UI controls that
      // mirror server state (e.g. the "Require proxy" toggle) don't flicker
      // back to stale values while the invalidated refetch is in flight.
      qc.setQueryData<Settings>(keys.settings(), (prev) =>
        prev
          ? {
              ...prev,
              proxy_configured: true,
              proxy_required: vars.proxyRequired,
              proxy_alias: vars.proxyAlias,
            }
          : prev,
      );
      qc.invalidateQueries({ queryKey: keys.settings() });
      qc.invalidateQueries({ queryKey: keys.proxyHealth() });
      qc.invalidateQueries({ queryKey: keys.models() });
    },
  });
}

/** Persist the label on its own (no URL rewrite). */
export function useSetProxyAlias() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (proxyAlias: string | null) => setProxyAlias(proxyAlias),
    onSuccess: (_data, proxyAlias) => {
      qc.setQueryData<Settings>(keys.settings(), (prev) =>
        prev ? { ...prev, proxy_alias: proxyAlias } : prev,
      );
      qc.invalidateQueries({ queryKey: keys.settings() });
    },
  });
}

/** Persist the kill-switch on its own (no URL rewrite). */
export function useSetProxyRequired() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (proxyRequired: boolean) => setProxyRequired(proxyRequired),
    onSuccess: (_data, proxyRequired) => {
      qc.setQueryData<Settings>(keys.settings(), (prev) =>
        prev ? { ...prev, proxy_required: proxyRequired } : prev,
      );
      qc.invalidateQueries({ queryKey: keys.settings() });
      qc.invalidateQueries({ queryKey: keys.proxyHealth() });
    },
  });
}

/**
 * Persist the stop sequences in the vault.
 *
 * They are the one generation setting that is user CONTENT (character names),
 * so localStorage is closed to them by the S-09b privacy rule - which is why
 * they used to be in-memory only, and had to be retyped every session and
 * after every vault lock.
 */
export function useSetStopSequences() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (stopSequences: string[]) => setStopSequences(stopSequences),
    onSuccess: (data) => {
      // Write the SERVER's list through: it clamps to four, drops blanks and
      // dedupes, so echoing what we sent would let the UI drift from the vault.
      qc.setQueryData<Settings>(keys.settings(), (prev) =>
        prev ? { ...prev, stop_sequences: data.stop_sequences } : prev,
      );
    },
  });
}

export function useDeleteProxy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => deleteProxy(),
    onSuccess: () => {
      // Backend resets proxy_required=false and clears the alias on delete.
      qc.setQueryData<Settings>(keys.settings(), (prev) =>
        prev
          ? {
              ...prev,
              proxy_configured: false,
              proxy_required: false,
              proxy_alias: null,
            }
          : prev,
      );
      qc.invalidateQueries({ queryKey: keys.settings() });
      qc.invalidateQueries({ queryKey: keys.proxyHealth() });
      qc.invalidateQueries({ queryKey: keys.models() });
    },
  });
}

export function useRefreshProxyHealth() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => getProxyHealth(),
    onSuccess: (data) => {
      // Seed the cache with the mutation result directly - invalidating here
      // would trigger a redundant second fetch of the same endpoint.
      qc.setQueryData(keys.proxyHealth(), data);
    },
  });
}
