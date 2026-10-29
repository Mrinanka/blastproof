"use client";

import { ErrorState, LoadingState } from "@/components/common/states";
import { CreateUserForm } from "@/components/users/create-user-form";
import { UserTable } from "@/components/users/user-table";
import { useMe } from "@/hooks/use-me";
import { useUsers } from "@/hooks/use-users";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-base font-semibold">{title}</h2>
      {children}
    </section>
  );
}

export function Users() {
  const me = useMe();
  const isAdmin = me.data?.role === "admin";
  // A member sends no /v1/users request; the backend would answer 403 anyway.
  const users = useUsers(isAdmin);

  if (me.isPending) return <LoadingState label="Loading" />;
  if (me.isError) return <ErrorState error={me.error} onRetry={() => void me.refetch()} />;
  if (!isAdmin) {
    return <p className="text-sm text-muted-foreground">Users is for admins.</p>;
  }
  return (
    <div className="space-y-8">
      <Section title="Users">
        <UserTable
          rows={users.data?.items}
          total={users.data?.total}
          isLoading={users.isPending}
          error={users.error}
          onRetry={() => void users.refetch()}
        />
      </Section>
      <Section title="Create a user">
        <CreateUserForm />
      </Section>
    </div>
  );
}
