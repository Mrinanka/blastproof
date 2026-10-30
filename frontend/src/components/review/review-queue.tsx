"use client";

import Link from "next/link";
import { useState } from "react";

import { RiskBadge } from "@/components/actions/risk-badge";
import { DataTable, type Column } from "@/components/common/data-table";
import { TimeAgo } from "@/components/common/time-ago";
import { useActions } from "@/hooks/use-actions";
import type { components } from "@/lib/api/schema";

type ActionSummary = components["schemas"]["ActionSummary"];

export const REVIEW_POLL_MS = 5000;
export const REVIEW_PAGE_SIZE = 25;

const COLUMNS: Column<ActionSummary>[] = [
  { header: "Agent", cell: (a) => a.agent.name },
  // Table cells do not wrap by default; a long intent would make the table scroll sideways.
  {
    header: "Intent",
    className: "min-w-64 max-w-md whitespace-normal",
    cell: (a) => a.intent ?? "—",
  },
  {
    header: "Headline",
    className: "min-w-64 whitespace-normal",
    cell: (a) => (
      <Link href={`/dashboard/actions/${a.id}`} className="font-medium hover:underline">
        {a.headline ?? "Summary not ready"}
      </Link>
    ),
  },
  { header: "Risk", cell: (a) => <RiskBadge level={a.risk_level} /> },
  { header: "Waiting", cell: (a) => <TimeAgo iso={a.created_at} /> },
];

export function ReviewQueue() {
  const [requested, setOffset] = useState(0);
  const query = useActions(
    { status: ["awaiting_review"], order: "oldest", limit: REVIEW_PAGE_SIZE, offset: requested },
    { refetchInterval: REVIEW_POLL_MS },
  );
  const data = query.data;
  // Decisions can shrink the queue under the page being viewed: show the last page instead.
  const offset =
    data && data.total > 0 && requested >= data.total
      ? Math.floor((data.total - 1) / REVIEW_PAGE_SIZE) * REVIEW_PAGE_SIZE
      : requested;
  if (offset !== requested) setOffset(offset);

  return (
    <DataTable
      label="Review queue"
      columns={COLUMNS}
      rows={data?.items}
      rowKey={(a) => a.id}
      page={data ? { total: data.total, limit: data.limit, offset: data.offset } : undefined}
      onPageChange={setOffset}
      isLoading={query.isPending}
      error={query.error}
      onRetry={() => void query.refetch()}
      empty={{
        title: "Nothing to review",
        description: "New held writes show up here within 5 seconds.",
      }}
    />
  );
}
