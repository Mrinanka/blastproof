"use client";

import { TONE_CLASSES, type Tone } from "@/components/actions/tone";
import { DataTable, type Column } from "@/components/common/data-table";
import { Badge } from "@/components/ui/badge";
import { usePolicies } from "@/hooks/use-policies";
import { conditionText, passLabel, type Rule } from "@/lib/rules";

const OUTCOMES: Record<Rule["outcome"], { label: string; tone: Tone }> = {
  block: { label: "Block", tone: "danger" },
  review: { label: "Review", tone: "warning" },
  allow: { label: "Allow", tone: "neutral" },
};

// All text is plain: rule names and reasons come from a file, but still render as text (SEC-27).
const columns: Column<Rule>[] = [
  { header: "Name", className: "whitespace-normal [overflow-wrap:anywhere]", cell: (r) => r.name },
  { header: "Stage", cell: (r) => passLabel(r.pass) },
  {
    header: "Conditions",
    className: "min-w-48 whitespace-normal",
    cell: (r) => (
      <ul className="space-y-0.5 font-mono text-xs [overflow-wrap:anywhere]">
        {Object.entries(r.conditions).map(([key, value]) => (
          <li key={key}>{conditionText(key, value)}</li>
        ))}
      </ul>
    ),
  },
  {
    header: "Outcome",
    cell: (r) => {
      const meta = OUTCOMES[r.outcome];
      return (
        <Badge variant="outline" data-tone={meta.tone} className={TONE_CLASSES[meta.tone]}>
          {meta.label}
        </Badge>
      );
    },
  },
  { header: "Reason", className: "min-w-48 whitespace-normal", cell: (r) => r.reason },
];

export function RulesTable() {
  const rules = usePolicies();
  return (
    <DataTable
      label="rules"
      columns={columns}
      rows={rules.data?.items}
      rowKey={(r) => `${r.pass}:${r.name}`}
      isLoading={rules.isPending}
      error={rules.error}
      onRetry={() => void rules.refetch()}
      empty={{ title: "No rules loaded" }}
    />
  );
}
