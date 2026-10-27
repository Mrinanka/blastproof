// F4 guards: SEC-30.
import { afterEach, describe, expect, it, vi } from "vitest";

import { logout } from "../src/lib/logout";

afterEach(() => vi.unstubAllGlobals());

describe("logout", () => {
  it("POSTs to the logout route with no body and returns true on 204", async () => {
    const spy = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", spy);
    expect(await logout()).toBe(true);
    const [url, init] = spy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/auth/logout");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
  });

  it.each([403, 500])("returns false on %s", async (status) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { status })),
    );
    expect(await logout()).toBe(false);
  });

  it("returns false, without throwing, when the network fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Promise.reject(new TypeError("offline"))),
    );
    expect(await logout()).toBe(false);
  });
});
