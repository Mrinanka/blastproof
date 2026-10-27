import { formatNumber } from "@/lib/format";

export interface BreakdownItem {
  label: string;
  value: number;
  hint?: string;
}

// Largest first, ties by label.
export function countsToItems(counts: Record<string, number>): BreakdownItem[] {
  return Object.entries(counts)
    .map(([label, value]) => ({ label, value }))
    .sort((a, b) => b.value - a.value || a.label.localeCompare(b.label));
}

export function NumberBreakdown({ items }: { items: BreakdownItem[] }) {
  if (items.length === 0) return <p className="text-sm text-muted-foreground">No rows</p>;
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
      {items.map((item) => (
        <div key={item.label} className="contents">
          <dt className="text-muted-foreground">{item.label}</dt>
          <dd className="font-medium tabular-nums">
            {formatNumber(item.value)}
            {item.hint ? (
              <span className="ml-2 font-normal text-muted-foreground">{item.hint}</span>
            ) : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}
