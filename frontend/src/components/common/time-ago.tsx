"use client";

import { useNow } from "@/hooks/use-now";
import { formatDateTime, formatRelative } from "@/lib/format";

// Server render and hydration have no clock (useNow is null): they show UTC, then the client
// switches to the browser's time zone.
function absolute(iso: string, ready: boolean, seconds = false) {
  return formatDateTime(iso, { seconds, timeZone: ready ? undefined : "UTC" });
}

export function TimeAgo({ iso }: { iso: string }) {
  const now = useNow();
  return (
    <time dateTime={iso} title={absolute(iso, now !== null)}>
      {now ? formatRelative(iso, now) : absolute(iso, false)}
    </time>
  );
}

export function LocalTime({ iso, seconds = false }: { iso: string; seconds?: boolean }) {
  const now = useNow();
  return <time dateTime={iso}>{absolute(iso, now !== null, seconds)}</time>;
}
