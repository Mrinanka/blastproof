"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useEffectEvent, useMemo } from "react";

import { RiskBadge } from "@/components/actions/risk-badge";
import { StatusBadge } from "@/components/actions/status-badge";
import { DataTable, type Column } from "@/components/common/data-table";
import { TimeAgo } from "@/components/common/time-ago";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useActions } from "@/hooks/use-actions";
import { useAgents } from "@/hooks/use-agents";
import { useMe } from "@/hooks/use-me";
import type { Schemas } from "@/lib/api/client";
import {
  HISTORY_PAGE_SIZE,
  applyPatch,
  hasFilters,
  parseHistoryParams,
  toActionsQuery,
  toSearchString,
  withPage,
  type FilterPatch,
  type HistoryFilters as Filters,
} from "@/lib/history-filters";

import { HistoryFilters } from "./history-filters";

type ActionSummary = Schemas["ActionSummary"];

const COLUMNS: Column<ActionSummary>[] = [
  { header: "When", cell: (a) => <TimeAgo iso={a.created_at} /> },
  { header: "Status", cell: (a) => <StatusBadge status={a.status} /> },
  { header: "Agent", cell: (a) => a.agent.name },
  {
    header: "Summary",
    // Table cells do not wrap by default; long intents would push the last columns off screen.
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
  { header: "Tables", cell: (a) => (a.tables.length > 0 ? a.tables.join(", ") : "—") },
  { header: "Risk", cell: (a) => <RiskBadge level={a.risk_level} /> },
  { header: "Decided by", cell: (a) => a.decided_by?.display_name ?? "—" },
];

export function History() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const urlKey = params.toString();
  const filters = useMemo(() => parseHistoryParams(new URLSearchParams(urlKey)), [urlKey]);
  const query = toActionsQuery(filters);
  const actions = useActions(query ?? {}, { enabled: query !== null });
  const agents = useAgents();
  const { data: me } = useMe();

  // Only validated values reach the URL: toSearchString builds it from parsed filters.
  function navigate(next: Filters, how: "push" | "replace" = "push") {
    const search = toSearchString(next);
    router[how](search ? `${pathname}?${search}` : pathname, { scroll: false });
  }

  // A bookmarked page past the end moves to the last page, without a new history entry.
  const data = actions.data;
  const lastPage =
    data && !actions.isPlaceholderData ? Math.max(1, Math.ceil(data.total / HISTORY_PAGE_SIZE)) : 0;
  // An Effect Event reads the latest filters and router without re-running the effect for them.
  const clampPage = useEffectEvent((last: number) => {
    if (filters.page > last) navigate(withPage(filters, last), "replace");
  });
  useEffect(() => {
    if (lastPage > 0) clampPage(lastPage);
  }, [lastPage, filters.page]);

  const filtered = hasFilters(filters);
  return (
    <div className="space-y-4">
      <HistoryFilters
        filters={filters}
        urlKey={urlKey}
        onChange={(patch: FilterPatch) => navigate(applyPatch(filters, patch))}
        onClear={() => navigate(parseHistoryParams(new URLSearchParams()))}
        agents={agents.data?.items}
        agentsPending={agents.isPending}
        agentsFailed={agents.isError}
        isAdmin={me?.role === "admin"}
      />
      {query === null ? (
        <Alert>
          <AlertDescription>The start date is after the end date.</AlertDescription>
        </Alert>
      ) : (
        <DataTable
          label="History"
          columns={COLUMNS}
          rows={data?.items}
          rowKey={(a) => a.id}
          page={data ? { total: data.total, limit: data.limit, offset: data.offset } : undefined}
          onPageChange={(offset) =>
            navigate(withPage(filters, Math.floor(offset / HISTORY_PAGE_SIZE) + 1))
          }
          isLoading={actions.isPending}
          error={actions.error}
          onRetry={() => void actions.refetch()}
          empty={
            filtered
              ? { title: "No actions match these filters", description: "Try removing a filter." }
              : { title: "No actions yet", description: "Actions your agents take show up here." }
          }
        />
      )}
    </div>
  );
}
