"use client";

import { LatestActions } from "@/components/overview/latest-actions";
import { StatCards } from "@/components/overview/stat-cards";
import { usePendingCount } from "@/hooks/use-pending-count";
import { useStats } from "@/hooks/use-stats";

export function Overview() {
  const stats = useStats();
  // Shared with the sidebar badge (same query key), so the card and the badge always agree.
  const pending = usePendingCount();
  return (
    <div className="space-y-6">
      <StatCards
        stats={stats.data}
        pending={pending.data}
        isLoading={stats.isPending}
        error={stats.error}
        onRetry={() => void stats.refetch()}
      />
      <LatestActions />
    </div>
  );
}
