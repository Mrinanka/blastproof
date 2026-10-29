// @vitest-environment jsdom
// F11 guards: query keys and limits of the new hooks (ARCH-22).
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

import { useAgents } from "../src/hooks/use-agents";
import { policiesKey, usePolicies } from "../src/hooks/use-policies";
import { usersKey, useUsers } from "../src/hooks/use-users";
import { makeQueryClient } from "../src/lib/query-client";

const page = () => Response.json({ items: [], total: 0, limit: 100, offset: 0 });
function setup() {
  const client = makeQueryClient(vi.fn());
  client.setDefaultOptions({ queries: { retry: false } });
  function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  }
  return Wrapper;
}
const urlOf = (spy: ReturnType<typeof vi.fn>) => {
  const url = new URL((spy.mock.calls[0] as unknown as [Request])[0].url);
  return url.pathname + url.search;
};

afterEach(() => vi.unstubAllGlobals());

describe("F11 hooks", () => {
  it("useUsers asks for 100 under the users key, and not at all when disabled", async () => {
    expect(usersKey[0]).toBe("users");
    const spy = vi.fn(async () => page());
    vi.stubGlobal("fetch", spy);
    renderHook(() => useUsers(false), { wrapper: setup() });
    await new Promise((r) => setTimeout(r, 20));
    expect(spy).not.toHaveBeenCalled();
    const { result } = renderHook(() => useUsers(true), { wrapper: setup() });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(urlOf(spy)).toBe("/api/blastproof/v1/users?limit=100");
  });

  it("usePolicies asks for 100 under the policies key", async () => {
    expect(policiesKey[0]).toBe("policies");
    const spy = vi.fn(async () => page());
    vi.stubGlobal("fetch", spy);
    const { result } = renderHook(() => usePolicies(), { wrapper: setup() });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(urlOf(spy)).toBe("/api/blastproof/v1/policies?limit=100");
  });

  it("useAgents(false) sends nothing", async () => {
    const spy = vi.fn(async () => page());
    vi.stubGlobal("fetch", spy);
    renderHook(() => useAgents(false), { wrapper: setup() });
    await new Promise((r) => setTimeout(r, 20));
    expect(spy).not.toHaveBeenCalled();
  });
});
