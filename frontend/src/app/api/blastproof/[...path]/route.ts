import type { NextRequest } from "next/server";

import {
  MAX_BODY_BYTES,
  errorResponse,
  fetchBackend,
  relay,
  sameOrigin,
} from "@/lib/server/backend";
import { clearSessionCookie, readSessionToken } from "@/lib/server/session";

type Context = { params: Promise<{ path: string[] }> };

const FORWARDED_REQUEST_HEADERS = ["content-type", "accept"];

// The body, or undefined when it is over MAX_BODY_BYTES. Counts while reading, so a body with no
// (or a false) Content-Length is stopped too.
async function readCapped(request: NextRequest): Promise<ArrayBuffer | undefined> {
  const length = Number(request.headers.get("content-length"));
  if (Number.isFinite(length) && length > MAX_BODY_BYTES) return undefined;
  if (!request.body) return new ArrayBuffer(0);
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_BODY_BYTES) {
      await reader.cancel();
      return undefined;
    }
    chunks.push(value);
  }
  const out = new Uint8Array(size);
  let at = 0;
  for (const chunk of chunks) {
    out.set(chunk, at);
    at += chunk.byteLength;
  }
  return out.buffer;
}

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

  let body: ArrayBuffer | undefined;
  if (writes) {
    body = await readCapped(request);
    if (!body) return errorResponse(413, "too_large", "The request is too large.");
  }
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
