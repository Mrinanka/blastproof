// The only place that formats numbers, money, durations and times (CONV-17). Locale is fixed to
// en-US so numbers match the backend's headline text; the time zone is the browser's.
const LOCALE = "en-US";
const DASH = "—";

export function formatNumber(n: number | null): string {
  if (n === null || Number.isNaN(n)) return DASH;
  return new Intl.NumberFormat(LOCALE).format(n);
}

// null is an unknown cost (D-153), which is not the same as $0.
export function formatUsd(n: number | null): string {
  if (n === null || Number.isNaN(n)) return "Unknown";
  const small = n !== 0 && Math.abs(n) < 0.01;
  return new Intl.NumberFormat(LOCALE, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: small ? 4 : 2,
    maximumFractionDigits: small ? 4 : 2,
  }).format(n);
}

export function formatDuration(ms: number | null): string {
  if (ms === null || Number.isNaN(ms)) return DASH;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) {
    const seconds = Math.round(ms / 100) / 10;
    // 59 960 ms rounds to 60.0: show it as a minute instead.
    if (seconds < 60) return `${seconds} s`;
  }
  const total = Math.round(ms / 1000);
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return rest === 0 ? `${minutes} min` : `${minutes} min ${rest} s`;
}

export function formatConfidence(c: number | null): string {
  if (c === null || Number.isNaN(c)) return DASH;
  return `${Math.round(c * 100)}%`;
}

// A string without Z or an offset would be read as local time, so it counts as invalid.
const HAS_ZONE = /(Z|[+-]\d{2}(:?\d{2})?)$/i;

export function parseTimestamp(iso: string): Date | null {
  if (typeof iso !== "string" || !HAS_ZONE.test(iso.trim())) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(
  iso: string,
  options: { timeZone?: string; seconds?: boolean } = {},
): string {
  const date = parseTimestamp(iso);
  if (!date) return DASH;
  return new Intl.DateTimeFormat(LOCALE, {
    dateStyle: "medium",
    timeStyle: options.seconds ? "long" : "short",
    timeZone: options.timeZone,
  })
    .format(date)
    .replace(/ /g, " ");
}

const RELATIVE = new Intl.RelativeTimeFormat(LOCALE, { numeric: "auto" });

export function formatRelative(iso: string, now: Date): string {
  const date = parseTimestamp(iso);
  if (!date) return DASH;
  const diffSeconds = Math.round((date.getTime() - now.getTime()) / 1000);
  const abs = Math.abs(diffSeconds);
  if (abs < 45) return "just now";
  const sign = diffSeconds < 0 ? -1 : 1;
  if (abs < 45 * 60) return RELATIVE.format(sign * Math.max(1, Math.round(abs / 60)), "minute");
  if (abs < 22 * 3600) return RELATIVE.format(sign * Math.max(1, Math.round(abs / 3600)), "hour");
  const days = Math.round(abs / 86_400);
  if (days < 30) return RELATIVE.format(sign * Math.max(1, days), "day");
  const months = Math.round(abs / (30 * 86_400));
  if (months < 12) return RELATIVE.format(sign * Math.max(1, months), "month");
  return RELATIVE.format(sign * Math.max(1, Math.round(abs / (365 * 86_400))), "year");
}
