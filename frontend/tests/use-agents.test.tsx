// @vitest-environment jsdom
// F8 guards: the agent list behind History's agent filter (F11 reuses it).
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
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AGENTS_LIMIT, agentsKey, useAgents } from "../src/hooks/use-agents";
import { ApiError } from "../src/lib/api/errors";
import { makeQueryClient } from "../src/lib/query-client";

const page = () => Response.json({ items: [], total: 0, limit: AGENTS_LIMIT, offset: 0 });

function setup() {
  const client = makeQueryClient(vi.fn());
  client.setDefaultOptions({ queries: { retry: false } });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { wrapper, client };
}

afterEach(() => vi.unstubAllGlobals());

describe("useAgents", () => {
  it("asks for the first 100 agents, the backend's maximum", async () => {
    const fetchSpy = vi.fn(async () => page());
    vi.stubGlobal("fetch", fetchSpy);
    const { result } = renderHook(() => useAgents(), { wrapper: setup().wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());
    const url = new URL((fetchSpy.mock.calls[0] as unknown as [Request])[0].url);
    expect(url.pathname + url.search).toBe("/api/blastproof/v1/agents?limit=100");
  });

  it("sits under the agents key, so invalidating it refetches", async () => {
    expect(agentsKey[0]).toBe("agents");
    const fetchSpy = vi.fn(async () => page());
    vi.stubGlobal("fetch", fetchSpy);
    const { wrapper, client } = setup();
    const { result } = renderHook(() => useAgents(), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["agents"] });
    });
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("reports a failure as an ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ error: { code: "x", message: "Down" } }, { status: 500 })),
    );
    const { result } = renderHook(() => useAgents(), { wrapper: setup().wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect((result.current.error as ApiError).message).toBe("Down");
  });
});
