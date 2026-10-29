"use client";

import { TONE_CLASSES } from "@/components/actions/tone";
import { DataTable, type Column } from "@/components/common/data-table";
import { TimeAgo } from "@/components/common/time-ago";
import { Badge } from "@/components/ui/badge";
import { USERS_LIMIT } from "@/hooks/use-users";
import { formatNumber } from "@/lib/format";
import type { Account } from "@/lib/users";

interface Props {
  rows: Account[] | undefined;
  total: number | undefined;
  isLoading: boolean;
  error: unknown;
  onRetry: () => void;
}

// Names and emails are user-provided: plain text only (SEC-27).
const WRAP = "whitespace-normal [overflow-wrap:anywhere]";
const columns: Column<Account>[] = [
  { header: "Name", className: WRAP, cell: (u) => u.display_name },
  { header: "Email", className: WRAP, cell: (u) => u.email },
  {
    header: "Role",
    cell: (u) => <Badge variant="secondary">{u.role === "admin" ? "Admin" : "Member"}</Badge>,
  },
  {
    header: "Active",
    cell: (u) => {
      const tone = u.is_active ? "success" : "muted";
      return (
        <Badge variant="outline" data-tone={tone} className={TONE_CLASSES[tone]}>
          {u.is_active ? "Active" : "Inactive"}
        </Badge>
      );
    },
  },
  { header: "Created", cell: (u) => <TimeAgo iso={u.created_at} /> },
];

export function UserTable({ rows, total, isLoading, error, onRetry }: Props) {
  return (
    <div className="space-y-3">
      <DataTable
        label="users"
        columns={columns}
        rows={rows}
        rowKey={(u) => u.id}
        isLoading={isLoading}
        error={error}
        onRetry={onRetry}
        empty={{ title: "No users yet" }}
      />
      {total !== undefined && total > USERS_LIMIT ? (
        <p className="text-sm text-muted-foreground">
          Showing the first {formatNumber(USERS_LIMIT)} of {formatNumber(total)} users.
        </p>
      ) : null}
    </div>
  );
}
