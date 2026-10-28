import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/errors";

// The backend's maximum page; History's agent filter lists at most this many.
export const AGENTS_LIMIT = 100;

// Under "agents" so creating or deactivating one (F11) can invalidate ["agents"].
export const agentsKey = ["agents", "list", { limit: AGENTS_LIMIT }] as const;

export function useAgents() {
  return useQuery({
    queryKey: agentsKey,
    queryFn: () => unwrap(api.GET("/v1/agents", { params: { query: { limit: AGENTS_LIMIT } } })),
  });
}
