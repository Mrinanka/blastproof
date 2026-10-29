// @vitest-environment jsdom
// F9 guards: FE-22.
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

import { STATS_KEY, useStats } from "../src/hooks/use-stats";
import { makeQueryClient } from "../src/lib/query-client";

const body = (reviewed: number) => ({
  pending_reviews: 0,
  by_status: {},
  by_decision: {},
  today: {
    day: "2026-10-29",
    actions: 0,
    by_status: {},
    by_decision: {},
    reviewed,
    blocked: 0,
    avg_preview_ms: null,
    llm_cost_usd: 0,
  },
});

function setup(retry = true) {
  const onUnauthorized = vi.fn();
  const client = makeQueryClient(onUnauthorized);
  if (!retry) client.setDefaultOptions({ queries: { retry: false } });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { wrapper, onUnauthorized };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useStats", () => {
  it("calls exactly GET /v1/stats", async () => {
    const spy = vi.fn(async () => Response.json(body(1)));
    vi.stubGlobal("fetch", spy);
    const { result } = renderHook(() => useStats(), { wrapper: setup().wrapper });
    await waitFor(() => expect(result.current.data?.today.reviewed).toBe(1));
    const url = new URL((spy.mock.calls[0] as unknown as [Request])[0].url);
    expect(url.pathname + url.search).toBe("/api/blastproof/v1/stats");
  });

  it("lives under the actions key so a decision refreshes it", () => {
    expect(STATS_KEY[0]).toBe("actions");
  });

  it("refetches at 15 s and not before", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    const spy = vi.fn(async () => Response.json(body(spy.mock.calls.length)));
    vi.stubGlobal("fetch", spy);
    const { result } = renderHook(() => useStats(), { wrapper: setup().wrapper });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.data?.today.reviewed).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(14_900);
    });
    expect(spy).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("calls the redirect once on a 401 without retrying", async () => {
    const spy = vi.fn(async () =>
      Response.json({ error: { code: "unauthorized", message: "no" } }, { status: 401 }),
    );
    vi.stubGlobal("fetch", spy);
    const { wrapper, onUnauthorized } = setup();
    renderHook(() => useStats(), { wrapper });
    await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("reports a 500 as an error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ error: { code: "x", message: "y" } }, { status: 500 })),
    );
    const { result } = renderHook(() => useStats(), { wrapper: setup(false).wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toBeUndefined();
  });
});
