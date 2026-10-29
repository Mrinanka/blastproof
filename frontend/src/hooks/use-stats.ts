import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/errors";
import { OVERVIEW_POLL_MS } from "@/lib/overview";

// Under "actions" so invalidating ["actions"] after a decision refreshes the cards too.
export const STATS_KEY = ["actions", "stats"] as const;

export function useStats() {
  return useQuery({
    queryKey: STATS_KEY,
    refetchInterval: OVERVIEW_POLL_MS,
    queryFn: () => unwrap(api.GET("/v1/stats")),
  });
}
