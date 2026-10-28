// @vitest-environment jsdom
// F6 guards: FE-14 (the shared actions list hook).
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

import { actionsKey, useActions, type ActionsQuery } from "../src/hooks/use-actions";
import { ApiError } from "../src/lib/api/errors";
import { makeQueryClient } from "../src/lib/query-client";

const QUERY: ActionsQuery = { status: ["awaiting_review"], order: "oldest", limit: 25, offset: 0 };
const page = () => Response.json({ items: [], total: 0, limit: 25, offset: 0 });

function setup(retry = true) {
  const onUnauthorized = vi.fn();
  const client = makeQueryClient(onUnauthorized);
  if (!retry) client.setDefaultOptions({ queries: { retry: false } });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { wrapper, onUnauthorized, client };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useActions", () => {
  it("asks for the exact URL", async () => {
    const fetchSpy = vi.fn(async () => page());
    vi.stubGlobal("fetch", fetchSpy);
    const { result } = renderHook(() => useActions(QUERY), { wrapper: setup().wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());
    const request = (fetchSpy.mock.calls[0] as unknown as [Request])[0];
    const url = new URL(request.url);
    expect(url.pathname + url.search).toBe(
      "/api/blastproof/v1/actions?status=awaiting_review&order=oldest&limit=25&offset=0",
    );
  });

  it("sits under the actions key, so invalidating it refetches", async () => {
    expect(actionsKey(QUERY)[0]).toBe("actions");
    const fetchSpy = vi.fn(async () => page());
    vi.stubGlobal("fetch", fetchSpy);
    const { wrapper, client } = setup();
    const { result } = renderHook(() => useActions(QUERY), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["actions"] });
    });
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("polls at the given interval and not before", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    const fetchSpy = vi.fn(async () => page());
    vi.stubGlobal("fetch", fetchSpy);
    renderHook(() => useActions(QUERY, { refetchInterval: 5000 }), { wrapper: setup().wrapper });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4900);
    });
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("does not poll without the option", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    const fetchSpy = vi.fn(async () => page());
    vi.stubGlobal("fetch", fetchSpy);
    renderHook(() => useActions(QUERY), { wrapper: setup().wrapper });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("calls the redirect on a 401 without retrying", async () => {
    const fetchSpy = vi.fn(async () =>
      Response.json({ error: { code: "unauthorized", message: "no" } }, { status: 401 }),
    );
    vi.stubGlobal("fetch", fetchSpy);
    const { wrapper, onUnauthorized } = setup();
    renderHook(() => useActions(QUERY), { wrapper });
    await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("reports a 500 as an error value", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ error: { code: "x", message: "y" } }, { status: 500 })),
    );
    const { result } = renderHook(() => useActions(QUERY), { wrapper: setup(false).wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toBeUndefined();
  });

  it("turns a rejected fetch into ApiError(0, network_error)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("failed");
      }),
    );
    const { result } = renderHook(() => useActions(QUERY), { wrapper: setup(false).wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    const error = result.current.error as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(0);
    expect(error.code).toBe("network_error");
  });
});
