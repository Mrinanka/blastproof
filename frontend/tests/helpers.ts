import { NextRequest } from "next/server";
import { vi } from "vitest";

export const ORIGIN = "http://dashboard.test";
export const BACKEND = "http://backend.test:8000";

export function setEnv(extra: Record<string, string | undefined> = {}) {
  const env: Record<string, string | undefined> = {
    BLASTPROOF_API_URL: BACKEND,
    SESSION_COOKIE_NAME: "bp_session",
    SESSION_MAX_AGE_SECONDS: "28800",
    ...extra,
  };
  for (const [key, value] of Object.entries(env)) {
    vi.stubEnv(key, value as string);
  }
}

export function makeRequest(
  path: string,
  init: {
    method?: string;
    headers?: Record<string, string>;
    body?: BodyInit;
    cookie?: string;
  } = {},
) {
  const headers = new Headers(init.headers);
  if (init.cookie) headers.set("cookie", `bp_session=${init.cookie}`);
  return new NextRequest(`${ORIGIN}${path}`, {
    method: init.method ?? "GET",
    headers,
    body: init.body,
  });
}

export function stubFetch(response: () => Response | Promise<Response>) {
  const spy = vi.fn(async () => response());
  vi.stubGlobal("fetch", spy);
  return spy;
}

export function lastCall(spy: ReturnType<typeof stubFetch>) {
  const [url, init] = spy.mock.calls[0] as unknown as [string, RequestInit];
  return { url, init, headers: new Headers(init.headers) };
}
