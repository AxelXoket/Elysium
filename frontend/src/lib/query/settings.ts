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
  setAutoLock,
  setScreenPrivacy,
  setImageOutput,
  setSelectedModel,
  setThinkingEffort,
  deleteProxy,
  getProxyHealth,
} from "../api/settings";
import type { Settings } from "../schemas/settings";
import { useErrorStore } from "@/lib/errors";

export function useSettings() {
  return useQuery({
    queryKey: keys.settings(),
    queryFn: getSettings,
    staleTime: 10_000,
  });
}

export function useProxyHealth(enabled = true, pollMs?: number) {
  return useQuery({
    queryKey: keys.proxyHealth(),
    queryFn: getProxyHealth,
    staleTime: 15_000,
    // Off for the Notes tab's reading line unless a proxy is both required
    // and set up: only then can its health stop the note reader
    // (`proxy_gate`). That line asks on the worker poll's clock, so a proxy
    // that goes down or comes back changes it without a remount; the server
    // answers from its own cache, so this adds no probe of its own.
    enabled,
    refetchInterval: enabled && pollMs ? pollMs : false,
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
              // Same rule as useSetProxyAlias below: only a name we KNOW goes
              // into the cache ahead of the refetch. A null here means the
              // server is writing its default name, and the refetch is what
              // brings that name back; writing the null through left the
              // panel with no name at all in between.
              ...(vars.proxyAlias !== null
                ? { proxy_alias: vars.proxyAlias }
                : {}),
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
      // Only a name we KNOW is written into the cache ahead of the refetch.
      // An emptied box is no longer a delete - the server stores the default
      // proxy name - so writing `null` here would show the panel as having no
      // proxy name at all, which is the exact state this change removed.
      if (proxyAlias !== null) {
        qc.setQueryData<Settings>(keys.settings(), (prev) =>
          prev ? { ...prev, proxy_alias: proxyAlias } : prev,
        );
      }
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

/**
 * Toggle generated image output.
 *
 * The server's answer is written through rather than the value we sent, for the
 * same reason the stop-sequence mutation does it: the vault is the truth and the
 * UI must not drift from it.
 */
export function useSetImageOutput() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) => setImageOutput(enabled),
    onSuccess: (data) => {
      qc.setQueryData<Settings>(keys.settings(), (prev) =>
        prev ? { ...prev, image_output_enabled: data.image_output_enabled } : prev,
      );
    },
    // K-22. There was no onError at all, and the caller passes no options
    // either, so a refused write snapped the switch back with nothing said
    // anywhere. A rejected save and a mis-registered click looked identical.
    onError: (err: unknown) => useErrorStore.getState().pushError(err),
  });
}

/**
 * Set the idle timeout. Writes the server's answer through, like its
 * neighbours: the vault is the truth and the UI must not drift from it.
 */
export function useSetAutoLock() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (minutes: number) => setAutoLock(minutes),
    onSuccess: (data) => {
      qc.setQueryData<Settings>(keys.settings(), (prev) =>
        prev ? { ...prev, auto_lock_minutes: data.auto_lock_minutes } : prev,
      );
    },
  });
}

/**
 * Persist which model is selected, in the vault.
 *
 * Called by useStaleSelectionReconciliation whenever uiStore's in-memory
 * `selectedModelId` changes - never directly by a component, so this hook has
 * no UI-facing consumer of its own. Same write-through shape as its
 * neighbours: the vault is the truth, so the cache is updated from the
 * server's own answer.
 */
export function useSetSelectedModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (modelId: string | null) => setSelectedModel(modelId),
    onSuccess: (data) => {
      qc.setQueryData<Settings>(keys.settings(), (prev) =>
        prev ? { ...prev, selected_model_id: data.selected_model_id } : prev,
      );
    },
  });
}

/** Names the thinking-choice save, so a reader can see which saves are
 *  still in flight (useMutationState) and lay them over the settings read. */
export const THINKING_EFFORT_MUTATION_KEY = ["settings", "thinking-effort"] as const;
const THINKING_EFFORT_SCOPE = "settings-thinking-effort";

export interface ThinkingEffortVars {
  modelId: string;
  effort: string | null;
}

function withEffort(
  prev: Settings | undefined,
  modelId: string,
  effort: string | null,
): Settings | undefined {
  if (!prev) return prev;
  const next = { ...prev.thinking_efforts };
  if (effort === null) delete next[modelId];
  else next[modelId] = effort;
  return { ...prev, thinking_efforts: next };
}

/**
 * Keep the thinking choice for one model, in the vault.
 *
 * - A settings read already in flight is cancelled first: it started before
 *   this save, so its answer is older than the choice and must not land
 *   over it.
 * - The cache changes at once, so the select does not wait on the round
 *   trip. A read that starts while the save is pending cannot undo it
 *   either: GenerationSettingsContext lays pending saves over the read.
 * - Saves run one at a time (the mutation scope), so answers come back in
 *   the order the picks were made and the last pick is the one the vault
 *   keeps. Each answer is written, then the settings are read again, so
 *   the screen ends on what the vault holds.
 * - A refused save puts back the value from before it and says why.
 */
export function useSetThinkingEffort() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: THINKING_EFFORT_MUTATION_KEY,
    // One save at a time: the next waits until the one before has answered,
    // so the server applies them in the order they were picked. Two saves
    // in flight together can commit in either order, and the vault would
    // then keep the older pick.
    scope: { id: THINKING_EFFORT_SCOPE },
    mutationFn: (vars: ThinkingEffortVars) =>
      setThinkingEffort(vars.modelId, vars.effort),
    onMutate: async (vars) => {
      await qc.cancelQueries({ queryKey: keys.settings() });
      const before = qc.getQueryData<Settings>(keys.settings())
        ?.thinking_efforts[vars.modelId];
      qc.setQueryData<Settings>(keys.settings(), (prev) =>
        withEffort(prev, vars.modelId, vars.effort),
      );
      return { before: before ?? null };
    },
    onSuccess: (data) => {
      qc.setQueryData<Settings>(keys.settings(), (prev) =>
        prev ? { ...prev, thinking_efforts: data.thinking_efforts } : prev,
      );
    },
    onError: (err: unknown, vars, context) => {
      if (context) {
        qc.setQueryData<Settings>(keys.settings(), (prev) =>
          withEffort(prev, vars.modelId, context.before),
        );
      }
      useErrorStore.getState().pushError(err);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: keys.settings() });
    },
  });
}

/** Same shape as useSetAutoLock: the vault is the truth, so the server's
 *  answer is written through rather than a local guess. */
export function useSetScreenPrivacy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) => setScreenPrivacy(enabled),
    onSuccess: (_data, enabled) => {
      qc.setQueryData<Settings>(keys.settings(), (prev) =>
        prev ? { ...prev, screen_privacy_enabled: enabled } : prev,
      );
    },
    onError: (err: unknown) => useErrorStore.getState().pushError(err),
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
