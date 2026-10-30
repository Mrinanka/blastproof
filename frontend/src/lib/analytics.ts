// Vercel Web Analytics and Speed Insights (D-304). Pure helpers, so they are tested without the
// packages; the components live in components/analytics/vercel-analytics.tsx.

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Analytics runs only on the production deployment: in development the packages load a debug
// script from another origin that the CSP blocks, and previews would use up the Hobby quota.
export function collectsAnalytics(vercelEnv: string | undefined): boolean {
  return vercelEnv === "production";
}

// Action ids, History filters and the login `next` target never reach Vercel: the query string
// and hash are dropped and every UUID path segment becomes `[id]`. A URL we cannot parse is
// dropped rather than sent as is (fail closed).
export function redactUrl(url: string): string | null {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  const path = parsed.pathname
    .split("/")
    .map((segment) => (UUID.test(segment) ? "[id]" : segment))
    .join("/");
  return parsed.origin + path;
}

export function redactEvent<T extends { url: string }>(event: T): T | null {
  const url = redactUrl(event.url);
  return url === null ? null : { ...event, url };
}
