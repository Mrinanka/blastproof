"use client";

import { useRef, useState } from "react";

import { DataTable, type Column } from "@/components/common/data-table";
import { TimeAgo } from "@/components/common/time-ago";
import { DeleteDialog } from "@/components/knowledge/delete-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { KNOWLEDGE_LIMIT, useDeleteKnowledge } from "@/hooks/use-knowledge";
import { formatNumber } from "@/lib/format";
import { kindLabel, type KnowledgeSource } from "@/lib/knowledge";

interface Props {
  rows: KnowledgeSource[] | undefined;
  total: number | undefined;
  isLoading: boolean;
  error: unknown;
  onRetry: () => void;
  // Admins only: a member reads the list and nothing else.
  canDelete: boolean;
}

export function SourceTable({ rows, total, isLoading, error, onRetry, canDelete }: Props) {
  const [target, setTarget] = useState<KnowledgeSource | null>(null);
  const remove = useDeleteKnowledge();
  // isPending lags a click by a render, so a quick second click would send a second DELETE.
  const inflight = useRef(false);

  const columns: Column<KnowledgeSource>[] = [
    // Plain text: a source name comes from an upload (SEC-27). Wide enough that a usual name stays
    // whole; only a very long one breaks (break-all split "schema_docs.md" mid-word).
    {
      header: "Source",
      className: "min-w-40 whitespace-normal [overflow-wrap:anywhere]",
      cell: (s) => s.source,
    },
    { header: "Kind", cell: (s) => <Badge variant="secondary">{kindLabel(s.kind)}</Badge> },
    { header: "Chunks", cell: (s) => formatNumber(s.chunks) },
    {
      header: "Tables",
      className: "min-w-40 whitespace-normal",
      cell: (s) => (s.table_names.length > 0 ? s.table_names.join(", ") : "—"),
    },
    { header: "Updated", cell: (s) => <TimeAgo iso={s.updated_at} /> },
  ];
  if (canDelete) {
    columns.push({
      header: "",
      cell: (s) =>
        s.kind === "schema_scan" ? (
          <span className="text-xs text-muted-foreground">Rescan to replace</span>
        ) : (
          <Button variant="outline" size="sm" onClick={() => setTarget(s)}>
            Delete
          </Button>
        ),
    });
  }

  return (
    <div className="space-y-3">
      <DataTable
        label="knowledge sources"
        columns={columns}
        rows={rows}
        rowKey={(s) => s.source}
        isLoading={isLoading}
        error={error}
        onRetry={onRetry}
        empty={{
          title: "No sources yet",
          description: canDelete ? "Rescan the schema or upload a document." : undefined,
        }}
      />
      {total !== undefined && total > KNOWLEDGE_LIMIT ? (
        <p className="text-sm text-muted-foreground">
          Showing the first {KNOWLEDGE_LIMIT} of {formatNumber(total)} sources.
        </p>
      ) : null}
      <DeleteDialog
        source={target}
        pending={remove.isPending}
        onClose={() => setTarget(null)}
        onConfirm={(source) => {
          if (inflight.current) return;
          inflight.current = true;
          remove.mutate(source, {
            onSettled: () => {
              inflight.current = false;
              setTarget(null);
            },
          });
        }}
      />
    </div>
  );
}
