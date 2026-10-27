import { isServer, MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";

import { ApiError } from "@/lib/api/errors";

// Both match TanStack's defaults; set explicitly so a later edit cannot change them silently (FE-03).
export const QUERY_DEFAULTS = {
  refetchOnWindowFocus: true,
  refetchIntervalInBackground: false,
} as const;

const MAX_RETRIES = 3;

// A 4xx answer will not change on retry (D-223); a 5xx or a lost connection might.
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  return (error.status === 0 || error.status >= 500) && failureCount < MAX_RETRIES;
}

// A full navigation, so the cache of the old session is dropped with the page.
export function redirectToLogin(): void {
  const { pathname, search } = window.location;
  if (pathname === "/login") return;
  // A full page load to a fixed path, not a Next.js route change.
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  window.location.assign(`/login?next=${encodeURIComponent(pathname + search)}`);
}

export function makeQueryClient(onUnauthorized: () => void = redirectToLogin): QueryClient {
  const onError = (error: unknown) => {
    if (error instanceof ApiError && error.status === 401) onUnauthorized();
  };
  return new QueryClient({
    queryCache: new QueryCache({ onError }),
    mutationCache: new MutationCache({ onError }),
    defaultOptions: { queries: { ...QUERY_DEFAULTS, retry: shouldRetry } },
  });
}

let browserClient: QueryClient | undefined;

// Server: a new client per call, so one user's cache never reaches another request.
// Browser: one shared client.
export function getQueryClient(): QueryClient {
  if (isServer) return makeQueryClient();
  browserClient ??= makeQueryClient();
  return browserClient;
}
