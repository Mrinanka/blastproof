// F3 guards: FE-05, SEC-29.
import { getRedirectUrl, unstable_doesMiddlewareMatch } from "next/experimental/testing/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { config, proxy } from "../src/proxy";
import { makeRequest, ORIGIN, setEnv } from "./helpers";

beforeEach(() => setEnv());
afterEach(() => vi.unstubAllEnvs());

describe("matcher", () => {
  it.each(["/dashboard", "/dashboard/review", "/login", "/api/blastproof/v1/actions"])(
    "matches %s",
    (url) => expect(unstable_doesMiddlewareMatch({ config, url })).toBe(true),
  );
  it.each(["/", "/_next/static/x.js"])("does not match %s", (url) =>
    expect(unstable_doesMiddlewareMatch({ config, url })).toBe(false),
  );
});

describe("session gate", () => {
  it("redirects /dashboard without a cookie, keeping path and query in next", () => {
    const response = proxy(makeRequest("/dashboard/actions/1?x=1"));
    expect(response.status).toBe(307);
    expect(getRedirectUrl(response)).toBe(
      `${ORIGIN}/login?next=%2Fdashboard%2Factions%2F1%3Fx%3D1`,
    );
  });

  it("treats a config error as no session", () => {
    setEnv({ SESSION_COOKIE_NAME: "" });
    const response = proxy(makeRequest("/dashboard", { cookie: "t" }));
    expect(response.status).toBe(307);
  });

  it("lets a request with the cookie through", () => {
    const response = proxy(makeRequest("/dashboard", { cookie: "t" }));
    expect(getRedirectUrl(response)).toBeNull();
  });

  it("does not redirect /login without a cookie", () => {
    const response = proxy(makeRequest("/login"));
    expect(getRedirectUrl(response)).toBeNull();
    expect(response.headers.get("content-security-policy")).toBeTruthy();
  });
});

describe("CSP", () => {
  const nonceOf = (csp: string | null) => /'nonce-([^']+)'/.exec(csp ?? "")?.[1];

  it("sets a nonce CSP on the response and the same one on the request override", () => {
    const response = proxy(makeRequest("/dashboard", { cookie: "t" }));
    const csp = response.headers.get("content-security-policy");
    const nonce = nonceOf(csp);
    expect(nonce).toBeTruthy();
    expect(response.headers.get("x-middleware-request-content-security-policy")).toBe(csp);
    expect(response.headers.get("x-middleware-request-x-nonce")).toBe(nonce);
  });

  it("uses a different nonce on every request", () => {
    const a = nonceOf(proxy(makeRequest("/login")).headers.get("content-security-policy"));
    const b = nonceOf(proxy(makeRequest("/login")).headers.get("content-security-policy"));
    expect(a).not.toBe(b);
  });
});
