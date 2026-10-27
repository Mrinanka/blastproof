import { isServer, QueryClient } from "@tanstack/react-query";

// Both match TanStack's defaults; set explicitly so a later edit cannot change them silently (FE-03).
export const QUERY_DEFAULTS = {
  refetchOnWindowFocus: true,
  refetchIntervalInBackground: false,
} as const;

export function makeQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { ...QUERY_DEFAULTS } } });
}

let browserClient: QueryClient | undefined;

// Server: a new client per call, so one user's cache never reaches another request.
// Browser: one shared client.
export function getQueryClient(): QueryClient {
  if (isServer) return makeQueryClient();
  browserClient ??= makeQueryClient();
  return browserClient;
}
