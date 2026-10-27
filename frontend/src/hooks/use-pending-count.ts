import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/errors";

// Under "actions" so invalidating ["actions"] after a decision refreshes the badge too.
export const PENDING_COUNT_KEY = ["actions", "pending-count"] as const;
export const PENDING_POLL_MS = 5000;

export function usePendingCount() {
  return useQuery({
    queryKey: PENDING_COUNT_KEY,
    refetchInterval: PENDING_POLL_MS,
    queryFn: () =>
      unwrap(
        api.GET("/v1/actions", {
          params: { query: { status: ["awaiting_review"], limit: 1 } },
        }),
      ),
    select: (page) => page.total,
  });
}
