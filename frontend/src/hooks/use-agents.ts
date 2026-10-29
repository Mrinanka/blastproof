import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { errorText } from "@/components/common/states";
import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/errors";
import type { Agent } from "@/lib/agents";

// The backend's maximum page; History's agent filter lists at most this many.
export const AGENTS_LIMIT = 100;

// Under "agents" so creating or deactivating one (F11) can invalidate ["agents"].
export const agentsKey = ["agents", "list", { limit: AGENTS_LIMIT }] as const;

export function useAgents(enabled = true) {
  return useQuery({
    queryKey: agentsKey,
    enabled,
    queryFn: () => unwrap(api.GET("/v1/agents", { params: { query: { limit: AGENTS_LIMIT } } })),
  });
}

export function useCreateAgent() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => unwrap(api.POST("/v1/agents", { body: { name } })),
    // The response holds the key, shown once: nothing may keep it after the dialog closes.
    gcTime: 0,
    onSettled: () => client.invalidateQueries({ queryKey: ["agents"] }),
  });
}

export function useDeactivateAgent() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (agent: Pick<Agent, "id" | "name">) =>
      unwrap(
        api.POST("/v1/agents/{agent_id}/deactivate", { params: { path: { agent_id: agent.id } } }),
      ),
    onSuccess: (_data, agent) => toast.success(`Deactivated ${agent.name}`),
    onError: (error) => toast.error(errorText(error)),
    // A 404 means the list is stale, so it refreshes either way.
    onSettled: () => client.invalidateQueries({ queryKey: ["agents"] }),
  });
}
