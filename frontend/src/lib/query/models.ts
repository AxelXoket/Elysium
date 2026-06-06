import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { keys } from "./keys";
import { listModels } from "../api/models";

export function useModels() {
  return useQuery({
    queryKey: keys.models(),
    queryFn: () => listModels(),
    staleTime: 300_000, // 5 min — matches backend TTL
  });
}

export function useRefreshModels() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => listModels(true),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.models() });
    },
  });
}
