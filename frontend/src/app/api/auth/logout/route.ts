import { NextResponse, type NextRequest } from "next/server";

import { errorResponse, sameOrigin } from "@/lib/server/backend";
import { clearSessionCookie } from "@/lib/server/session";

export async function POST(request: NextRequest) {
  if (!sameOrigin(request)) {
    return errorResponse(403, "bad_origin", "Cross-origin requests are not allowed.");
  }
  const response = new NextResponse(null, {
    status: 204,
    headers: { "cache-control": "no-store" },
  });
  try {
    clearSessionCookie(response);
  } catch {
    return errorResponse(500, "config_error", "The dashboard is not configured.");
  }
  return response;
}
