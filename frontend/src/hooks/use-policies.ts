import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/errors";

export const POLICIES_LIMIT = 100;

export const policiesKey = ["policies", "list", { limit: POLICIES_LIMIT }] as const;

// No polling: the rules load when the backend starts and change only on a restart.
export function usePolicies() {
  return useQuery({
    queryKey: policiesKey,
    queryFn: () =>
      unwrap(api.GET("/v1/policies", { params: { query: { limit: POLICIES_LIMIT } } })),
  });
}
