import Link from "next/link";
import type { ReactNode } from "react";

import { RiskBadge } from "@/components/actions/risk-badge";
import { SQLBlock } from "@/components/actions/sql-block";
import { StatusBadge } from "@/components/actions/status-badge";
import { TimeAgo } from "@/components/common/time-ago";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ActionDetail } from "@/hooks/use-action";
import { formatConfidence } from "@/lib/format";

export const SECTION_EMPTY = {
  summary: "Summary not ready",
  numbers: "No preview",
  samples: "No sample rows",
  judgeNone: "The AI check did not run",
  rules: "No rules matched",
  knowledge: "No knowledge used",
  reason: "Reason withheld",
} as const;

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>{title}</h2>
        </CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

export function RequestHeader({ action }: { action: ActionDetail }) {
  return (
    <header className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={action.status} />
        <RiskBadge level={action.risk_level} />
      </div>
      <p className="text-sm text-muted-foreground">
        {action.agent.name} for {action.owner.display_name} · <TimeAgo iso={action.created_at} />
        {action.decided_by ? <> · Decided by {action.decided_by.display_name}</> : null}
      </p>
      {action.reviewer_note ? (
        <p className="text-sm">
          <span className="text-muted-foreground">Reviewer note: </span>
          {action.reviewer_note}
        </p>
      ) : null}
    </header>
  );
}

export function ImpactSummary({ report }: { report: ActionDetail["impact_report"] }) {
  if (!report) return <p className="text-lg text-muted-foreground">{SECTION_EMPTY.summary}</p>;
  return (
    <div className="space-y-3">
      <p className="text-2xl font-semibold">{report.headline}</p>
      {report.key_points.length > 0 ? (
        <ul className="list-disc space-y-1 pl-5 text-sm">
          {report.key_points.map((point, i) => (
            <li key={i}>{point}</li>
          ))}
        </ul>
      ) : null}
      {report.concerns.length > 0 ? (
        <div>
          <h3 className="text-sm font-medium">Concerns</h3>
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {report.concerns.map((concern, i) => (
              <li key={i}>{concern}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function ActionLink({ id, label }: { id: string; label: string }) {
  return (
    <Link href={`/dashboard/actions/${id}`} className="underline">
      {label}
    </Link>
  );
}

export function IntentAndSql({ action }: { action: ActionDetail }) {
  const pinned = Object.entries(action.pinned_values);
  return (
    <div className="space-y-3">
      <p className="text-sm">
        <span className="text-muted-foreground">Intent: </span>
        {action.intent ?? "—"}
      </p>
      {action.original_sql_display ? (
        <div className="grid gap-3 md:grid-cols-2">
          <div>
            <h3 className="mb-1 text-sm font-medium">Original</h3>
            <SQLBlock sql={action.original_sql_display} />
          </div>
          <div>
            <h3 className="mb-1 text-sm font-medium">As it will run</h3>
            <SQLBlock sql={action.sql_display} />
          </div>
        </div>
      ) : (
        <SQLBlock sql={action.sql_display} />
      )}
      {pinned.length > 0 ? (
        <div>
          <h3 className="mb-1 text-sm font-medium">Pinned values</h3>
          <ul className="space-y-1 font-mono text-xs">
            {pinned.map(([name, value]) => (
              <li key={name}>
                {name} → {value}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {action.retry_of ? (
        <p className="text-sm">
          Retry of <ActionLink id={action.retry_of} label="the earlier request" />
        </p>
      ) : null}
      {action.retried_by ? (
        <p className="text-sm">
          Retried as <ActionLink id={action.retried_by} label="a later request" />
        </p>
      ) : null}
    </div>
  );
}

export function JudgeResult({ judge }: { judge: ActionDetail["judge"] }) {
  if (!judge) return <p className="text-sm text-muted-foreground">{SECTION_EMPTY.judgeNone}</p>;
  const ran = [judge.provider, judge.model].filter(Boolean).join(" / ");
  if (judge.failure) {
    return (
      <p className="text-sm">
        AI check unavailable ({judge.failure})
        <span className="ml-2 text-muted-foreground">{ran}</span>
      </p>
    );
  }
  return (
    <div className="space-y-1 text-sm">
      <p>
        <span className="text-muted-foreground">Recommendation: </span>
        <span className="font-medium">{judge.recommendation ?? "—"}</span>
        {" · "}
        <span className="text-muted-foreground">Confidence: </span>
        {formatConfidence(judge.confidence)}
      </p>
      <p>{judge.reason ?? SECTION_EMPTY.reason}</p>
      <p className="text-xs text-muted-foreground">{ran}</p>
    </div>
  );
}

export function MatchedRules({ rules }: { rules: ActionDetail["matched_rules"] }) {
  if (rules.length === 0) {
    return <p className="text-sm text-muted-foreground">{SECTION_EMPTY.rules}</p>;
  }
  return (
    <ul className="space-y-2 text-sm">
      {rules.map((rule, i) => (
        <li key={`${rule.name}-${i}`}>
          <span className="font-medium">{rule.name}</span>{" "}
          <Badge variant="outline">{rule.outcome}</Badge>{" "}
          <Badge variant="outline">{rule.pass}</Badge>
          <p className="text-muted-foreground">{rule.reason}</p>
        </li>
      ))}
    </ul>
  );
}

export function KnowledgeUsed({ items }: { items: ActionDetail["knowledge_used"] }) {
  if (items.length === 0) {
    return <p className="text-sm text-muted-foreground">{SECTION_EMPTY.knowledge}</p>;
  }
  return (
    <ul className="space-y-2 text-sm">
      {items.map((item) => (
        <li key={item.chunk_id}>
          <span className="font-medium">{item.source}</span>
          {item.table_names.length > 0 ? (
            <span className="text-muted-foreground"> · {item.table_names.join(", ")}</span>
          ) : null}
          <p className="text-muted-foreground">{item.first_line}</p>
        </li>
      ))}
    </ul>
  );
}
