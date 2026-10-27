"use client";

import { useMe } from "@/hooks/use-me";

// Placeholder until F9 builds the real page.
export default function DashboardPage() {
  const { data, isPending } = useMe();
  return (
    <main className="p-6">
      {isPending ? "Loading…" : data ? `Signed in as ${data.display_name} (${data.role})` : null}
    </main>
  );
}
