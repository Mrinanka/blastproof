// F2 guards: FE-03 in the browser. TanStack decides "server" when it loads, so `window` must
// exist before any import; this lives in its own file because query-client.test.ts needs the server.
import { vi } from "vitest";

vi.hoisted(() => {
  (globalThis as { window?: unknown }).window = {};
});

import { focusManager, QueryObserver } from "@tanstack/react-query";
import { afterEach, describe, expect, it } from "vitest";

import { getQueryClient, makeQueryClient } from "../src/lib/query-client";

afterEach(() => {
  vi.useRealTimers();
  focusManager.setFocused(undefined);
});

describe("in the browser", () => {
  it("getQueryClient returns one shared client", () => {
    expect(getQueryClient()).toBe(getQueryClient());
  });

  it("polling pauses while the page is hidden and resumes on focus", async () => {
    vi.useFakeTimers();
    const queryFn = vi.fn(async () => "ok");
    const observer = new QueryObserver(makeQueryClient(), {
      queryKey: ["poll"],
      queryFn,
      refetchInterval: 1000,
    });
    const unsubscribe = observer.subscribe(() => {});
    await vi.advanceTimersByTimeAsync(2500);
    expect(queryFn.mock.calls.length).toBeGreaterThan(1);

    focusManager.setFocused(false);
    const paused = queryFn.mock.calls.length;
    await vi.advanceTimersByTimeAsync(5000);
    expect(queryFn.mock.calls.length).toBe(paused);

    focusManager.setFocused(true);
    await vi.advanceTimersByTimeAsync(2500);
    expect(queryFn.mock.calls.length).toBeGreaterThan(paused);
    unsubscribe();
  });
});
