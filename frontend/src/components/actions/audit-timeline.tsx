import { STATUS_META } from "@/components/actions/status-badge";
import { EmptyState } from "@/components/common/states";
import { LocalTime } from "@/components/common/time-ago";
import type { components } from "@/lib/api/schema";

type AuditEvent = components["schemas"]["AuditEventView"];

export const EVENT_LABELS: Record<string, string> = {
  received: "Received",
  classified: "Classified",
  shadow_done: "Preview done",
  fail_closed: "Failed closed",
  decided: "Decided",
};

// Status-named events (awaiting_review, committed) reuse the status labels.
function eventLabel(type: string): string {
  const status = (STATUS_META as Record<string, { label: string } | undefined>)[type];
  return EVENT_LABELS[type] ?? status?.label ?? type;
}

// Backend order is kept. Values are shown as text, whatever they contain.
export function AuditTimeline({ events }: { events: AuditEvent[] }) {
  if (events.length === 0) return <EmptyState title="No events yet" />;
  return (
    <ol className="space-y-3">
      {events.map((event, i) => (
        <li key={`${event.created_at}-${i}`} className="text-sm">
          <div className="flex items-baseline justify-between gap-4">
            <span className="font-medium">{eventLabel(event.event_type)}</span>
            <span className="text-xs text-muted-foreground">
              <LocalTime iso={event.created_at} seconds />
            </span>
          </div>
          {Object.keys(event.data).length > 0 ? (
            <ul className="mt-1 text-xs text-muted-foreground">
              {Object.entries(event.data).map(([key, value]) => (
                <li key={key}>
                  {key}: {String(value)}
                </li>
              ))}
            </ul>
          ) : null}
        </li>
      ))}
    </ol>
  );
}
