"use client";

import { ErrorState, LoadingState } from "@/components/common/states";
import { Rescan } from "@/components/knowledge/rescan";
import { SourceTable } from "@/components/knowledge/source-table";
import { UploadForm } from "@/components/knowledge/upload-form";
import { useKnowledge } from "@/hooks/use-knowledge";
import { useMe } from "@/hooks/use-me";
import { knownTables } from "@/lib/knowledge";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-base font-semibold">{title}</h2>
      {children}
    </section>
  );
}

export function Knowledge() {
  const me = useMe();
  const isAdmin = me.data?.role === "admin";
  // Any signed-in user may read the list (BE-114); only admins get the write controls. Nothing is
  // fetched until the role is known, so a failed /me shows no data.
  const sources = useKnowledge(me.isSuccess);

  if (me.isPending) return <LoadingState label="Loading" />;
  if (me.isError) return <ErrorState error={me.error} onRetry={() => void me.refetch()} />;
  const items = sources.data?.items ?? [];
  return (
    <div className="space-y-8">
      <Section title="Sources">
        <SourceTable
          rows={sources.data?.items}
          total={sources.data?.total}
          isLoading={sources.isPending}
          error={sources.error}
          onRetry={() => void sources.refetch()}
          canDelete={isAdmin}
        />
      </Section>
      {isAdmin ? (
        <>
          <Section title="Upload a document">
            <UploadForm sources={items} tables={knownTables(items)} />
          </Section>
          <Section title="Schema">
            <Rescan />
          </Section>
        </>
      ) : null}
    </div>
  );
}
