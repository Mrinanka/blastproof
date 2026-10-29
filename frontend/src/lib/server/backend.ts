import "server-only";

import { NextResponse, type NextRequest } from "next/server";

export const BACKEND_TIMEOUT_MS = 30_000;
// An upload embeds up to 1 MB of text and a rescan reads the whole schema; both are slow (F10).
export const LONG_BACKEND_TIMEOUT_MS = 120_000;
// Above the backend's 1 MB + 64 KB upload limit, so the backend's own 413 and 422 still decide.
export const MAX_BODY_BYTES = 2 * 1024 * 1024;

const LONG_PATHS = [/^\/v1\/knowledge$/, /^\/v1\/systems\/[^/]+\/scan$/];

// `target` is the backend path, query allowed.
export function timeoutFor(method: string, target: string): number {
  const path = target.split("?")[0];
  return method === "POST" && LONG_PATHS.some((re) => re.test(path))
    ? LONG_BACKEND_TIMEOUT_MS
    : BACKEND_TIMEOUT_MS;
}

// Thrown when a required environment variable is missing or malformed. The message names the
// variable, never its value.
export class ConfigError extends Error {}

// The only reader of BLASTPROOF_API_URL in the dashboard (CONV-14).
export function backendUrl(path: string): string {
  const base = process.env.BLASTPROOF_API_URL;
  if (!base) throw new ConfigError("BLASTPROOF_API_URL is not set");
  return `${base.replace(/\/+$/, "")}${path}`;
}

export function errorResponse(status: number, code: string, message: string): NextResponse {
  return NextResponse.json(
    { error: { code, message } },
    { status, headers: { "cache-control": "no-store" } },
  );
}

// A state-changing request must come from the dashboard's own origin (SEC-25). Fails closed on a
// missing or "null" Origin.
export function sameOrigin(request: NextRequest): boolean {
  return request.headers.get("origin") === request.nextUrl.origin;
}

export type BackendResult = { response: Response } | { failure: NextResponse };

// Calls the backend with a timeout. Never throws: a failure comes back as the envelope to send on.
// Messages never hold the backend address or the token.
export async function fetchBackend(path: string, init: RequestInit): Promise<BackendResult> {
  let url: string;
  try {
    url = backendUrl(path);
  } catch (error) {
    if (error instanceof ConfigError) {
      return { failure: errorResponse(500, "config_error", "The dashboard is not configured.") };
    }
    throw error;
  }
  try {
    const response = await fetch(url, {
      ...init,
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutFor(init.method ?? "GET", path)),
    });
    return { response };
  } catch (error) {
    if (error instanceof Error && error.name === "TimeoutError") {
      return { failure: errorResponse(504, "backend_timeout", "The backend took too long.") };
    }
    return { failure: errorResponse(502, "backend_unavailable", "The backend is unreachable.") };
  }
}

const RELAYED_HEADERS = ["content-type", "retry-after", "x-request-id"];
const NO_BODY_STATUSES = new Set([204, 205, 304]);

// The backend's status and body bytes unchanged; only an allowlist of headers is copied, so a
// backend Set-Cookie never reaches the browser.
export async function relay(backend: Response): Promise<NextResponse> {
  const headers = new Headers({ "cache-control": "no-store" });
  for (const name of RELAYED_HEADERS) {
    const value = backend.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  const body = NO_BODY_STATUSES.has(backend.status) ? null : await backend.arrayBuffer();
  return new NextResponse(body, { status: backend.status, headers });
}
