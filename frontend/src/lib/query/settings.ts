import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { keys } from "./keys";
import {
  getSettings,
  setApiKey,
  deleteApiKey,
  setProxy,
  deleteProxy,
  getProxyHealth,
} from "../api/settings";

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
    onSuccess: () => {
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
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.settings() });
      qc.invalidateQueries({ queryKey: keys.proxyHealth() });
      qc.invalidateQueries({ queryKey: keys.models() });
    },
  });
}

export function useDeleteProxy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => deleteProxy(),
    onSuccess: () => {
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
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.proxyHealth() });
    },
  });
}
