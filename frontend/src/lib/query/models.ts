import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { keys } from "./keys";
import { useErrorStore } from "../errors";
import { listModels } from "../api/models";

export function useModels() {
  return useQuery({
    queryKey: keys.models(),
    queryFn: () => listModels(),
    staleTime: 300_000, // 5 min - matches backend TTL
  });
}

export function useRefreshModels() {
  const qc = useQueryClient();
  const pushError = useErrorStore((s) => s.pushError);
  return useMutation({
    mutationFn: () => listModels(true),
    onSuccess: (data) => {
      // Seed the cache with the mutation result directly - invalidating here
      // would trigger a redundant second fetch of the same endpoint.
      qc.setQueryData(keys.models(), data);
    },
    // No inline surface exists for the refresh button → toast is the single
    // error surface for this mutation.
    onError: (err) => {
      pushError(err);
    },
  });
}
