// @vitest-environment jsdom
// F7 guards: FE-15 to FE-18, FE-20, DW-F07 (stand-in), SEC-27, ARCH-22.
vi.hoisted(() => {
  const NodeRequest = globalThis.Request;
  class BrowserRequest extends NodeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === "string" ? new URL(input, "http://localhost") : input, init);
    }
  }
  globalThis.Request = BrowserRequest;
});

const toast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock("sonner", () => ({ toast }));

import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChangeRequest } from "../src/components/change-request/change-request";
import type { ActionDetail } from "../src/hooks/use-action";
import { ACTION_POLL_MS } from "../src/hooks/use-action";
import { makeQueryClient } from "../src/lib/query-client";
import { ACTION_ID, makeAction } from "./action-detail-fixture";
import { installDomStubs } from "./dom-stubs";

interface Call {
  method: string;
  path: string;
  body: string;
}
const calls: Call[] = [];
let client: QueryClient;

type Handler = (call: Call, n: number) => Response | Promise<Response>;
function serve(handler: Handler) {
  calls.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      const call = {
        method: input.method,
        path: new URL(input.url).pathname.replace("/api/blastproof", ""),
        body: await input.text(),
      };
      calls.push(call);
      return handler(call, calls.length);
    }),
  );
}
const gets = () => calls.filter((c) => c.method === "GET" && c.path.startsWith("/v1/actions/"));
const posts = () => calls.filter((c) => c.method === "POST");
const detail = (a: ActionDetail) => Response.json(a);
const error = (status: number, message: string) =>
  Response.json({ error: { code: "e", message } }, { status });

function mount() {
  client = makeQueryClient(vi.fn());
  client.setDefaultOptions({ queries: { retry: false } });
  render(
    <QueryClientProvider client={client}>
      <ChangeRequest id={ACTION_ID} />
    </QueryClientProvider>,
  );
}
const settle = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
const poll = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ACTION_POLL_MS + 100);
  });

beforeEach(() => {
  installDomStubs();
  vi.useFakeTimers({ shouldAdvanceTime: false });
  toast.error.mockClear();
  toast.success.mockClear();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("ChangeRequest layout", () => {
  it("shows the sections in the planned order with their data", async () => {
    serve(() => detail(makeAction()));
    mount();
    await settle();
    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings).toEqual([
      "Impact",
      "Intent and SQL",
      "Impact numbers",
      "Sample rows",
      "AI check",
      "Rules matched",
      "Knowledge used",
      "Timeline",
    ]);
    const text = document.body.textContent ?? "";
    for (const expected of [
      "Changes the plan of 48,213 users",
      "Every user is touched",
      "12 paying customers lose access",
      "48,213",
      "free48,201",
      "paying",
      "plan != free",
      "a***@example.com",
      "87%",
      "fake / judge-1",
      "bulk-update",
      "Touches most of the table",
      "schema.md",
      "users holds one row per customer",
      "Awaiting review",
      "Demo One",
      "claude-1",
    ]) {
      expect(text).toContain(expected);
    }
    expect(screen.getAllByText("cascade").length).toBe(1);
    expect(screen.getByText("orders").textContent).toContain("cascade");
  });

  it("puts the decision bar between the impact and the SQL", async () => {
    serve(() => detail(makeAction()));
    mount();
    await settle();
    const bar = screen.getByRole("button", { name: "Approve" });
    const impact = screen.getByRole("heading", { name: "Impact" });
    const sql = screen.getByRole("heading", { name: "Intent and SQL" });
    expect(impact.compareDocumentPosition(bar) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(bar.compareDocumentPosition(sql) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("lists pinned values with the original SQL beside the pinned one", async () => {
    serve(() =>
      detail(
        makeAction({
          sql_display: "UPDATE t SET at = '2026-10-24 12:00 UTC'",
          original_sql_display: "UPDATE t SET at = now()",
          pinned_values: { "now()": "2026-10-24 12:00 UTC" },
        }),
      ),
    );
    mount();
    await settle();
    expect(screen.getByText("now() → 2026-10-24 12:00 UTC")).toBeTruthy();
    expect(screen.getByText("UPDATE t SET at = now()")).toBeTruthy();
    expect(screen.getByText("Original")).toBeTruthy();
  });

  it("shows neither pinned values nor an original when there are none", async () => {
    serve(() => detail(makeAction()));
    mount();
    await settle();
    expect(screen.queryByText("Original")).toBeNull();
    expect(screen.queryByText("Pinned values")).toBeNull();
  });

  it("links to the retried and retrying actions", async () => {
    const a = "22222222-2222-4222-8222-222222222222";
    const b = "33333333-3333-4333-8333-333333333333";
    serve(() => detail(makeAction({ retry_of: a, retried_by: b })));
    mount();
    await settle();
    expect(screen.getByRole("link", { name: "the earlier request" }).getAttribute("href")).toBe(
      `/dashboard/actions/${a}`,
    );
    expect(screen.getByRole("link", { name: "a later request" }).getAttribute("href")).toBe(
      `/dashboard/actions/${b}`,
    );
  });

  it("falls back when parts are missing", async () => {
    serve(() =>
      detail(
        makeAction({
          impact_report: null,
          table_changes: [],
          breakdowns: [],
          derived_groups: [],
          samples: [],
          judge: null,
          matched_rules: [],
          knowledge_used: [],
        }),
      ),
    );
    mount();
    await settle();
    for (const text of [
      "Summary not ready",
      "No preview",
      "No sample rows",
      "The AI check did not run",
      "No rules matched",
      "No knowledge used",
    ]) {
      expect(screen.getByText(text)).toBeTruthy();
    }
  });

  it("shows a judge failure and a withheld reason", async () => {
    const base = makeAction().judge!;
    serve(() => detail(makeAction({ judge: { ...base, failure: "timeout" } })));
    mount();
    await settle();
    expect(screen.getByText(/AI check unavailable \(timeout\)/)).toBeTruthy();
    cleanup();
    serve(() => detail(makeAction({ judge: { ...base, reason: null } })));
    mount();
    await settle();
    expect(screen.getByText("Reason withheld")).toBeTruthy();
  });

  it("renders agent text as literal text (SEC-27)", async () => {
    const evil = "<img src=x onerror=alert(1)>";
    const base = makeAction();
    serve(() =>
      detail(
        makeAction({
          intent: evil,
          sql_display: evil,
          reviewer_note: evil,
          impact_report: { ...base.impact_report!, headline: evil, key_points: [evil] },
          samples: [{ table: "users", change: "updated", side: "before", values: { name: evil } }],
          matched_rules: [{ name: "r", outcome: "review", reason: evil, pass: "pre" }],
          knowledge_used: [{ ...base.knowledge_used[0], first_line: evil }],
        }),
      ),
    );
    mount();
    await settle();
    expect((document.body.textContent ?? "").split(evil).length - 1).toBeGreaterThanOrEqual(7);
    expect(document.querySelector("img")).toBeNull();
  });
});

describe("ChangeRequest states", () => {
  it("says not found with a queue link on 404, and stops polling", async () => {
    serve(() => error(404, "Action not found"));
    mount();
    await settle();
    expect(screen.getByText("Action not found")).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "Back to the review queue" }).getAttribute("href"),
    ).toBe("/dashboard/review");
    await poll();
    expect(gets().length).toBe(1);
  });

  it("shows the error and Retry on a 500", async () => {
    serve(() => error(500, "Database is down"));
    mount();
    await settle();
    expect(screen.getByText("Database is down")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("updates in place to committed on the next poll, then stops", async () => {
    serve((_c, n) => detail(makeAction(n === 1 ? {} : { status: "committed", can_decide: false })));
    mount();
    await settle();
    expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy();
    await poll();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.getAllByText("Committed").length).toBeGreaterThan(0);
    const n = gets().length;
    await poll();
    expect(gets().length).toBe(n);
  });
});

describe("decision bar", () => {
  it.each([
    ["awaiting_review", false],
    ["approved", true],
    ["rejected", false],
  ] as const)("is hidden for %s (can_decide %s)", async (status, canDecide) => {
    serve(() => detail(makeAction({ status, can_decide: canDecide })));
    mount();
    await settle();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject…" })).toBeNull();
  });

  it("is shown for awaiting_review with can_decide", async () => {
    serve(() => detail(makeAction()));
    mount();
    await settle();
    expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reject…" })).toBeTruthy();
  });

  it("approves once even on a double click, disables while pending, and refreshes everything", async () => {
    let release: (r: Response) => void = () => {};
    let approved = false;
    serve((c) => {
      if (c.method === "POST") {
        return new Promise<Response>((resolve) => {
          release = (r) => {
            approved = true;
            resolve(r);
          };
        });
      }
      if (c.path === "/v1/actions")
        return Response.json({ items: [], total: 0, limit: 1, offset: 0 });
      return detail(makeAction(approved ? { status: "approved", can_decide: false } : {}));
    });
    mount();
    await settle();
    client.setQueryData(["actions", "pending-count"], 1);
    client.setQueryData(["actions", "list", { status: ["awaiting_review"] }], { total: 1 });
    const button = screen.getByRole("button", { name: "Approve" });
    fireEvent.click(button);
    fireEvent.click(button);
    await settle();
    expect(posts().length).toBe(1);
    expect(posts()[0].path).toBe(`/v1/actions/${ACTION_ID}/approve`);
    expect((screen.getByRole("button", { name: "Approving…" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect((screen.getByRole("button", { name: "Reject…" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(client.isFetching({ queryKey: ["actions"] })).toBe(0);
    await act(async () => release(Response.json(makeAction({ status: "approved" }))));
    await settle();
    expect(toast.success).toHaveBeenCalledWith("Approved");
    expect(gets().length).toBeGreaterThanOrEqual(2);
    expect(client.getQueryState(["actions", "pending-count"])?.isInvalidated).toBe(true);
    expect(
      client.getQueryState(["actions", "list", { status: ["awaiting_review"] }])?.isInvalidated,
    ).toBe(true);
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("toasts the backend's 409 text verbatim and refreshes the stale page", async () => {
    let decided = false;
    serve((c) => {
      if (c.method === "POST") {
        decided = true;
        return error(409, "Already approved by Demo User 2.");
      }
      return detail(makeAction(decided ? { status: "approved", can_decide: false } : {}));
    });
    mount();
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await settle();
    expect(toast.error).toHaveBeenCalledWith("Already approved by Demo User 2.");
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("toasts a 503 and a network error, and re-enables the buttons", async () => {
    let mode: 503 | "network" = 503;
    serve((c) => {
      if (c.method === "POST") {
        if (mode === "network") throw new TypeError("offline");
        return error(503, "Service busy");
      }
      return detail(makeAction());
    });
    mount();
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await settle();
    expect(toast.error).toHaveBeenLastCalledWith("Service busy");
    mode = "network";
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await settle();
    expect(toast.error).toHaveBeenLastCalledWith("Could not reach the server.");
    expect((screen.getByRole("button", { name: "Approve" }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it("toasts a 409 on reject and the bar goes away after the refresh", async () => {
    let decided = false;
    serve((c) => {
      if (c.method === "POST") {
        decided = true;
        return error(409, "Already approved by Demo User 2.");
      }
      return detail(makeAction(decided ? { status: "approved", can_decide: false } : {}));
    });
    mount();
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Reject…" }));
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "not on prod data" } });
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    await settle();
    await settle();
    expect(toast.error).toHaveBeenCalledWith("Already approved by Demo User 2.");
    expect(screen.queryByRole("button", { name: "Reject…" })).toBeNull();
  });

  it("rejects with the trimmed reason", async () => {
    serve((c) =>
      c.method === "POST" ? detail(makeAction({ status: "rejected" })) : detail(makeAction()),
    );
    mount();
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Reject…" }));
    fireEvent.change(screen.getByLabelText("Reason"), {
      target: { value: "  not on prod data  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    await settle();
    await settle();
    expect(posts().length).toBe(1);
    expect(posts()[0].path).toBe(`/v1/actions/${ACTION_ID}/reject`);
    expect(JSON.parse(posts()[0].body)).toEqual({ reason: "not on prod data" });
    expect(toast.success).toHaveBeenCalledWith("Rejected");
  });
});
