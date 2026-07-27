/**
 * tts.ts - TanStack hooks for the voice subsystem.
 *
 * Invalidation philosophy: almost every voice mutation can change almost every
 * voice read (loading a model changes state AND active AND models' readiness;
 * installing a runtime changes readiness everywhere), so mutations sweep the
 * whole `keys.tts()` namespace rather than hand-picking - a missed pick here
 * would show a stale "will not run" badge next to a model that just started
 * working, which is exactly the kind of lie this subsystem exists to avoid.
 *
 * Polling: `useTtsInstallStatus` polls fast while a job is running (the user
 * is watching a progress log) and stops on its own when the job ends.
 * `useTtsState` polls slowly - it is how a worker crash becomes visible even
 * when nobody presses anything.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  cancelInstall,
  deleteVoice,
  getInstallPlan,
  getInstallStatus,
  getTtsActive,
  getTtsSchema,
  getTtsSettings,
  getTtsState,
  getVoiceMode,
  listTtsModels,
  listTtsRuntimes,
  listVoices,
  loadVoice,
  rescanTtsModels,
  resetTtsSettings,
  saveTtsSettings,
  setTtsActive,
  setVoiceMode,
  setVoiceTranscript,
  startInstall,
  transcribeVoice,
  uninstallRuntime,
  unloadVoice,
  uploadVoice,
  getTagPrefs,
} from "../api/tts";
import type { TtsParamValue } from "../schemas/tts";
import { keys } from "./keys";

// ── reads ────────────────────────────────────────────────────────────────────

export function useTtsModels(enabled = true) {
  return useQuery({
    queryKey: keys.ttsModels(),
    queryFn: listTtsModels,
    enabled,
    // A scan walks the filesystem and reads the GPU once; do not hammer it.
    staleTime: 15_000,
  });
}

export function useTtsSchema(uid: string | null) {
  return useQuery({
    queryKey: keys.ttsSchema(uid ?? ""),
    queryFn: () => getTtsSchema(uid as string),
    enabled: uid != null,
    staleTime: 60_000, // descriptors change only when the model files do
  });
}

export function useTtsSettings(uid: string | null) {
  return useQuery({
    queryKey: keys.ttsSettings(uid ?? ""),
    queryFn: () => getTtsSettings(uid as string),
    enabled: uid != null,
  });
}

export function useTtsActive(enabled = true) {
  return useQuery({
    queryKey: keys.ttsActive(),
    queryFn: getTtsActive,
    enabled,
    // While the model is coming up, this is the ONLY thing that tells the chat
    // so - and a cold Fish S2 load takes tens of seconds. A 15 s staleTime with
    // no interval meant every voice control sat in its loading face long after
    // the model was ready, and the user pressed a button that had silently
    // become live. Polling stops the moment it settles.
    // Stops on ERROR too (KÖK 15). TanStack v5 keeps the last SUCCESSFUL
    // data through a failure, so a predicate reading only `data` polls
    // forever once the request starts failing - and no consumer renders
    // isError, so nothing on screen ever says the answer went stale.
    // vault.ts:20 has always done this correctly.
    refetchInterval: (query) =>
      query.state.status === "error"
        ? false
        : query.state.data?.state === "loading"
          ? 1_500
          : false,
    staleTime: 15_000,
  });
}

export function useTtsState(opts?: { poll?: boolean }) {
  return useQuery({
    queryKey: keys.ttsState(),
    queryFn: getTtsState,
    // The slow heartbeat that makes a crashed worker VISIBLE without anyone
    // pressing anything - the backend stores the death reason; this fetch is
    // what carries it to the screen.
    refetchInterval: opts?.poll === false ? undefined : 10_000,
  });
}

export function useTtsRuntimes(enabled = true) {
  return useQuery({
    queryKey: keys.ttsRuntimes(),
    queryFn: listTtsRuntimes,
    enabled,
    staleTime: 15_000,
  });
}

export function useTtsInstallPlan(engineId: string | null) {
  return useQuery({
    queryKey: [...keys.ttsInstall(engineId ?? ""), "plan"] as const,
    queryFn: () => getInstallPlan(engineId as string),
    enabled: engineId != null,
    staleTime: 60_000,
  });
}

export function useTtsInstallStatus(engineId: string | null) {
  return useQuery({
    queryKey: keys.ttsInstall(engineId ?? ""),
    queryFn: () => getInstallStatus(engineId as string),
    enabled: engineId != null,
    // Fast while the user is watching a live install log; stops by itself
    // the moment the job reaches a terminal state.
    // Same as useTtsActive: without the error arm the install progress
    // bar never resolves - it keeps polling a failing endpoint against
    // the last job snapshot that said `running`.
    refetchInterval: (query) =>
      query.state.status === "error"
        ? false
        : query.state.data?.running
          ? 700
          : false,
  });
}

export function useTtsVoices(enabled = true) {
  return useQuery({
    queryKey: keys.ttsVoices(),
    queryFn: listVoices,
    enabled,
    staleTime: 15_000,
  });
}

/**
 * The delivery dials, cached app-wide.
 *
 * Read by the STREAM, not only by the settings panel: the sentence-gap value
 * has to be known when a reply starts playing, and a value that only the open
 * Delivery page knew was the reason the dial did nothing.
 */
export function useTagPrefs(enabled = true) {
  return useQuery({
    queryKey: keys.ttsTagPrefs(),
    queryFn: getTagPrefs,
    enabled,
    staleTime: 30_000,
  });
}

export function useVoiceMode() {
  return useQuery({
    queryKey: keys.ttsVoiceMode(),
    queryFn: getVoiceMode,
    // The context gauge reads this on every chat render; keep it cheap.
    staleTime: 30_000,
  });
}

// ── mutations ────────────────────────────────────────────────────────────────

function useTtsSweep() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: keys.tts() });
}

export function useRescanTtsModels() {
  const sweep = useTtsSweep();
  return useMutation({ mutationFn: rescanTtsModels, onSuccess: sweep });
}

export function useSelectTtsModel() {
  const sweep = useTtsSweep();
  return useMutation({
    mutationFn: (uid: string) => setTtsActive(uid),
    onSuccess: sweep,
  });
}

export function useSaveTtsSettings(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (values: Record<string, TtsParamValue>) =>
      saveTtsSettings(uid, values),
    onSuccess: (data) => {
      // The response IS the fresh effective state - write it through instead
      // of refetching what we are already holding.
      qc.setQueryData(keys.ttsSettings(uid), data);
    },
  });
}

export function useResetTtsSettings(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => resetTtsSettings(uid),
    onSuccess: (data) => {
      qc.setQueryData(keys.ttsSettings(uid), data);
    },
  });
}

export function useLoadVoice() {
  const sweep = useTtsSweep();
  return useMutation({
    mutationFn: (uid?: string) => loadVoice(uid),
    onSettled: sweep, // load failures still change state (error_code sticks)
  });
}

export function useUnloadVoice() {
  const sweep = useTtsSweep();
  return useMutation({ mutationFn: unloadVoice, onSettled: sweep });
}

export function useStartInstall() {
  const sweep = useTtsSweep();
  return useMutation({
    mutationFn: (engineId: string) => startInstall(engineId),
    onSettled: sweep,
  });
}

export function useCancelInstall() {
  const sweep = useTtsSweep();
  return useMutation({
    mutationFn: (engineId: string) => cancelInstall(engineId),
    onSettled: sweep,
  });
}

export function useUninstallRuntime() {
  const sweep = useTtsSweep();
  return useMutation({
    mutationFn: (engineId: string) => uninstallRuntime(engineId),
    onSettled: sweep,
  });
}

export function useUploadVoice() {
  const sweep = useTtsSweep();
  return useMutation({
    mutationFn: (input: {
      voiceId: string;
      file: File;
      label?: string;
      transcript?: string;
    }) => uploadVoice(input.voiceId, input.file, input),
    onSuccess: sweep,
  });
}

export function useSetVoiceTranscript() {
  const sweep = useTtsSweep();
  return useMutation({
    mutationFn: (input: { voiceId: string; text: string }) =>
      setVoiceTranscript(input.voiceId, input.text),
    onSuccess: sweep,
  });
}

export function useTranscribeVoice() {
  const sweep = useTtsSweep();
  return useMutation({
    mutationFn: (voiceId: string) => transcribeVoice(voiceId),
    onSuccess: sweep,
  });
}

export function useDeleteVoice() {
  const sweep = useTtsSweep();
  return useMutation({
    mutationFn: (voiceId: string) => deleteVoice(voiceId),
    onSuccess: sweep,
  });
}

export function useSetVoiceMode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) => setVoiceMode(enabled),
    onSuccess: (data) => {
      // Written through immediately: the context gauge charges the injected
      // block off this value, and a stale flag here means the gauge and the
      // backend budget briefly disagree - G2's whole point is that they never do.
      qc.setQueryData(keys.ttsVoiceMode(), data);
    },
  });
}
