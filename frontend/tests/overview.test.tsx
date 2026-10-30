// @vitest-environment jsdom
// F9 guards: FE-22, DW-F09 (stand-in), SEC-27, ARCH-22.
vi.hoisted(() => {
  const NodeRequest = globalThis.Request;
  class BrowserRequest extends NodeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === "string" ? new URL(input, "http://localhost") : input, init);
    }
  }
  globalThis.Request = BrowserRequest;
});

import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Overview } from "../src/components/overview/overview";
import { makeQueryClient } from "../src/lib/query-client";

const stats = (reviewed = 7, day = "2026-10-29") =>
  Response.json({
    pending_reviews: 99,
    by_status: {},
    by_decision: {},
    today: {
      day,
      actions: 40,
      by_status: {},
      by_decision: { allow: 20, block: 6 },
      reviewed,
      blocked: 6,
      avg_preview_ms: 1234,
      llm_cost_usd: 0.0042,
    },
  });

const row = (id: string, headline: string | null = "Updates 3 rows") => ({
  id,
  status: "committed",
  decision: "allow",
  risk_level: "low",
  agent: { id: `ag-${id}`, name: "agent-a" },
  intent: "Clean up",
  headline,
  tables: ["users"],
  created_at: "2026-10-29T10:00:00Z",
  decided_by: null,
});

const list = (ids: string[], total = ids.length) =>
  Response.json({ items: ids.map((i) => row(i)), total, limit: 10, offset: 0 });

interface Routes {
  stats?: (n: number) => Response;
  list?: (url: URL, n: number) => Response;
  pending?: number;
}
const calls = { stats: 0, list: 0, pending: 0 };
const listUrls: URL[] = [];

function serve(r: Routes = {}) {
  calls.stats = calls.list = calls.pending = 0;
  listUrls.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      const url = new URL(input.url);
      if (url.pathname.endsWith("/v1/stats")) return (r.stats ?? (() => stats()))(++calls.stats);
      if (url.searchParams.get("status") === "awaiting_review") {
        calls.pending++;
        return Response.json({ items: [], total: r.pending ?? 4, limit: 1, offset: 0 });
      }
      listUrls.push(url);
      return (r.list ?? (() => list(["a1"])))(url, ++calls.list);
    }),
  );
}

let client: QueryClient;
function mount() {
  client = makeQueryClient(vi.fn());
  client.setDefaultOptions({ queries: { retry: false } });
  render(
    <QueryClientProvider client={client}>
      <Overview />
    </QueryClientProvider>,
  );
}
const advance = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
const settle = () => advance(0);

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: false }));
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const card = (label: RegExp) => {
  const el = screen.getByText(label).closest("[data-slot='card']") as HTMLElement;
  return within(el);
};

describe("Overview", () => {
  it("shows the fixture's numbers on the cards", async () => {
    serve();
    mount();
    await settle();
    expect(card(/Reviewed today/).getByText("7")).toBeTruthy();
    expect(card(/Blocked today/).getByText("6")).toBeTruthy();
    expect(card(/Avg preview/).getByText("1.2 s")).toBeTruthy();
    expect(card(/LLM cost/).getByText("$0.0042")).toBeTruthy();
  });

  it("takes the Pending card from the actions total, not stats", async () => {
    serve({ pending: 4 });
    mount();
    await settle();
    expect(card(/Pending reviews/).getByText("4")).toBeTruthy();
    expect(screen.queryByText("99")).toBeNull();
  });

  it("labels the day with the backend's day", async () => {
    serve({ stats: () => stats(1, "2026-02-03") });
    mount();
    await settle();
    expect(screen.getByText("Today (UTC): Feb 3, 2026")).toBeTruthy();
  });

  it("asks for exactly the 10 newest actions", async () => {
    serve();
    mount();
    await settle();
    expect([...listUrls[0].searchParams.entries()].sort()).toEqual([
      ["limit", "10"],
      ["order", "newest"],
    ]);
  });

  it("links rows to their action and to History", async () => {
    serve({ list: () => list(["abc"]) });
    mount();
    await settle();
    expect(screen.getByRole("link", { name: "Updates 3 rows" }).getAttribute("href")).toBe(
      "/dashboard/actions/abc",
    );
    expect(screen.getByRole("link", { name: /View all in History/ }).getAttribute("href")).toBe(
      "/dashboard/history",
    );
  });

  it("shows an empty state", async () => {
    serve({ list: () => list([]) });
    mount();
    await settle();
    expect(screen.getByText("No actions yet")).toBeTruthy();
  });

  it("shows an error with Retry when stats fail first, while the table still renders", async () => {
    serve({
      stats: () => Response.json({ error: { code: "x", message: "Stats broke" } }, { status: 500 }),
    });
    mount();
    await settle();
    expect(screen.getByText("Stats broke")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
    expect(screen.getByText("Updates 3 rows")).toBeTruthy();
  });

  it("keeps the numbers and says so when a poll fails", async () => {
    serve({
      stats: (n) =>
        n === 1
          ? stats()
          : Response.json({ error: { code: "x", message: "Down" } }, { status: 500 }),
    });
    mount();
    await settle();
    await advance(15_100);
    expect(screen.getByText(/Couldn't refresh/)).toBeTruthy();
    expect(card(/Reviewed today/).getByText("7")).toBeTruthy();
  });

  it("refetches stats and the table at 15 s and not before", async () => {
    serve();
    mount();
    await settle();
    await advance(14_800);
    expect(calls.stats).toBe(1);
    expect(calls.list).toBe(1);
    await advance(400);
    expect(calls.stats).toBe(2);
    expect(calls.list).toBe(2);
  });

  it("refetches stats when a decision invalidates the actions key", async () => {
    serve({ stats: (n) => stats(n === 1 ? 7 : 8) });
    mount();
    await settle();
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["actions"] });
    });
    await settle();
    expect(card(/Reviewed today/).getByText("8")).toBeTruthy();
  });

  it("shows skeleton cards on first load", () => {
    serve();
    mount();
    expect(screen.getAllByTestId("skeleton-card")).toHaveLength(5);
  });
});
