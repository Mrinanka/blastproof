import { cn } from "cn";

import { Badge } from "@/components/ui/badge";
import type { components } from "@/lib/api/schema";

import { TONE_CLASSES, type Tone } from "./tone";

export type RiskLevel = components["schemas"]["RiskLevel"];

export const RISK_META: Record<RiskLevel, { label: string; tone: Tone }> = {
  low: { label: "Low risk", tone: "success" },
  medium: { label: "Medium risk", tone: "warning" },
  high: { label: "High risk", tone: "danger" },
  critical: { label: "Critical risk", tone: "danger" },
};

export function RiskBadge({ level }: { level: string | null }) {
  const meta =
    level === null
      ? undefined
      : (RISK_META as Record<string, { label: string; tone: Tone } | undefined>)[level];
  const tone: Tone = level === null ? "muted" : (meta?.tone ?? "neutral");
  const label = level === null ? "No risk score" : (meta?.label ?? level);
  return (
    <Badge variant="outline" data-tone={tone} className={cn(TONE_CLASSES[tone])}>
      {label}
    </Badge>
  );
}
