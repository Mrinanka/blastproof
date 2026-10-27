import { AlertCircle } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/errors";

export function LoadingState({ lines = 3, label = "Loading" }: { lines?: number; label?: string }) {
  return (
    <div aria-busy="true" aria-label={label} role="status" className="space-y-2">
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} data-testid="skeleton-line" className="h-5 w-full" />
      ))}
    </div>
  );
}

export function EmptyState({ title, description }: { title: string; description?: string }) {
  return (
    <Empty>
      <EmptyHeader>
        <EmptyTitle>{title}</EmptyTitle>
        {description ? <EmptyDescription>{description}</EmptyDescription> : null}
      </EmptyHeader>
    </Empty>
  );
}

// Only an ApiError's message (the backend's envelope text) is shown; anything else could carry
// internals, so it gets the generic text.
export function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong.";
}

export function ErrorState({
  error,
  title = "Something went wrong",
  onRetry,
}: {
  error: unknown;
  title?: string;
  onRetry?: () => void;
}) {
  return (
    <Alert variant="destructive">
      <AlertCircle />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>
        <p>{errorText(error)}</p>
        {onRetry ? (
          <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
            Retry
          </Button>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}
