"use client";

import { useRef, useState } from "react";

import { TONE_CLASSES } from "@/components/actions/tone";
import { DeactivateDialog } from "@/components/agents/deactivate-dialog";
import { DataTable, type Column } from "@/components/common/data-table";
import { TimeAgo } from "@/components/common/time-ago";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { AGENTS_LIMIT, useDeactivateAgent } from "@/hooks/use-agents";
import type { Agent } from "@/lib/agents";
import { formatNumber } from "@/lib/format";

interface Props {
  rows: Agent[] | undefined;
  total: number | undefined;
  isLoading: boolean;
  error: unknown;
  onRetry: () => void;
  // Admins see everyone's agents, so they also see whose each one is.
  showOwner: boolean;
}

export function AgentTable({ rows, total, isLoading, error, onRetry, showOwner }: Props) {
  const [target, setTarget] = useState<Agent | null>(null);
  const deactivate = useDeactivateAgent();
  // isPending lags a click by a render, so a quick second click would send a second POST.
  const inflight = useRef(false);

  // Names come from users: plain text only (SEC-27).
  const columns: Column<Agent>[] = [
    {
      header: "Name",
      className: "whitespace-normal [overflow-wrap:anywhere]",
      cell: (a) => a.name,
    },
    {
      header: "Key prefix",
      cell: (a) => <code className="font-mono text-xs">{a.key_prefix}…</code>,
    },
    {
      header: "Status",
      cell: (a) => {
        const tone = a.is_active ? "success" : "muted";
        return (
          <Badge variant="outline" data-tone={tone} className={TONE_CLASSES[tone]}>
            {a.is_active ? "Active" : "Inactive"}
          </Badge>
        );
      },
    },
    { header: "Created", cell: (a) => <TimeAgo iso={a.created_at} /> },
  ];
  if (showOwner) {
    columns.splice(1, 0, {
      header: "Owner",
      className: "whitespace-normal [overflow-wrap:anywhere]",
      cell: (a) => a.owner.display_name,
    });
  }
  columns.push({
    header: "",
    cell: (a) =>
      a.is_active ? (
        <Button variant="outline" size="sm" onClick={() => setTarget(a)}>
          Deactivate
        </Button>
      ) : null,
  });

  function confirm(agent: Agent) {
    if (inflight.current) return;
    inflight.current = true;
    deactivate.mutate(agent, {
      onSettled: () => {
        inflight.current = false;
        setTarget(null);
      },
    });
  }

  return (
    <div className="space-y-3">
      <DataTable
        label="agents"
        columns={columns}
        rows={rows}
        rowKey={(a) => a.id}
        isLoading={isLoading}
        error={error}
        onRetry={onRetry}
        empty={{ title: "No agents yet", description: "Create one below to connect Claude Code." }}
      />
      {total !== undefined && total > AGENTS_LIMIT ? (
        <p className="text-sm text-muted-foreground">
          Showing the first {formatNumber(AGENTS_LIMIT)} of {formatNumber(total)} agents.
        </p>
      ) : null}
      <DeactivateDialog
        agent={target}
        pending={deactivate.isPending}
        onConfirm={confirm}
        onClose={() => setTarget(null)}
      />
    </div>
  );
}
