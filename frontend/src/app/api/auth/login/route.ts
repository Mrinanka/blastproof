import { NextResponse, type NextRequest } from "next/server";

import { errorResponse, fetchBackend, relay, sameOrigin } from "@/lib/server/backend";
import { sessionConfig, setSessionCookie } from "@/lib/server/session";

// Logs in against the backend and keeps the JWT only in an httpOnly cookie. The browser gets the
// display name and role, nothing else (SEC-24).
export async function POST(request: NextRequest) {
  if (!sameOrigin(request)) {
    return errorResponse(403, "bad_origin", "Cross-origin requests are not allowed.");
  }
  try {
    sessionConfig();
  } catch {
    return errorResponse(500, "config_error", "The dashboard is not configured.");
  }

  const result = await fetchBackend("/v1/auth/login", {
    method: "POST",
    headers: { "content-type": request.headers.get("content-type") ?? "application/json" },
    body: await request.arrayBuffer(),
  });
  if ("failure" in result) return result.failure;
  if (result.response.status !== 200) return relay(result.response);

  const login: unknown = await result.response.json().catch(() => null);
  const { access_token, user } = (login ?? {}) as {
    access_token?: unknown;
    user?: { display_name?: unknown; role?: unknown };
  };
  if (
    typeof access_token !== "string" ||
    !access_token ||
    typeof user?.display_name !== "string" ||
    typeof user.role !== "string"
  ) {
    return errorResponse(502, "bad_backend_response", "The backend sent an unexpected answer.");
  }

  const response = NextResponse.json(
    { display_name: user.display_name, role: user.role },
    { headers: { "cache-control": "no-store" } },
  );
  setSessionCookie(response, access_token);
  return response;
}
