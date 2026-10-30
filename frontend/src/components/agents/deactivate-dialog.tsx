"use client";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { Agent } from "@/lib/agents";

export function DeactivateDialog({
  agent,
  pending,
  onConfirm,
  onClose,
}: {
  agent: Agent | null;
  pending: boolean;
  onConfirm: (agent: Agent) => void;
  onClose: () => void;
}) {
  return (
    <Dialog open={agent !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Deactivate this agent</DialogTitle>
          <DialogDescription>
            {agent
              ? `${agent.name}'s key stops working at once. Its pending changes stay in the review queue.`
              : null}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={pending}
            onClick={() => agent && !pending && onConfirm(agent)}
          >
            {pending ? "Deactivating…" : "Deactivate"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
