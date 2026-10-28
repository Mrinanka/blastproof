import { useQuery, type Query } from "@tanstack/react-query";

import type { ActionStatus } from "@/components/actions/status-badge";
import { api, type Schemas } from "@/lib/api/client";
import { ApiError, unwrap } from "@/lib/api/errors";

export type ActionDetail = Schemas["ActionDetail"];

export const ACTION_POLL_MS = 5000;

// Under "actions" so invalidating ["actions"] after a decision refreshes the detail too.
export const actionKey = (id: string) => ["actions", "detail", id] as const;

// Statuses with no outgoing transition. Record<ActionStatus, ...>: a status added by the backend
// breaks typecheck after gen:api, so it cannot silently poll forever or stop too early.
export const FINAL_STATUS: Record<ActionStatus, boolean> = {
  received: false,
  classified: false,
  previewing: false,
  awaiting_review: false,
  approved: false,
  rejected: true,
  expired: true,
  blocked: true,
  committing: false,
  committed: true,
  drifted: false,
  executed: true,
  failed: true,
};

// Polls until the action is final, so the reviewer sees an approved write land. A 404 will not
// change, so it stops too; any other error keeps polling.
export function pollInterval(query: Query<ActionDetail, Error>): number | false {
  if (query.state.error instanceof ApiError && query.state.error.status === 404) return false;
  const status = query.state.data?.status;
  if (status !== undefined && (FINAL_STATUS as Record<string, boolean | undefined>)[status]) {
    return false;
  }
  return ACTION_POLL_MS;
}

export function useAction(id: string) {
  return useQuery({
    queryKey: actionKey(id),
    refetchInterval: pollInterval,
    queryFn: () =>
      unwrap(api.GET("/v1/actions/{action_id}", { params: { path: { action_id: id } } })),
  });
}
