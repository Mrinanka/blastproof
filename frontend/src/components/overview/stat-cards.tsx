import Link from "next/link";

import { ErrorState, errorText } from "@/components/common/states";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { Schemas } from "@/lib/api/client";
import { formatDay } from "@/lib/format";
import { statCards } from "@/lib/overview";

interface Props {
  stats: Schemas["StatsResponse"] | undefined;
  pending: number | undefined;
  isLoading: boolean;
  error: unknown;
  onRetry: () => void;
}

const CARD_COUNT = 5;
const GRID = "grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5";

export function StatCards({ stats, pending, isLoading, error, onRetry }: Props) {
  if (isLoading) {
    return (
      <div role="status" aria-busy="true" aria-label="Loading stats" className={GRID}>
        {Array.from({ length: CARD_COUNT }, (_, i) => (
          <Skeleton key={i} data-testid="skeleton-card" className="h-28 w-full" />
        ))}
      </div>
    );
  }
  if (!stats) return <ErrorState error={error} onRetry={onRetry} />;

  return (
    <div className="space-y-3">
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>Couldn&apos;t refresh. {errorText(error)}</AlertDescription>
        </Alert>
      ) : null}
      <p className="text-sm text-muted-foreground">Today (UTC): {formatDay(stats.today.day)}</p>
      <div className={GRID}>
        {statCards(stats, pending).map((card) => (
          <Card key={card.id} size="sm">
            <CardHeader>
              <CardDescription>
                {card.href ? (
                  <Link href={card.href} className="hover:underline">
                    {card.label}
                  </Link>
                ) : (
                  card.label
                )}
              </CardDescription>
              <CardTitle className="text-2xl">{card.value}</CardTitle>
            </CardHeader>
            <CardContent className="text-xs text-muted-foreground">{card.hint}</CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
