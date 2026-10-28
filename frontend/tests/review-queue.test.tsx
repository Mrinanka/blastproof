// @vitest-environment jsdom
// F6 guards: FE-14, DW-F06 (stand-in), SEC-27, ARCH-22.
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
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  REVIEW_PAGE_SIZE,
  REVIEW_POLL_MS,
  ReviewQueue,
} from "../src/components/review/review-queue";
import { makeQueryClient } from "../src/lib/query-client";

interface Item {
  id: string;
  agent?: string;
  intent?: string | null;
  headline?: string | null;
  risk?: string | null;
}

const row = ({
  id,
  agent = "agent-a",
  intent = "Clean up",
  headline = "Updates 3 rows",
  risk = "high",
}: Item) => ({
  id,
  status: "awaiting_review",
  decision: null,
  risk_level: risk,
  agent: { id: `ag-${id}`, name: agent },
  intent,
  headline,
  tables: ["users"],
  created_at: "2026-10-29T10:00:00Z",
  decided_by: null,
});

const page = (items: Item[], total = items.length, offset = 0) =>
  Response.json({ items: items.map(row), total, limit: REVIEW_PAGE_SIZE, offset });

function mount() {
  const client = makeQueryClient(vi.fn());
  client.setDefaultOptions({ queries: { retry: false } });
  render(
    <QueryClientProvider client={client}>
      <ReviewQueue />
    </QueryClientProvider>,
  );
}

const urls: URL[] = [];
function serve(handler: (url: URL, n: number) => Response | Promise<Response>) {
  urls.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      const url = new URL(input.url);
      urls.push(url);
      return handler(url, urls.length);
    }),
  );
}
const settle = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
const poll = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(REVIEW_POLL_MS + 100);
  });

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: false }));
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("ReviewQueue", () => {
  it("shows a held write that arrives on the next poll, in the same tree (DW-F06)", async () => {
    serve((_u, n) => (n === 1 ? page([]) : page([{ id: "a1", headline: "Sets plan to free" }])));
    mount();
    await settle();
    expect(screen.getByText("Nothing to review")).toBeTruthy();
    expect(screen.getByText(/within 5 seconds/)).toBeTruthy();
    await poll();
    expect(screen.queryByText("Nothing to review")).toBeNull();
    expect(screen.getByText("Sets plan to free")).toBeTruthy();
  });

  it("asks oldest first with no owner or user parameter, and keeps the backend order", async () => {
    serve(() =>
      page([
        { id: "b", headline: "h0" },
        { id: "a", headline: "h1" },
        { id: "c", headline: "h2" },
      ]),
    );
    mount();
    await settle();
    expect(urls[0].searchParams.get("order")).toBe("oldest");
    expect(urls[0].searchParams.getAll("status")).toEqual(["awaiting_review"]);
    expect(urls[0].searchParams.get("limit")).toBe("25");
    expect([...urls[0].searchParams.keys()].sort()).toEqual(["limit", "offset", "order", "status"]);
    expect(screen.getAllByRole("link").map((l) => l.textContent)).toEqual(["h0", "h1", "h2"]);
  });

  it("shows agent, intent, headline, risk and a time element, and links to the action", async () => {
    serve(() =>
      page([{ id: "abc", agent: "claude-1", intent: "Fix plans", headline: "Sets plan" }]),
    );
    mount();
    await settle();
    const table = within(screen.getByRole("table"));
    expect(table.getByText("claude-1")).toBeTruthy();
    expect(table.getByText("Fix plans")).toBeTruthy();
    expect(table.getByText("High risk")).toBeTruthy();
    expect(document.querySelector("time")).not.toBeNull();
    expect(screen.getByRole("link", { name: "Sets plan" }).getAttribute("href")).toBe(
      "/dashboard/actions/abc",
    );
  });

  it("falls back for null headline, intent and risk", async () => {
    serve(() => page([{ id: "n", intent: null, headline: null, risk: null }]));
    mount();
    await settle();
    expect(screen.getByRole("link", { name: "Summary not ready" })).toBeTruthy();
    expect(screen.getByText("—")).toBeTruthy();
    expect(screen.getByText("No risk score")).toBeTruthy();
  });

  it("renders agent text as literal text", async () => {
    const evil = "<img src=x onerror=alert(1)>";
    serve(() => page([{ id: "x", intent: evil, headline: evil }]));
    mount();
    await settle();
    expect(screen.getAllByText(evil).length).toBe(2);
    expect(document.querySelector("img")).toBeNull();
  });

  it("shows the backend's message and Retry on a 500 with no rows", async () => {
    serve(() =>
      Response.json({ error: { code: "boom", message: "Database is down" } }, { status: 500 }),
    );
    mount();
    await settle();
    expect(screen.getByText("Database is down")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("says the server could not be reached on a network error", async () => {
    serve(() => {
      throw new TypeError("failed");
    });
    mount();
    await settle();
    expect(screen.getByText("Could not reach the server.")).toBeTruthy();
  });

  it("keeps the rows and warns when a later poll fails", async () => {
    serve((_u, n) =>
      n === 1
        ? page([{ id: "k", headline: "Kept row" }])
        : Response.json({ error: { code: "boom", message: "Poll failed" } }, { status: 500 }),
    );
    mount();
    await settle();
    await poll();
    expect(screen.getByText("Kept row")).toBeTruthy();
    expect(screen.getByText(/Couldn't refresh/)).toBeTruthy();
  });

  it("pages with Next and keeps page one on screen while page two loads", async () => {
    let release: (r: Response) => void = () => {};
    serve((u) => {
      if (u.searchParams.get("offset") === "0") {
        return page([{ id: "p1", headline: "First page" }], 30);
      }
      return new Promise<Response>((resolve) => {
        release = resolve;
      });
    });
    mount();
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await settle();
    expect(urls.at(-1)!.searchParams.get("offset")).toBe("25");
    expect(screen.getByText("First page")).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
    await act(async () => {
      release(page([{ id: "p2", headline: "Second page" }], 30, 25));
    });
    await settle();
    expect(screen.getByText("Second page")).toBeTruthy();
  });

  it("steps back to the last page when decisions shrink the total past the offset", async () => {
    let shrunk = false;
    serve((u) => {
      const offset = Number(u.searchParams.get("offset"));
      if (offset === 0) {
        return shrunk
          ? page([{ id: "last", headline: "Only row left" }], 10, 0)
          : page([{ id: "a" }], 30);
      }
      if (shrunk) return page([], 10, offset);
      shrunk = true;
      return page([{ id: "b" }], 30, offset);
    });
    mount();
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await settle();
    await poll();
    await settle();
    expect(screen.getByText("Only row left")).toBeTruthy();
    expect(screen.queryByText("No rows on this page")).toBeNull();
  });
});
