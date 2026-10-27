import type { NextRequest } from "next/server";

import { errorResponse, fetchBackend, relay, sameOrigin } from "@/lib/server/backend";
import { clearSessionCookie, readSessionToken } from "@/lib/server/session";

type Context = { params: Promise<{ path: string[] }> };

const FORWARDED_REQUEST_HEADERS = ["content-type", "accept"];

// The backend proxy (FE-06): adds the Bearer token from the httpOnly cookie and returns the
// backend's answer unchanged. Only /v1/* is reachable through it.
async function handle(request: NextRequest, { params }: Context) {
  const { path } = await params;
  if (path[0] !== "v1" || path.some((s) => s === "" || s === "." || s === "..")) {
    return errorResponse(404, "not_found", "Not found.");
  }
  const writes = request.method !== "GET";
  if (writes && !sameOrigin(request)) {
    return errorResponse(403, "bad_origin", "Cross-origin requests are not allowed.");
  }

  // An allowlist, so the browser's own Cookie, Authorization, Host and X-Forwarded-* never pass.
  const headers = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  let token: string | undefined;
  try {
    token = readSessionToken(request);
  } catch {
    return errorResponse(500, "config_error", "The dashboard is not configured.");
  }
  if (token) headers.set("authorization", `Bearer ${token}`);

  const body = writes ? await request.arrayBuffer() : undefined;
  const target = `/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  const result = await fetchBackend(target, {
    method: request.method,
    headers,
    body: body && body.byteLength > 0 ? body : undefined,
  });
  if ("failure" in result) return result.failure;

  const response = await relay(result.response);
  // An expired token would otherwise keep the dashboard open on a dead session.
  if (result.response.status === 401) clearSessionCookie(response);
  return response;
}

export { handle as GET, handle as POST, handle as DELETE };
