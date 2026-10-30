import type { Schemas } from "@/lib/api/client";
import { formatDuration, formatNumber, formatUsd } from "@/lib/format";

export const OVERVIEW_POLL_MS = 15000;
export const LATEST_LIMIT = 10;

export interface StatCard {
  id: string;
  label: string;
  value: string;
  hint: string;
  href?: string;
}

// Each value is one backend field, formatted: the page does no arithmetic of its own.
export function statCards(
  stats: Schemas["StatsResponse"],
  pending: number | undefined,
): StatCard[] {
  const { today } = stats;
  return [
    {
      id: "pending",
      label: "Pending reviews",
      value: formatNumber(pending ?? null),
      hint: "Right now",
      href: "/dashboard/review",
    },
    {
      id: "reviewed",
      label: "Reviewed today (UTC)",
      value: formatNumber(today.reviewed),
      hint: "Approved or rejected by a person",
    },
    {
      id: "blocked",
      label: "Blocked today (UTC)",
      value: formatNumber(today.blocked),
      hint: "Writes stopped by rules or the judge",
    },
    {
      id: "preview",
      label: "Avg preview time today (UTC)",
      value: formatDuration(today.avg_preview_ms),
      hint: today.avg_preview_ms === null ? "No previews today" : "Across today's actions",
    },
    {
      id: "cost",
      label: "LLM cost today (UTC)",
      value: formatUsd(today.llm_cost_usd),
      hint: "Known costs only",
    },
  ];
}
