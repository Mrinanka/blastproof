// F3 guards: TEST-10, FE-06, SEC-25, SEC-26.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DELETE, GET, POST } from "../src/app/api/blastproof/[...path]/route";
import { MAX_BODY_BYTES, timeoutFor } from "../src/lib/server/backend";
import { BACKEND, lastCall, makeRequest, ORIGIN, setEnv, stubFetch } from "./helpers";

const ctx = (...path: string[]) => ({ params: Promise.resolve({ path }) });
const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  Response.json(body, { status, headers });

beforeEach(() => setEnv());
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("forwarding", () => {
  it("forwards method, path, query, body and content type", async () => {
    const spy = stubFetch(() => json({ ok: true }));
    const request = makeRequest("/api/blastproof/v1/actions/abc/reject?x=1&y=a%20b", {
      method: "POST",
      headers: { origin: ORIGIN, "content-type": "application/json" },
      body: JSON.stringify({ reason: "no" }),
    });
    await POST(request, ctx("v1", "actions", "abc", "reject"));
    const { url, init, headers } = lastCall(spy);
    expect(url).toBe(`${BACKEND}/v1/actions/abc/reject?x=1&y=a%20b`);
    expect(init.method).toBe("POST");
    expect(headers.get("content-type")).toBe("application/json");
    expect(new TextDecoder().decode(init.body as ArrayBuffer)).toBe('{"reason":"no"}');
  });

  it("adds the Bearer token from the cookie and no other incoming credential or host header", async () => {
    const spy = stubFetch(() => json({}));
    const request = makeRequest("/api/blastproof/v1/actions", {
      cookie: "tok123",
      headers: {
        authorization: "Bearer attacker",
        "x-forwarded-for": "6.6.6.6",
        "x-custom": "1",
        accept: "application/json",
      },
    });
    await GET(request, ctx("v1", "actions"));
    const { headers } = lastCall(spy);
    expect(headers.get("authorization")).toBe("Bearer tok123");
    expect(headers.get("cookie")).toBeNull();
    expect(headers.get("x-forwarded-for")).toBeNull();
    expect(headers.get("host")).toBeNull();
    expect(headers.get("x-custom")).toBeNull();
    expect(headers.get("accept")).toBe("application/json");
  });

  it("sends no Authorization without a cookie, even if the browser sent one", async () => {
    const spy = stubFetch(() => json({}, 401));
    await GET(
      makeRequest("/api/blastproof/v1/actions", { headers: { authorization: "Bearer x" } }),
      ctx("v1", "actions"),
    );
    expect(lastCall(spy).headers.get("authorization")).toBeNull();
  });

  it("sends multipart bytes and boundary unchanged", async () => {
    const spy = stubFetch(() => json({}));
    const type = "multipart/form-data; boundary=XyZ";
    const raw = '--XyZ\r\nContent-Disposition: form-data; name="f"\r\n\r\né\r\n--XyZ--\r\n';
    const bytes = new TextEncoder().encode(raw);
    await POST(
      makeRequest("/api/blastproof/v1/knowledge", {
        method: "POST",
        headers: { origin: ORIGIN, "content-type": type },
        body: bytes,
      }),
      ctx("v1", "knowledge"),
    );
    const { init, headers } = lastCall(spy);
    expect(headers.get("content-type")).toBe(type);
    expect(new Uint8Array(init.body as ArrayBuffer)).toEqual(bytes);
  });

  it("keeps an encoded segment encoded", async () => {
    const spy = stubFetch(() => json({}));
    await GET(makeRequest("/api/blastproof/v1/a%3Fb"), ctx("v1", "a?b"));
    expect(lastCall(spy).url).toBe(`${BACKEND}/v1/a%3Fb`);
  });

  it("forwards a same-origin DELETE and a GET with a foreign Origin", async () => {
    const spy = stubFetch(() => new Response(null, { status: 204 }));
    const del = await DELETE(
      makeRequest("/api/blastproof/v1/x", { method: "DELETE", headers: { origin: ORIGIN } }),
      ctx("v1", "x"),
    );
    expect(del.status).toBe(204);
    const get = await GET(
      makeRequest("/api/blastproof/v1/x", { headers: { origin: "http://evil.test" } }),
      ctx("v1", "x"),
    );
    expect(get.status).toBe(204);
    expect(spy).toHaveBeenCalledTimes(2);
  });
});

describe("response", () => {
  it.each([200, 404, 409, 422, 429, 500])(
    "returns status %i and body unchanged",
    async (status) => {
      const body = { error: { code: "c", message: `m${status}` } };
      stubFetch(() => json(body, status));
      const response = await GET(makeRequest("/api/blastproof/v1/x"), ctx("v1", "x"));
      expect(response.status).toBe(status);
      expect(await response.json()).toEqual(body);
      expect(response.headers.get("cache-control")).toBe("no-store");
    },
  );

  it("keeps Retry-After and X-Request-ID and drops the backend Set-Cookie", async () => {
    stubFetch(() =>
      json({}, 429, { "retry-after": "7", "x-request-id": "r1", "set-cookie": "evil=1" }),
    );
    const response = await GET(makeRequest("/api/blastproof/v1/x"), ctx("v1", "x"));
    expect(response.headers.get("retry-after")).toBe("7");
    expect(response.headers.get("x-request-id")).toBe("r1");
    expect(response.headers.get("set-cookie")).toBeNull();
  });

  it("clears the session cookie on a backend 401, keeping status and body", async () => {
    const body = { error: { code: "unauthorized", message: "expired" } };
    stubFetch(() => json(body, 401));
    const response = await GET(
      makeRequest("/api/blastproof/v1/x", { cookie: "old" }),
      ctx("v1", "x"),
    );
    expect(response.status).toBe(401);
    expect(await response.json()).toEqual(body);
    const cookie = response.headers.get("set-cookie") ?? "";
    expect(cookie).toContain("bp_session=;");
    expect(cookie).toContain("Max-Age=0");
  });

  it("does not clear the cookie on other errors", async () => {
    stubFetch(() => json({}, 403));
    const response = await GET(
      makeRequest("/api/blastproof/v1/x", { cookie: "t" }),
      ctx("v1", "x"),
    );
    expect(response.headers.get("set-cookie")).toBeNull();
  });
});

describe("origin check (SEC-25)", () => {
  it.each(["POST", "DELETE"])(
    "%s with a foreign, missing or null Origin is 403 with no fetch",
    async (method) => {
      const handler = method === "POST" ? POST : DELETE;
      for (const origin of ["http://evil.test", undefined, "null"]) {
        const spy = stubFetch(() => json({}));
        const headers: Record<string, string> = origin ? { origin } : {};
        const response = await handler(
          makeRequest("/api/blastproof/v1/x", { method, headers, cookie: "t" }),
          ctx("v1", "x"),
        );
        expect(response.status).toBe(403);
        expect((await response.json()).error.code).toBe("bad_origin");
        expect(spy).not.toHaveBeenCalled();
      }
    },
  );
});

describe("path guard", () => {
  it.each([
    [["..", "v1"]],
    [["v1", ".."]],
    [["v1", ".", "x"]],
    [["v1", "", "x"]],
    [["docs"]],
    [["openapi.json"]],
    [["mcp"]],
    [[]],
  ])("%j is 404 with no fetch", async (path) => {
    const spy = stubFetch(() => json({}));
    const response = await GET(makeRequest("/api/blastproof/x"), ctx(...path));
    expect(response.status).toBe(404);
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("failures", () => {
  it("backend down is 502 and the body holds no address or token", async () => {
    stubFetch(() => Promise.reject(new TypeError(`fetch failed ${BACKEND}`)));
    const response = await GET(
      makeRequest("/api/blastproof/v1/x", { cookie: "tok" }),
      ctx("v1", "x"),
    );
    expect(response.status).toBe(502);
    const text = await response.text();
    expect(text).toContain("backend_unavailable");
    expect(text).not.toContain("backend.test");
    expect(text).not.toContain("tok");
  });

  it("a timeout is 504", async () => {
    stubFetch(() => Promise.reject(new DOMException("timed out", "TimeoutError")));
    const response = await GET(makeRequest("/api/blastproof/v1/x"), ctx("v1", "x"));
    expect(response.status).toBe(504);
    expect(await response.text()).toContain("backend_timeout");
  });

  it("a missing BLASTPROOF_API_URL is 500 with no fetch", async () => {
    vi.stubEnv("BLASTPROOF_API_URL", "");
    const spy = stubFetch(() => json({}));
    const response = await GET(makeRequest("/api/blastproof/v1/x"), ctx("v1", "x"));
    expect(response.status).toBe(500);
    expect(await response.text()).toContain("config_error");
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("body cap and long timeouts (F10)", () => {
  const post = (body: BodyInit, headers: Record<string, string> = {}) =>
    makeRequest("/api/blastproof/v1/knowledge", {
      method: "POST",
      headers: { origin: ORIGIN, ...headers },
      body,
    });

  it("refuses a Content-Length over the cap with 413 and no fetch", async () => {
    const spy = stubFetch(() => json({}));
    const request = post("x", { "content-length": String(MAX_BODY_BYTES + 1) });
    const response = await POST(request, ctx("v1", "knowledge"));
    expect(response.status).toBe(413);
    expect(await response.text()).toContain("too_large");
    expect(spy).not.toHaveBeenCalled();
  });

  it("refuses a streamed body over the cap that has no Content-Length", async () => {
    const spy = stubFetch(() => json({}));
    const chunk = new Uint8Array(512 * 1024);
    let sent = 0;
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (sent++ < 5) controller.enqueue(chunk);
        else controller.close();
      },
    });
    const request = new Request(`${ORIGIN}/api/blastproof/v1/knowledge`, {
      method: "POST",
      headers: { origin: ORIGIN },
      body: stream,
      duplex: "half",
    } as RequestInit);
    const { NextRequest } = await import("next/server");
    const response = await POST(new NextRequest(request), ctx("v1", "knowledge"));
    expect(response.status).toBe(413);
    expect(spy).not.toHaveBeenCalled();
  });

  it("forwards a body exactly at the cap unchanged", async () => {
    const spy = stubFetch(() => json({ ok: true }));
    const bytes = new Uint8Array(MAX_BODY_BYTES).fill(7);
    const response = await POST(post(bytes), ctx("v1", "knowledge"));
    expect(response.status).toBe(200);
    const sent = new Uint8Array(lastCall(spy).init.body as ArrayBuffer);
    expect(sent.byteLength).toBe(MAX_BODY_BYTES);
    expect(sent[0]).toBe(7);
  });

  it("gives upload and scan 120 s and everything else 30 s", () => {
    expect(timeoutFor("POST", "/v1/knowledge")).toBe(120_000);
    expect(timeoutFor("POST", "/v1/systems/abc/scan")).toBe(120_000);
    expect(timeoutFor("GET", "/v1/knowledge?limit=100")).toBe(30_000);
    expect(timeoutFor("DELETE", "/v1/knowledge/x")).toBe(30_000);
    expect(timeoutFor("POST", "/v1/actions/abc/approve")).toBe(30_000);
    expect(timeoutFor("POST", "/v1/systems/abc/scan/x")).toBe(30_000);
  });
});
