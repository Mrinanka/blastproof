// @vitest-environment jsdom
// F4 guards: FE-09, DW-F04.
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

import {
  PENDING_COUNT_KEY,
  PENDING_POLL_MS,
  usePendingCount,
} from "../src/hooks/use-pending-count";
import { makeQueryClient } from "../src/lib/query-client";

const page = (total: number) => Response.json({ items: [], total, limit: 1, offset: 0 });

function setup() {
  const onUnauthorized = vi.fn();
  const client = makeQueryClient(onUnauthorized);
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { wrapper, onUnauthorized };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("usePendingCount", () => {
  it("asks for exactly one awaiting_review row and returns the total", async () => {
    const fetchSpy = vi.fn(async () => page(3));
    vi.stubGlobal("fetch", fetchSpy);
    const { result } = renderHook(() => usePendingCount(), { wrapper: setup().wrapper });
    await waitFor(() => expect(result.current.data).toBe(3));
    const request = (fetchSpy.mock.calls[0] as unknown as [Request])[0];
    const url = new URL(request.url);
    expect(url.pathname + url.search).toBe(
      "/api/blastproof/v1/actions?status=awaiting_review&limit=1",
    );
  });

  it("lives under the actions key so a decision's invalidation refreshes it", () => {
    expect(PENDING_COUNT_KEY[0]).toBe("actions");
    expect(PENDING_POLL_MS).toBe(5000);
  });

  it("refetches after 5 s and not before", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    const totals = [2, 3];
    const fetchSpy = vi.fn(async () => page(totals[Math.min(fetchSpy.mock.calls.length - 1, 1)]));
    vi.stubGlobal("fetch", fetchSpy);
    const { result } = renderHook(() => usePendingCount(), { wrapper: setup().wrapper });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.data).toBe(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(PENDING_POLL_MS - 100);
    });
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(result.current.data).toBe(3);
  });

  it("calls the redirect on a 401 without retrying", async () => {
    const fetchSpy = vi.fn(async () =>
      Response.json({ error: { code: "unauthorized", message: "no" } }, { status: 401 }),
    );
    vi.stubGlobal("fetch", fetchSpy);
    const { wrapper, onUnauthorized } = setup();
    renderHook(() => usePendingCount(), { wrapper });
    await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("reports a 500 as an error value", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ error: { code: "x", message: "y" } }, { status: 500 })),
    );
    const client = makeQueryClient(vi.fn());
    client.setDefaultOptions({ queries: { retry: false } });
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => usePendingCount(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toBeUndefined();
  });
});
