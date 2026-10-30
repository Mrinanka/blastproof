"use client";

import { useState } from "react";

import { AgentTable } from "@/components/agents/agent-table";
import { CreateAgentForm } from "@/components/agents/create-agent-form";
import { KeyDialog } from "@/components/agents/key-dialog";
import { ErrorState, LoadingState } from "@/components/common/states";
import { useAgents } from "@/hooks/use-agents";
import { useMe } from "@/hooks/use-me";
import type { CreatedAgent } from "@/lib/agents";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-base font-semibold">{title}</h2>
      {children}
    </section>
  );
}

export function Agents() {
  const me = useMe();
  // Nothing is fetched until the role is known, so a failed /me shows no data.
  const agents = useAgents(me.isSuccess);
  // The created agent (and its key) lives only here, until Done drops it.
  const [created, setCreated] = useState<CreatedAgent | null>(null);

  if (me.isPending) return <LoadingState label="Loading" />;
  if (me.isError) return <ErrorState error={me.error} onRetry={() => void me.refetch()} />;
  return (
    <div className="space-y-8">
      <Section title="Agents">
        <AgentTable
          rows={agents.data?.items}
          total={agents.data?.total}
          isLoading={agents.isPending}
          error={agents.error}
          onRetry={() => void agents.refetch()}
          showOwner={me.data.role === "admin"}
        />
      </Section>
      <Section title="Create an agent">
        <CreateAgentForm onCreated={setCreated} />
      </Section>
      <KeyDialog agent={created} onDone={() => setCreated(null)} />
    </div>
  );
}
