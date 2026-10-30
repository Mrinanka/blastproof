"use client";

import { useRef } from "react";

import { RejectDialog } from "@/components/change-request/reject-dialog";
import { Button } from "@/components/ui/button";
import { useDecision } from "@/hooks/use-decision";
import type { ActionDetail } from "@/hooks/use-action";

// Shown only while the write is held and the backend says this user may decide it. The backend
// enforces the permission again on the POST.
export function DecisionBar({ action }: { action: ActionDetail }) {
  const { approve, reject, pending } = useDecision(action.id);
  // The mutation's pending flag lands a tick late, so a fast second click needs this.
  const inFlight = useRef(false);
  if (action.status !== "awaiting_review" || !action.can_decide) return null;
  return (
    <div className="flex flex-wrap gap-2">
      <Button
        disabled={pending}
        onClick={() => {
          if (pending || inFlight.current) return;
          inFlight.current = true;
          approve.mutate(undefined, { onSettled: () => (inFlight.current = false) });
        }}
      >
        {approve.isPending ? "Approving…" : "Approve"}
      </Button>
      <RejectDialog onReject={reject.mutateAsync} disabled={pending} />
    </div>
  );
}
