import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { errorText } from "@/components/common/states";
import { api } from "@/lib/api/client";
import { ApiError, unwrap } from "@/lib/api/errors";

// Approve and reject for one action. Both invalidate ["actions"] on success and on error: a 409 or
// 404 means the page is stale, so it refreshes the detail, the queue and the badge either way.
export function useDecision(id: string) {
  const client = useQueryClient();
  const params = { params: { path: { action_id: id } } };
  const settled = () => client.invalidateQueries({ queryKey: ["actions"] });

  const approve = useMutation({
    mutationFn: () => unwrap(api.POST("/v1/actions/{action_id}/approve", params)),
    onSuccess: () => toast.success("Approved"),
    // The backend's 409 text already names who decided ("Already approved by Demo User 2.").
    onError: (error) => toast.error(errorText(error)),
    onSettled: settled,
  });

  const reject = useMutation({
    mutationFn: (reason: string) =>
      unwrap(api.POST("/v1/actions/{action_id}/reject", { ...params, body: { reason } })),
    onSuccess: () => toast.success("Rejected"),
    // Other errors show inside the dialog. A 409 also toasts: the refresh that follows hides the
    // decision bar, and the dialog with it, so the reviewer would otherwise see nothing.
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) toast.error(errorText(error));
    },
    onSettled: settled,
  });

  return { approve, reject, pending: approve.isPending || reject.isPending };
}
