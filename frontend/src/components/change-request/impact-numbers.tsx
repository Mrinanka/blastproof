import { countsToItems, NumberBreakdown } from "@/components/actions/number-breakdown";
import { SECTION_EMPTY } from "@/components/change-request/sections";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { ActionDetail } from "@/hooks/use-action";
import { formatNumber } from "@/lib/format";

// Every number comes from the backend's computed facts; this only lays them out.
export function ImpactNumbers({ action }: { action: ActionDetail }) {
  const { table_changes, breakdowns, derived_groups } = action;
  if (table_changes.length === 0 && breakdowns.length === 0 && derived_groups.length === 0) {
    return <p className="text-sm text-muted-foreground">{SECTION_EMPTY.numbers}</p>;
  }
  return (
    <div className="space-y-4">
      {table_changes.length > 0 ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Table</TableHead>
              <TableHead className="text-right">Deleted</TableHead>
              <TableHead className="text-right">Updated</TableHead>
              <TableHead className="text-right">Inserted</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {table_changes.map((change) => (
              <TableRow key={change.table}>
                <TableCell className="font-medium">
                  {change.table}
                  {change.is_cascade ? (
                    <Badge variant="outline" className="ml-2">
                      cascade
                    </Badge>
                  ) : null}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatNumber(change.deleted)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatNumber(change.updated)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatNumber(change.inserted)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : null}
      {breakdowns.map((b) => (
        <div key={`${b.table}.${b.column}`}>
          <h3 className="mb-1 text-sm font-medium">
            {b.table}.{b.column}
          </h3>
          <NumberBreakdown items={countsToItems(b.counts)} />
        </div>
      ))}
      {derived_groups.length > 0 ? (
        <div>
          <h3 className="mb-1 text-sm font-medium">Groups</h3>
          <NumberBreakdown
            items={derived_groups.map((g) => ({
              label: `${g.table}: ${g.name}`,
              value: g.count,
              hint: g.definition ?? undefined,
            }))}
          />
        </div>
      ) : null}
    </div>
  );
}

function cellText(value: unknown): string {
  if (value === null || value === undefined) return "—";
  return typeof value === "string" ? value : JSON.stringify(value);
}

// Sample values arrive masked from the backend; they are shown as text, whatever they contain.
export function SampleRows({ samples }: { samples: ActionDetail["samples"] }) {
  if (samples.length === 0) {
    return <p className="text-sm text-muted-foreground">{SECTION_EMPTY.samples}</p>;
  }
  const groups = new Map<string, ActionDetail["samples"]>();
  for (const sample of samples) {
    const key = `${sample.table} · ${sample.change}`;
    groups.set(key, [...(groups.get(key) ?? []), sample]);
  }
  return (
    <div className="space-y-4">
      {[...groups].map(([title, rows]) => {
        const columns = [...new Set(rows.flatMap((r) => Object.keys(r.values)))];
        return (
          <div key={title}>
            <h3 className="mb-1 text-sm font-medium">{title}</h3>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Side</TableHead>
                  {columns.map((c) => (
                    <TableHead key={c}>{c}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row, i) => (
                  <TableRow key={i}>
                    <TableCell className="text-muted-foreground">{row.side}</TableCell>
                    {columns.map((c) => (
                      <TableCell key={c} className="font-mono text-xs">
                        {cellText(row.values[c])}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        );
      })}
    </div>
  );
}
