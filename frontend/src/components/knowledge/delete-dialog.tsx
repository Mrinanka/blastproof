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
import { formatNumber } from "@/lib/format";
import type { KnowledgeSource } from "@/lib/knowledge";

export function DeleteDialog({
  source,
  pending,
  onConfirm,
  onClose,
}: {
  source: KnowledgeSource | null;
  pending: boolean;
  onConfirm: (source: string) => void;
  onClose: () => void;
}) {
  return (
    <Dialog open={source !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete this source</DialogTitle>
          <DialogDescription>
            {source
              ? `${source.source} (${formatNumber(source.chunks)} chunks) will no longer be used to explain changes.`
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
            onClick={() => source && !pending && onConfirm(source.source)}
          >
            {pending ? "Deleting…" : "Delete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
