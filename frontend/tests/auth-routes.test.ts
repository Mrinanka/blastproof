// F3 guards: SEC-23, SEC-24, SEC-30, FE-04.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST as login } from "../src/app/api/auth/login/route";
import { POST as logout } from "../src/app/api/auth/logout/route";
import { BACKEND, lastCall, makeRequest, ORIGIN, setEnv, stubFetch } from "./helpers";

const BACKEND_LOGIN = {
  access_token: "jwt-secret",
  token_type: "bearer",
  expires_at: "2026-10-28T00:00:00Z",
  user: { id: "u1", email: "a@example.com", display_name: "Demo One", role: "member" },
};

const loginRequest = (headers: Record<string, string> = { origin: ORIGIN }) =>
  makeRequest("/api/auth/login", {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify({ email: "a@example.com", password: "pw" }),
  });

beforeEach(() => setEnv());
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("login", () => {
  it("returns only display_name and role and sets the httpOnly cookie", async () => {
    const spy = stubFetch(() => Response.json(BACKEND_LOGIN));
    const response = await login(loginRequest());
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ display_name: "Demo One", role: "member" });
    const { url, init } = lastCall(spy);
    expect(url).toBe(`${BACKEND}/v1/auth/login`);
    expect(new TextDecoder().decode(init.body as ArrayBuffer)).toContain("a@example.com");
    const cookie = response.headers.get("set-cookie") ?? "";
    expect(cookie).toContain("bp_session=jwt-secret");
    expect(cookie).toContain("HttpOnly");
    expect(cookie.toLowerCase()).toContain("samesite=lax");
    expect(cookie).toContain("Path=/");
    expect(cookie).toContain("Max-Age=28800");
    expect(cookie).not.toContain("Secure");
  });

  it("marks the cookie Secure in production", async () => {
    vi.stubEnv("NODE_ENV", "production");
    stubFetch(() => Response.json(BACKEND_LOGIN));
    const response = await login(loginRequest());
    expect(response.headers.get("set-cookie")).toContain("Secure");
  });

  it.each([401, 422, 429])("passes %i through with no cookie", async (status) => {
    const body = { error: { code: "x", message: "nope" } };
    stubFetch(() => Response.json(body, { status, headers: { "retry-after": "9" } }));
    const response = await login(loginRequest());
    expect(response.status).toBe(status);
    expect(await response.json()).toEqual(body);
    expect(response.headers.get("set-cookie")).toBeNull();
    expect(response.headers.get("retry-after")).toBe("9");
  });

  it.each([
    {},
    { access_token: "t" },
    { access_token: "", user: { display_name: "a", role: "member" } },
  ])("a 200 with a bad shape is 502 with no cookie", async (shape) => {
    stubFetch(() => Response.json(shape));
    const response = await login(loginRequest());
    expect(response.status).toBe(502);
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("a foreign or missing Origin is 403 with no fetch", async () => {
    for (const headers of [{ origin: "http://evil.test" }, {} as Record<string, string>]) {
      const spy = stubFetch(() => Response.json(BACKEND_LOGIN));
      const response = await login(loginRequest(headers));
      expect(response.status).toBe(403);
      expect(spy).not.toHaveBeenCalled();
    }
  });

  it("backend down is 502 with no cookie", async () => {
    stubFetch(() => Promise.reject(new TypeError("fetch failed")));
    const response = await login(loginRequest());
    expect(response.status).toBe(502);
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("a bad session config is 500 with no fetch", async () => {
    setEnv({ SESSION_MAX_AGE_SECONDS: "abc" });
    const spy = stubFetch(() => Response.json(BACKEND_LOGIN));
    const response = await login(loginRequest());
    expect(response.status).toBe(500);
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("logout", () => {
  it("is 204 and expires the cookie", async () => {
    const response = await logout(
      makeRequest("/api/auth/logout", { method: "POST", headers: { origin: ORIGIN } }),
    );
    expect(response.status).toBe(204);
    const cookie = response.headers.get("set-cookie") ?? "";
    expect(cookie).toContain("bp_session=;");
    expect(cookie).toContain("Max-Age=0");
    expect(cookie).toContain("HttpOnly");
  });

  it("a foreign Origin is 403 and sets no cookie", async () => {
    const response = await logout(
      makeRequest("/api/auth/logout", { method: "POST", headers: { origin: "http://evil.test" } }),
    );
    expect(response.status).toBe(403);
    expect(response.headers.get("set-cookie")).toBeNull();
  });
});
