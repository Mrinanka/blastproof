// @vitest-environment jsdom
// F3 guards: FE-08.
// openapi-fetch reads the global Request when the client is created, so replace it before the
// imports run. Node's Request needs an absolute URL; a browser resolves the proxy path itself.
vi.hoisted(() => {
  const NodeRequest = globalThis.Request;
  class BrowserRequest extends NodeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === "string" ? new URL(input, "http://localhost") : input, init);
    }
  }
  globalThis.Request = BrowserRequest;
});

import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useMe } from "../src/hooks/use-me";
import { makeQueryClient } from "../src/lib/query-client";

const ME = { id: "u1", email: "a@example.com", display_name: "Demo One", role: "member" };

function setup() {
  const onUnauthorized = vi.fn();
  const client = makeQueryClient(onUnauthorized);
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { wrapper, onUnauthorized };
}

afterEach(() => vi.unstubAllGlobals());

describe("useMe", () => {
  it("returns the user from the proxy path", async () => {
    const fetchSpy = vi.fn(async () => Response.json(ME));
    vi.stubGlobal("fetch", fetchSpy);
    const { wrapper } = setup();
    const { result } = renderHook(() => useMe(), { wrapper });
    await waitFor(() => expect(result.current.data).toEqual(ME));
    const request = (fetchSpy.mock.calls[0] as unknown as [Request])[0];
    expect(new URL(request.url).pathname).toBe("/api/blastproof/v1/auth/me");
  });

  it("calls the redirect on a 401, without retrying", async () => {
    const fetchSpy = vi.fn(async () =>
      Response.json({ error: { code: "unauthorized", message: "no" } }, { status: 401 }),
    );
    vi.stubGlobal("fetch", fetchSpy);
    const { wrapper, onUnauthorized } = setup();
    renderHook(() => useMe(), { wrapper });
    await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });
});
