import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/errors";
import type { paths } from "@/lib/api/schema";

export type ActionsQuery = NonNullable<paths["/v1/actions"]["get"]["parameters"]["query"]>;

// Under "actions" so invalidating ["actions"] after a decision refreshes every list too.
export const actionsKey = (query: ActionsQuery) => ["actions", "list", query] as const;

export function useActions(query: ActionsQuery, options: { refetchInterval?: number } = {}) {
  return useQuery({
    queryKey: actionsKey(query),
    refetchInterval: options.refetchInterval,
    // Changing page keeps the old rows on screen instead of flashing a skeleton.
    placeholderData: keepPreviousData,
    queryFn: () => unwrap(api.GET("/v1/actions", { params: { query } })),
  });
}
