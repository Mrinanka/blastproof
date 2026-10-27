import type { ReactNode } from "react";

import { EmptyState, ErrorState, LoadingState, errorText } from "@/components/common/states";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatNumber } from "@/lib/format";

export interface Column<T> {
  header: string;
  cell: (row: T) => ReactNode;
  className?: string;
}

interface Page {
  total: number;
  limit: number;
  offset: number;
}

export function pageInfo({ total, limit, offset }: Page) {
  const size = Math.max(limit, 1);
  const pastEnd = total === 0 || offset >= total;
  return {
    from: pastEnd ? 0 : offset + 1,
    to: pastEnd ? 0 : Math.min(offset + size, total),
    hasPrev: offset > 0,
    hasNext: offset + size < total,
    prevOffset: Math.max(offset - size, 0),
    nextOffset: offset + size,
  };
}

interface DataTableProps<T> {
  label: string;
  columns: Column<T>[];
  rows: T[] | undefined;
  rowKey: (row: T) => string;
  page?: Page;
  onPageChange?: (offset: number) => void;
  isLoading: boolean;
  error?: unknown;
  onRetry?: () => void;
  empty: { title: string; description?: string };
}

export function DataTable<T>({
  label,
  columns,
  rows,
  rowKey,
  page,
  onPageChange,
  isLoading,
  error,
  onRetry,
  empty,
}: DataTableProps<T>) {
  if (isLoading) return <LoadingState lines={5} label={`Loading ${label}`} />;
  const hasError = error !== undefined && error !== null;
  const shown = rows ?? [];
  if (hasError && shown.length === 0) return <ErrorState error={error} onRetry={onRetry} />;

  const info = page ? pageInfo(page) : null;
  const pastEnd = page !== undefined && page.total > 0 && page.offset >= page.total;
  if (shown.length === 0 && !pastEnd) return <EmptyState {...empty} />;

  return (
    <div className="space-y-3">
      {hasError ? (
        <Alert variant="destructive">
          <AlertDescription>Couldn&apos;t refresh. {errorText(error)}</AlertDescription>
        </Alert>
      ) : null}
      {shown.length > 0 ? (
        <Table aria-label={label}>
          <TableHeader>
            <TableRow>
              {columns.map((c) => (
                <TableHead key={c.header} className={c.className}>
                  {c.header}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.map((row) => (
              <TableRow key={rowKey(row)}>
                {columns.map((c) => (
                  <TableCell key={c.header} className={c.className}>
                    {c.cell(row)}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <p className="text-sm text-muted-foreground">No rows on this page</p>
      )}
      {page && info ? (
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">
            Showing {formatNumber(info.from)}–{formatNumber(info.to)} of {formatNumber(page.total)}
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={!info.hasPrev}
              onClick={() => onPageChange?.(info.prevOffset)}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!info.hasNext}
              onClick={() => onPageChange?.(info.nextOffset)}
            >
              Next
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
