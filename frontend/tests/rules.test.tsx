// @vitest-environment jsdom
// F11 guards: FE-26, SEC-27.
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
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RulesTable } from "../src/components/rules/rules-table";
import { makeQueryClient } from "../src/lib/query-client";
import { installDomStubs } from "./dom-stubs";

const rules = [
  {
    name: "bulk-delete",
    outcome: "block",
    reason: "Deletes <script>alert(1)</script> too much",
    pass: "post",
    conditions: { statement_in: ["delete", "update"], fraction_of_table_gt: 0.5 },
  },
  {
    name: "no-where",
    outcome: "review",
    reason: "No WHERE",
    pass: "pre",
    conditions: { has_where: false },
  },
  { name: "tiny", outcome: "allow", reason: "Small", pass: "post", conditions: {} },
];

function mount(respond: () => Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => respond()),
  );
  const client = makeQueryClient(vi.fn());
  client.setDefaultOptions({ queries: { retry: false } });
  render(
    <QueryClientProvider client={client}>
      <RulesTable />
    </QueryClientProvider>,
  );
}

beforeEach(() => installDomStubs());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the rules table", () => {
  it("shows every rule read-only, reasons as plain text", async () => {
    mount(() => Response.json({ items: rules, total: 3, limit: 100, offset: 0 }));
    const table = within(await screen.findByRole("table"));
    for (const r of rules) {
      expect(table.getByText(r.name)).toBeTruthy();
      expect(table.getByText(r.reason)).toBeTruthy();
    }
    expect(table.getByText("statement_in: delete, update")).toBeTruthy();
    expect(table.getByText("fraction_of_table_gt: 0.5")).toBeTruthy();
    expect(table.getByText("has_where: false")).toBeTruthy();
    expect(table.getAllByText("After preview")).toHaveLength(2);
    expect(table.getByText("Before preview")).toBeTruthy();
    expect(table.getByText("Block")).toBeTruthy();
    expect(table.getByText("Review")).toBeTruthy();
    expect(table.getByText("Allow")).toBeTruthy();
    expect(document.querySelector("table script")).toBeNull();
    expect(screen.queryAllByRole("button")).toEqual([]);
    expect(screen.queryAllByRole("textbox")).toEqual([]);
  });

  it("shows an empty state", async () => {
    mount(() => Response.json({ items: [], total: 0, limit: 100, offset: 0 }));
    expect(await screen.findByText("No rules loaded")).toBeTruthy();
  });

  it("shows an error", async () => {
    mount(() => Response.json({ error: { code: "x", message: "Rules broke" } }, { status: 500 }));
    expect(await screen.findByText("Rules broke")).toBeTruthy();
  });
});
