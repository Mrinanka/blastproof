"use client";

import Link from "next/link";

import { AuditTimeline } from "@/components/actions/audit-timeline";
import { DecisionBar } from "@/components/change-request/decision-bar";
import { ImpactNumbers, SampleRows } from "@/components/change-request/impact-numbers";
import {
  ImpactSummary,
  IntentAndSql,
  JudgeResult,
  KnowledgeUsed,
  MatchedRules,
  RequestHeader,
  Section,
} from "@/components/change-request/sections";
import { EmptyState, ErrorState, LoadingState } from "@/components/common/states";
import { useAction } from "@/hooks/use-action";
import { ApiError } from "@/lib/api/errors";

export function ChangeRequest({ id }: { id: string }) {
  const query = useAction(id);
  const action = query.data;

  if (!action) {
    if (query.error instanceof ApiError && query.error.status === 404) {
      return (
        <div className="space-y-2">
          <EmptyState title="Action not found" />
          <p className="text-center text-sm">
            <Link href="/dashboard/review" className="underline">
              Back to the review queue
            </Link>
          </p>
        </div>
      );
    }
    if (query.error) {
      return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
    }
    return <LoadingState lines={6} label="Loading change request" />;
  }

  return (
    <div className="space-y-4">
      <RequestHeader action={action} />
      <Section title="Impact">
        <ImpactSummary report={action.impact_report} />
      </Section>
      <DecisionBar action={action} />
      <Section title="Intent and SQL">
        <IntentAndSql action={action} />
      </Section>
      <Section title="Impact numbers">
        <ImpactNumbers action={action} />
      </Section>
      <Section title="Sample rows">
        <SampleRows samples={action.samples} />
      </Section>
      <Section title="AI check">
        <JudgeResult judge={action.judge} />
      </Section>
      <Section title="Rules matched">
        <MatchedRules rules={action.matched_rules} />
      </Section>
      <Section title="Knowledge used">
        <KnowledgeUsed items={action.knowledge_used} />
      </Section>
      <Section title="Timeline">
        <AuditTimeline events={action.timeline} />
      </Section>
    </div>
  );
}
