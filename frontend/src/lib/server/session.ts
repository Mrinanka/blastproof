import "server-only";

import type { NextRequest, NextResponse } from "next/server";

import { ConfigError } from "./backend";

export interface SessionConfig {
  cookieName: string;
  maxAgeSeconds: number;
}

export function sessionConfig(): SessionConfig {
  const cookieName = process.env.SESSION_COOKIE_NAME;
  if (!cookieName) throw new ConfigError("SESSION_COOKIE_NAME is not set");
  const raw = process.env.SESSION_MAX_AGE_SECONDS;
  const maxAgeSeconds = raw && /^\d+$/.test(raw) ? Number(raw) : 0;
  if (maxAgeSeconds <= 0) {
    throw new ConfigError("SESSION_MAX_AGE_SECONDS must be a positive integer");
  }
  return { cookieName, maxAgeSeconds };
}

export function sessionCookieOptions(maxAge: number) {
  return {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge,
    secure: process.env.NODE_ENV === "production",
  } as const;
}

export function setSessionCookie(response: NextResponse, token: string): void {
  const { cookieName, maxAgeSeconds } = sessionConfig();
  response.cookies.set(cookieName, token, sessionCookieOptions(maxAgeSeconds));
}

export function clearSessionCookie(response: NextResponse): void {
  const { cookieName } = sessionConfig();
  response.cookies.set(cookieName, "", sessionCookieOptions(0));
}

export function readSessionToken(request: NextRequest): string | undefined {
  return request.cookies.get(sessionConfig().cookieName)?.value || undefined;
}
