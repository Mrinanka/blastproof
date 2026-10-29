import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { errorText } from "@/components/common/states";
import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/errors";

export const SYSTEMS_LIMIT = 100;

export const systemsKey = ["systems", "list", { limit: SYSTEMS_LIMIT }] as const;

export function useSystems(enabled: boolean) {
  return useQuery({
    queryKey: systemsKey,
    enabled,
    queryFn: () => unwrap(api.GET("/v1/systems", { params: { query: { limit: SYSTEMS_LIMIT } } })),
  });
}

export function useRescan() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (systemId: string) =>
      unwrap(
        api.POST("/v1/systems/{system_id}/scan", { params: { path: { system_id: systemId } } }),
      ),
    onSuccess: (result) => toast.success(`Schema rescanned: ${result.chunks} chunks`),
    onError: (error) => toast.error(errorText(error)),
    onSettled: () => client.invalidateQueries({ queryKey: ["knowledge"] }),
  });
}
