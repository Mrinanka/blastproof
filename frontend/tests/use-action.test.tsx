// @vitest-environment jsdom
// F7 guards: FE-17 (polling, 404 view).
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
import { act, cleanup, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ACTION_POLL_MS, actionKey, useAction } from "../src/hooks/use-action";
import { makeQueryClient } from "../src/lib/query-client";
import { ACTION_ID, makeAction } from "./action-detail-fixture";

const urls: string[] = [];
function serve(handler: () => Response) {
  urls.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      urls.push(new URL(input.url).pathname);
      return handler();
    }),
  );
}
function mount() {
  const client = makeQueryClient(vi.fn());
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(() => useAction(ACTION_ID), { wrapper });
}
const tick = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: false }));
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useAction", () => {
  it("lives under the actions key and asks for the one action", async () => {
    expect(actionKey("x")[0]).toBe("actions");
    serve(() => Response.json(makeAction()));
    mount();
    await tick(0);
    expect(urls).toEqual([`/api/blastproof/v1/actions/${ACTION_ID}`]);
  });

  it.each(["awaiting_review", "approved", "committing"] as const)(
    "keeps polling while %s",
    async (status) => {
      serve(() => Response.json(makeAction({ status })));
      mount();
      await tick(0);
      await tick(ACTION_POLL_MS + 100);
      await tick(ACTION_POLL_MS + 100);
      expect(urls.length).toBe(3);
    },
  );

  it.each(["rejected", "expired", "blocked", "committed", "executed", "failed"] as const)(
    "stops polling once %s",
    async (status) => {
      serve(() => Response.json(makeAction({ status })));
      mount();
      await tick(0);
      await tick(ACTION_POLL_MS * 3);
      expect(urls.length).toBe(1);
    },
  );

  it("stops after a 404 without retrying", async () => {
    serve(() =>
      Response.json({ error: { code: "not_found", message: "Not found" } }, { status: 404 }),
    );
    mount();
    await tick(0);
    await tick(ACTION_POLL_MS * 3);
    expect(urls.length).toBe(1);
  });

  it("keeps going after a 500 and recovers", async () => {
    let n = 0;
    serve(() => {
      n += 1;
      return n === 1
        ? Response.json({ error: { code: "boom", message: "down" } }, { status: 500 })
        : Response.json(makeAction());
    });
    const { result } = mount();
    await tick(0);
    await tick(ACTION_POLL_MS * 2 + 10_000);
    expect(result.current.data?.id).toBe(ACTION_ID);
  });
});
