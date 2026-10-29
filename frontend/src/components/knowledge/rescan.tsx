"use client";

import { ErrorState, LoadingState } from "@/components/common/states";
import { Button } from "@/components/ui/button";
import { useRescan, useSystems } from "@/hooks/use-systems";

export function Rescan() {
  const systems = useSystems(true);
  const rescan = useRescan();

  if (systems.isPending) return <LoadingState lines={1} label="Loading systems" />;
  if (systems.isError) {
    return <ErrorState error={systems.error} onRetry={() => void systems.refetch()} />;
  }
  if (systems.data.items.length === 0) {
    return <p className="text-sm text-muted-foreground">No systems are connected.</p>;
  }
  const busyId = rescan.isPending ? rescan.variables : undefined;
  return (
    <ul className="space-y-2">
      {systems.data.items.map((system) => (
        <li key={system.id} className="flex flex-wrap items-center gap-3">
          <span className="text-sm">
            {system.name}{" "}
            <span className="text-muted-foreground">({system.connection_profile})</span>
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={rescan.isPending}
            onClick={() => rescan.mutate(system.id)}
          >
            {busyId === system.id ? "Rescanning…" : "Rescan schema"}
          </Button>
        </li>
      ))}
    </ul>
  );
}
