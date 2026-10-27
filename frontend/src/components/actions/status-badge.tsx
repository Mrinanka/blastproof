import { cn } from "cn";

import { Badge } from "@/components/ui/badge";
import type { components } from "@/lib/api/schema";

import { TONE_CLASSES, type Tone } from "./tone";

export type ActionStatus = components["schemas"]["ActionStatus"];

// Record<ActionStatus, ...>: a status added by the backend breaks typecheck after gen:api.
export const STATUS_META: Record<ActionStatus, { label: string; tone: Tone }> = {
  received: { label: "Received", tone: "progress" },
  classified: { label: "Classified", tone: "progress" },
  previewing: { label: "Previewing", tone: "progress" },
  awaiting_review: { label: "Awaiting review", tone: "warning" },
  approved: { label: "Approved", tone: "progress" },
  rejected: { label: "Rejected", tone: "danger" },
  expired: { label: "Expired", tone: "muted" },
  blocked: { label: "Blocked", tone: "danger" },
  committing: { label: "Committing", tone: "progress" },
  committed: { label: "Committed", tone: "success" },
  drifted: { label: "Drifted", tone: "muted" },
  executed: { label: "Executed", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
};

// A string the frontend does not know (a newer backend) shows raw, neutral.
export function StatusBadge({ status }: { status: string }) {
  const meta = (STATUS_META as Record<string, { label: string; tone: Tone } | undefined>)[status];
  const tone = meta?.tone ?? "neutral";
  return (
    <Badge variant="outline" data-tone={tone} className={cn(TONE_CLASSES[tone])}>
      {meta?.label ?? status}
    </Badge>
  );
}
