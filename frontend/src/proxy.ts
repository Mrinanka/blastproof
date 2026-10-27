import { NextResponse, type NextRequest } from "next/server";

import { buildCsp, newNonce } from "@/lib/csp";
import { readSessionToken } from "@/lib/server/session";

// Keeps /dashboard behind the session cookie and adds the nonce CSP (FE-05, SEC-29). It never
// validates the token: the backend does, on every call.
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  if (pathname === "/dashboard" || pathname.startsWith("/dashboard/")) {
    let hasSession = false;
    try {
      hasSession = readSessionToken(request) !== undefined;
    } catch {
      // A config error counts as no session: fail closed.
    }
    if (!hasSession) {
      const login = new URL("/login", request.url);
      login.searchParams.set("next", pathname + search);
      return NextResponse.redirect(login);
    }
  }

  const nonce = newNonce();
  const csp = buildCsp(nonce, process.env.NODE_ENV === "development");
  // Next reads the nonce from the request's CSP header and puts it on its own scripts.
  const headers = new Headers(request.headers);
  headers.set("x-nonce", nonce);
  headers.set("content-security-policy", csp);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set("content-security-policy", csp);
  return response;
}

export const config = {
  matcher: ["/dashboard/:path*", "/login", "/api/:path*"],
};
