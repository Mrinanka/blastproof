"use client";

import Link from "next/link";

import { RiskBadge } from "@/components/actions/risk-badge";
import { StatusBadge } from "@/components/actions/status-badge";
import { DataTable, type Column } from "@/components/common/data-table";
import { TimeAgo } from "@/components/common/time-ago";
import { useActions } from "@/hooks/use-actions";
import type { Schemas } from "@/lib/api/client";
import { LATEST_LIMIT, OVERVIEW_POLL_MS } from "@/lib/overview";

type ActionSummary = Schemas["ActionSummary"];

const COLUMNS: Column<ActionSummary>[] = [
  { header: "When", cell: (a) => <TimeAgo iso={a.created_at} /> },
  { header: "Status", cell: (a) => <StatusBadge status={a.status} /> },
  { header: "Agent", cell: (a) => a.agent.name },
  {
    header: "Summary",
    // Table cells do not wrap by default; long intents would push the last column off screen.
    className: "min-w-72 whitespace-normal",
    cell: (a) => (
      <div className="space-y-0.5">
        <Link href={`/dashboard/actions/${a.id}`} className="font-medium hover:underline">
          {a.headline ?? "Summary not ready"}
        </Link>
        {a.intent ? <p className="text-xs text-muted-foreground">{a.intent}</p> : null}
      </div>
    ),
  },
  { header: "Risk", cell: (a) => <RiskBadge level={a.risk_level} /> },
];

export function LatestActions() {
  const actions = useActions(
    { order: "newest", limit: LATEST_LIMIT },
    { refetchInterval: OVERVIEW_POLL_MS },
  );
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold">Latest actions</h2>
        <Link href="/dashboard/history" className="text-sm hover:underline">
          View all in History
        </Link>
      </div>
      <DataTable
        label="Latest actions"
        columns={COLUMNS}
        rows={actions.data?.items}
        rowKey={(a) => a.id}
        isLoading={actions.isPending}
        error={actions.error}
        onRetry={() => void actions.refetch()}
        empty={{ title: "No actions yet", description: "Actions your agents take show up here." }}
      />
    </section>
  );
}
