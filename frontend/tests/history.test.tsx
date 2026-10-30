// @vitest-environment jsdom
// F8 guards: FE-21, CONV-12, DW-F08 (stand-in: filters in the URL, back and forward), SEC-27,
// ARCH-22 (no owner or user parameter: the backend decides what you see).
vi.hoisted(() => {
  const NodeRequest = globalThis.Request;
  class BrowserRequest extends NodeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === "string" ? new URL(input, "http://localhost") : input, init);
    }
  }
  globalThis.Request = BrowserRequest;
});

vi.mock("next/navigation", async () => (await import("./router-stub")).navigation);

import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { History } from "../src/components/history/history";
import type { Schemas } from "../src/lib/api/client";
import { makeQueryClient } from "../src/lib/query-client";
import { installDomStubs } from "./dom-stubs";
import { PATH, router } from "./router-stub";

const A1 = "11111111-1111-4111-8111-111111111111";
const A2 = "22222222-2222-4222-8222-222222222222";

interface Item {
  id: string;
  status?: Schemas["ActionStatus"];
  agent?: string;
  intent?: string | null;
  headline?: string | null;
  tables?: string[];
  decidedBy?: string | null;
}

// Typed against the generated schema, so a fixture cannot drift from the real response shape.
const row = ({
  id,
  status = "committed",
  agent = "agent-a",
  intent = "Fix plans",
  headline = `Headline ${id}`,
  tables = ["users"],
  decidedBy = null,
}: Item): Schemas["ActionSummary"] => ({
  id,
  status,
  decision: null,
  risk_level: "low",
  agent: { id: A1, name: agent },
  intent,
  headline,
  tables,
  created_at: "2026-10-29T10:00:00Z",
  decided_by: decidedBy ? { id: "u9", display_name: decidedBy } : null,
});

const page = (items: Item[], total = items.length, offset = 0) =>
  Response.json({ items: items.map(row), total, limit: 25, offset });

const agent = (id: string, name: string, owner: string, active = true): Schemas["AgentView"] => ({
  id,
  name,
  key_prefix: "bp_x",
  is_active: active,
  created_at: "2026-10-01T00:00:00Z",
  owner: { id: `o-${id}`, display_name: owner },
});

const AGENTS = [agent(A1, "agent-a", "Demo One"), agent(A2, "agent-b", "Demo Two", false)];

let role = "member";
let agentsResponse: () => Response = () =>
  Response.json({ items: AGENTS, total: 2, limit: 100, offset: 0 });
let actionsHandler: (url: URL) => Response | Promise<Response> = () => page([]);
const actionUrls: URL[] = [];

function serve() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      const url = new URL(input.url);
      if (url.pathname.endsWith("/v1/auth/me")) {
        return Response.json({ id: "u1", email: "a@example.com", display_name: "Me", role });
      }
      if (url.pathname.endsWith("/v1/agents")) return agentsResponse();
      actionUrls.push(url);
      return actionsHandler(url);
    }),
  );
}

function mount(initial = "") {
  router.reset(initial);
  serve();
  const client = makeQueryClient(vi.fn());
  client.setDefaultOptions({ queries: { retry: false } });
  render(
    <QueryClientProvider client={client}>
      <History />
    </QueryClientProvider>,
  );
}

const lastActions = () => actionUrls.at(-1)!;
const settle = () => act(async () => new Promise((r) => setTimeout(r, 0)));
const agentSelect = () => screen.getByLabelText("Agent") as HTMLSelectElement;
const tableInput = () => screen.getByLabelText("Table") as HTMLInputElement;
const pushed = () => router.push.mock.calls.map(([href]) => href);

// An open Radix menu hides everything else from the accessibility tree, the trigger included.
const statusButton = (name: string | RegExp) => screen.getByRole("button", { name, hidden: true });

async function toggleStatus(label: string) {
  const trigger = statusButton(/^Status/);
  if (trigger.getAttribute("aria-expanded") !== "true") {
    fireEvent.keyDown(trigger, { key: "Enter" });
  }
  fireEvent.click(await screen.findByRole("menuitemcheckbox", { name: label }));
}

beforeEach(() => {
  installDomStubs();
  role = "member";
  agentsResponse = () => Response.json({ items: AGENTS, total: 2, limit: 100, offset: 0 });
  actionsHandler = () => page([]);
  actionUrls.length = 0;
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("History: the URL drives the request", () => {
  it("sends every filter from the URL, with no owner or user parameter", async () => {
    mount(
      `status=rejected&status=blocked&agent=${A1}&table=users&from=2026-10-01&to=2026-10-29&page=2`,
    );
    await waitFor(() => expect(actionUrls.length).toBe(1));
    const params = lastActions().searchParams;
    expect([...params.keys()].sort()).toEqual(
      ["agent_id", "from", "limit", "offset", "order", "status", "status", "table", "to"].sort(),
    );
    expect(params.getAll("status")).toEqual(["rejected", "blocked"]);
    expect(params.get("agent_id")).toBe(A1);
    expect(params.get("table")).toBe("users");
    expect(params.get("from")).toBe(new Date(2026, 9, 1).toISOString());
    expect(params.get("to")).toBe(new Date(2026, 9, 30).toISOString());
    expect(params.get("order")).toBe("newest");
    expect(params.get("limit")).toBe("25");
    expect(params.get("offset")).toBe("25");
  });

  it("drops a malformed agent from the URL instead of sending it", async () => {
    mount("agent=nope&table=users");
    await waitFor(() => expect(actionUrls.length).toBe(1));
    expect(lastActions().searchParams.has("agent_id")).toBe(false);
    expect(lastActions().searchParams.get("table")).toBe("users");
  });

  it("shows a message and sends nothing when the range runs backwards", async () => {
    mount("from=2026-10-29&to=2026-10-01");
    expect(await screen.findByText("The start date is after the end date.")).toBeTruthy();
    await settle();
    expect(actionUrls).toEqual([]);
  });
});

describe("History: rows", () => {
  it("shows status, agent, summary, tables and decider, and links to the action", async () => {
    actionsHandler = () =>
      page([
        {
          id: "r1",
          status: "rejected",
          agent: "claude-1",
          headline: "Deletes 3 users",
          intent: "Clean up",
          tables: ["users", "orders"],
          decidedBy: "Demo One",
        },
      ]);
    mount();
    const table = within(await screen.findByRole("table"));
    expect(table.getByText("Rejected")).toBeTruthy();
    expect(table.getByText("claude-1")).toBeTruthy();
    expect(table.getByText("Clean up")).toBeTruthy();
    expect(table.getByText("users, orders")).toBeTruthy();
    expect(table.getByText("Demo One")).toBeTruthy();
    expect(table.getByText("Low risk")).toBeTruthy();
    expect(document.querySelector("time")).not.toBeNull();
    expect(table.getByRole("link", { name: "Deletes 3 users" }).getAttribute("href")).toBe(
      "/dashboard/actions/r1",
    );
  });

  it("falls back for a missing headline, intent, tables and decider", async () => {
    actionsHandler = () => page([{ id: "n", headline: null, intent: null, tables: [] }]);
    mount();
    const table = within(await screen.findByRole("table"));
    expect(table.getByRole("link", { name: "Summary not ready" })).toBeTruthy();
    expect(table.getAllByText("—").length).toBe(2);
  });

  it("renders agent text as literal text", async () => {
    const evil = "<img src=x onerror=alert(1)>";
    actionsHandler = () => page([{ id: "x", agent: evil, intent: evil, headline: evil }]);
    mount();
    await screen.findByRole("table");
    expect(screen.getAllByText(evil).length).toBe(3);
    expect(document.querySelector("img")).toBeNull();
  });

  it("says no actions yet when nothing is filtered, and Clear filters is off", async () => {
    mount();
    expect(await screen.findByText("No actions yet")).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Clear filters" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("says nothing matches when filtered, and Clear filters goes to the bare page", async () => {
    mount("table=users&page=1");
    expect(await screen.findByText("No actions match these filters")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(pushed()).toEqual([PATH]);
  });

  it("shows the backend's message and Retry on a 500", async () => {
    actionsHandler = () =>
      Response.json({ error: { code: "boom", message: "Database is down" } }, { status: 500 });
    mount();
    expect(await screen.findByText("Database is down")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("says the server could not be reached on a network error", async () => {
    actionsHandler = () => {
      throw new TypeError("failed");
    };
    mount();
    expect(await screen.findByText("Could not reach the server.")).toBeTruthy();
  });
});

describe("History: changing filters", () => {
  it("pushes the agent and drops the page", async () => {
    mount("table=users&page=3");
    await screen.findByRole("option", { name: "agent-b (inactive)" });
    fireEvent.change(agentSelect(), { target: { value: A2 } });
    expect(router.push).toHaveBeenLastCalledWith(`${PATH}?agent=${A2}&table=users`, {
      scroll: false,
    });
    await waitFor(() => expect(lastActions().searchParams.get("agent_id")).toBe(A2));
    expect(lastActions().searchParams.get("offset")).toBe("0");
  });

  it("pushes the table on Enter or blur, never while typing, trimmed", async () => {
    mount();
    await settle();
    fireEvent.change(tableInput(), { target: { value: "  orders " } });
    expect(router.push).not.toHaveBeenCalled();
    fireEvent.keyDown(tableInput(), { key: "Enter" });
    expect(pushed()).toEqual([`${PATH}?table=orders`]);
    fireEvent.change(tableInput(), { target: { value: "" } });
    fireEvent.blur(tableInput());
    expect(pushed()).toEqual([`${PATH}?table=orders`, PATH]);
  });

  it("does not push when the committed table is unchanged", async () => {
    mount("table=users");
    await settle();
    fireEvent.keyDown(tableInput(), { key: "Enter" });
    fireEvent.blur(tableInput());
    expect(router.push).not.toHaveBeenCalled();
  });

  it("pushes the dates, and clears one when emptied", async () => {
    mount();
    await settle();
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-10-01" } });
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-10-29" } });
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "" } });
    expect(pushed()).toEqual([
      `${PATH}?from=2026-10-01`,
      `${PATH}?from=2026-10-01&to=2026-10-29`,
      `${PATH}?to=2026-10-29`,
    ]);
  });

  it("does not push a half-typed year", async () => {
    mount();
    await settle();
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "0202-10-01" } });
    expect(router.push).not.toHaveBeenCalled();
    expect((screen.getByLabelText("From") as HTMLInputElement).value).toBe("0202-10-01");
  });

  it("toggles statuses in canonical order and labels the button", async () => {
    mount();
    await settle();
    expect(statusButton("Status: All")).toBeTruthy();
    await toggleStatus("Blocked");
    // The menu stays open, so several statuses can be picked in a row.
    expect(statusButton(/^Status/).getAttribute("aria-expanded")).toBe("true");
    await toggleStatus("Rejected");
    expect(pushed()).toEqual([`${PATH}?status=blocked`, `${PATH}?status=rejected&status=blocked`]);
    expect(statusButton("Status: 2 selected")).toBeTruthy();
    await toggleStatus("Blocked");
    expect(pushed().at(-1)).toBe(`${PATH}?status=rejected`);
    expect(statusButton("Status: Rejected")).toBeTruthy();
  });
});

describe("History: back and forward restore the view (DW-F08)", () => {
  it("restores filters, the table text box and the rows", async () => {
    actionsHandler = (url) => {
      const label = [url.searchParams.get("table"), url.searchParams.get("agent_id")].join("|");
      return page([{ id: "v", headline: `View ${label}` }]);
    };
    mount();
    expect(await screen.findByText("View |")).toBeTruthy();
    await screen.findByRole("option", { name: "agent-a" });

    fireEvent.change(tableInput(), { target: { value: "users" } });
    fireEvent.keyDown(tableInput(), { key: "Enter" });
    expect(await screen.findByText("View users|")).toBeTruthy();
    fireEvent.change(agentSelect(), { target: { value: A1 } });
    expect(await screen.findByText(`View users|${A1}`)).toBeTruthy();

    // A draft typed but not committed is replaced by the URL's value on back.
    fireEvent.change(tableInput(), { target: { value: "half-typed" } });
    act(() => router.back());
    expect(await screen.findByText("View users|")).toBeTruthy();
    expect(agentSelect().value).toBe("");
    expect(tableInput().value).toBe("users");

    act(() => router.back());
    expect(await screen.findByText("View |")).toBeTruthy();
    expect(tableInput().value).toBe("");

    act(() => router.forward());
    act(() => router.forward());
    expect(await screen.findByText(`View users|${A1}`)).toBeTruthy();
    expect(agentSelect().value).toBe(A1);
    expect(tableInput().value).toBe("users");
  });

  it("pages with Next as a new history entry, keeps page one while loading, and back returns", async () => {
    let release: (r: Response) => void = () => {};
    actionsHandler = (url) => {
      if (url.searchParams.get("offset") === "0") return page([{ id: "p1" }], 60);
      return new Promise<Response>((resolve) => {
        release = resolve;
      });
    };
    mount();
    expect(await screen.findByText("Headline p1")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(pushed()).toEqual([`${PATH}?page=2`]);
    await waitFor(() => expect(lastActions().searchParams.get("offset")).toBe("25"));
    expect(screen.getByText("Headline p1")).toBeTruthy();
    await act(async () => release(page([{ id: "p2" }], 60, 25)));
    expect(await screen.findByText("Headline p2")).toBeTruthy();

    act(() => router.back());
    expect(await screen.findByText("Headline p1")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Previous" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("replaces a past-the-end page with the last page, adding no history entry", async () => {
    actionsHandler = (url) => {
      const offset = Number(url.searchParams.get("offset"));
      return offset >= 30 ? page([], 30, offset) : page([{ id: "last" }], 30, offset);
    };
    mount("table=users&page=9");
    expect(await screen.findByText("Headline last")).toBeTruthy();
    expect(router.replace).toHaveBeenCalledWith(`${PATH}?table=users&page=2`, { scroll: false });
    expect(router.push).not.toHaveBeenCalled();
    expect(lastActions().searchParams.get("offset")).toBe("25");
  });

  it("does not clamp a deep page against the previous view's total while it loads", async () => {
    let release: (r: Response) => void = () => {};
    actionsHandler = (url) => {
      if (url.searchParams.get("table") === "small") return page([{ id: "s" }], 10);
      return new Promise<Response>((resolve) => {
        release = resolve;
      });
    };
    mount("table=small");
    expect(await screen.findByText("Headline s")).toBeTruthy();
    act(() => router.push(`${PATH}?table=big&page=3`));
    await waitFor(() => expect(lastActions().searchParams.get("offset")).toBe("50"));
    await act(async () => release(page([{ id: "b3" }], 60, 50)));
    expect(await screen.findByText("Headline b3")).toBeTruthy();
    expect(router.replace).not.toHaveBeenCalled();
    expect(router.search).toBe("table=big&page=3");
  });

  it("replaces a page past an empty result with page one", async () => {
    mount("table=users&page=3");
    expect(await screen.findByText("No actions match these filters")).toBeTruthy();
    expect(router.replace).toHaveBeenCalledWith(`${PATH}?table=users`, { scroll: false });
  });
});

describe("History: layout", () => {
  it("gives the filter controls the default height, so the labels line up", async () => {
    mount();
    await settle();
    const controls = [
      statusButton(/^Status/),
      agentSelect(),
      screen.getByRole("button", { name: "Clear filters" }),
    ];
    for (const control of controls) expect(control.getAttribute("data-size")).toBe("default");
  });

  it("wraps the summary column instead of pushing the last columns off screen", async () => {
    actionsHandler = () => page([{ id: "w" }]);
    mount();
    const cell = (await screen.findByRole("link", { name: "Headline w" })).closest("td")!;
    expect(cell.className).toContain("whitespace-normal");
    expect(screen.getByRole("columnheader", { name: "Summary" }).className).toContain(
      "whitespace-normal",
    );
  });
});

describe("History: agent options", () => {
  it("shows a member's agents by name, marking inactive ones", async () => {
    mount();
    expect(await screen.findByRole("option", { name: "agent-a" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "agent-b (inactive)" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "All agents" })).toBeTruthy();
  });

  it("adds the owner's name for an admin", async () => {
    role = "admin";
    mount();
    expect(await screen.findByRole("option", { name: "agent-a (Demo One)" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "agent-b (Demo Two, inactive)" })).toBeTruthy();
  });

  it("keeps an agent from the URL that is not in the list selected, as unknown", async () => {
    const other = "33333333-3333-4333-8333-333333333333";
    mount(`agent=${other}`);
    expect(await screen.findByRole("option", { name: "Unknown agent" })).toBeTruthy();
    expect(agentSelect().value).toBe(other);
  });

  it("disables the agent filter when agents fail to load, and still lists actions", async () => {
    agentsResponse = () =>
      Response.json({ error: { code: "boom", message: "Down" } }, { status: 500 });
    actionsHandler = () => page([{ id: "still" }]);
    mount();
    expect(await screen.findByText("Headline still")).toBeTruthy();
    await waitFor(() => expect(agentSelect().disabled).toBe(true));
    expect(screen.getByText("Could not load agents.")).toBeTruthy();
  });
});
