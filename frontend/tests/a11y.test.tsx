// @vitest-environment jsdom
// F12 guards: FE-27 (labels and names on every control; keyboard focus).
vi.hoisted(() => {
  const NodeRequest = globalThis.Request;
  class BrowserRequest extends NodeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === "string" ? new URL(input, "http://localhost") : input, init);
    }
  }
  globalThis.Request = BrowserRequest;
});
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("next/navigation", async () => (await import("./router-stub")).navigation);

import { QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Agents } from "../src/components/agents/agents";
import { ChangeRequest } from "../src/components/change-request/change-request";
import { LoginForm } from "../src/components/auth/login-form";
import { NotFoundMessage } from "../src/components/common/not-found-message";
import { History } from "../src/components/history/history";
import { Knowledge } from "../src/components/knowledge/knowledge";
import { AppSidebar } from "../src/components/layout/app-sidebar";
import { UserMenu } from "../src/components/layout/user-menu";
import { Overview } from "../src/components/overview/overview";
import { ReviewQueue } from "../src/components/review/review-queue";
import { RulesTable } from "../src/components/rules/rules-table";
import { ThemeProvider } from "../src/components/theme/theme-provider";
import { SidebarProvider } from "../src/components/ui/sidebar";
import { TooltipProvider } from "../src/components/ui/tooltip";
import { Users } from "../src/components/users/users";
import { makeQueryClient } from "../src/lib/query-client";
import { ACTION_ID, makeAction } from "./action-detail-fixture";
import { expectNoAxeViolations } from "./a11y";
import { installDomStubs } from "./dom-stubs";

const when = "2026-10-29T10:00:00Z";
const list = (items: unknown[]) => ({ items, total: items.length, limit: 100, offset: 0 });
const summary = (id: string) => ({
  id,
  status: "awaiting_review",
  decision: null,
  risk_level: "high",
  agent: { id: "ag1", name: "claude-1" },
  intent: "Clean up",
  headline: "Updates 3 rows",
  tables: ["users"],
  created_at: when,
  decided_by: null,
});

interface World {
  role: string;
  actions: unknown[];
  action: unknown;
  missing: boolean;
}

function serve(over: Partial<World> = {}) {
  const world: World = {
    role: "admin",
    actions: [summary("a1"), summary("a2")],
    action: makeAction(),
    missing: false,
    ...over,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (req: Request) => {
      const path = new URL(req.url).pathname.replace("/api/blastproof", "");
      if (path === "/v1/auth/me") {
        return Response.json({
          id: "u",
          email: "a@x.io",
          display_name: "Demo One",
          role: world.role,
        });
      }
      if (path === "/v1/stats") {
        return Response.json({
          pending_reviews: 2,
          by_status: {},
          by_decision: {},
          today: {
            day: "2026-10-29",
            actions: 4,
            by_status: {},
            by_decision: { allow: 2 },
            reviewed: 1,
            blocked: 1,
            avg_preview_ms: 900,
            llm_cost_usd: 0.01,
          },
        });
      }
      if (path === "/v1/actions") return Response.json(list(world.actions));
      if (path.startsWith("/v1/actions/")) {
        if (world.missing) {
          return Response.json(
            { error: { code: "not_found", message: "Not found" } },
            { status: 404 },
          );
        }
        return Response.json(world.action);
      }
      if (path === "/v1/knowledge") {
        return Response.json(
          list([
            {
              source: "schema_scan",
              kind: "schema_scan",
              chunks: 12,
              table_names: ["users"],
              updated_at: when,
            },
            { source: "notes.md", kind: "upload", chunks: 2, table_names: [], updated_at: when },
          ]),
        );
      }
      if (path === "/v1/systems") {
        return Response.json(
          list([{ id: "s1", name: "Company", kind: "postgres", connection_profile: "local" }]),
        );
      }
      if (path === "/v1/agents") {
        return Response.json(
          list([
            {
              id: "a1",
              name: "bot",
              key_prefix: "bp_ab12",
              is_active: true,
              created_at: when,
              owner: { id: "u1", display_name: "Olga Owner" },
            },
          ]),
        );
      }
      if (path === "/v1/policies") {
        return Response.json(
          list([
            {
              name: "no-where",
              outcome: "review",
              reason: "No WHERE",
              pass: "pre",
              conditions: { has_where: false },
            },
          ]),
        );
      }
      if (path === "/v1/users") {
        return Response.json(
          list([
            {
              id: "u1",
              email: "boss@x.io",
              display_name: "Ann",
              role: "admin",
              is_active: true,
              created_at: when,
            },
          ]),
        );
      }
      return Response.json({ error: { code: "x", message: "unexpected" } }, { status: 500 });
    }),
  );
}

function page(node: ReactNode) {
  const client = makeQueryClient(vi.fn());
  client.setDefaultOptions({ queries: { retry: false } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

beforeEach(() => installDomStubs());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("axe: no violations", () => {
  it("Overview", async () => {
    serve();
    const { container } = page(<Overview />);
    await screen.findAllByText("Updates 3 rows");
    await expectNoAxeViolations(container);
  });

  it("Review queue with rows", async () => {
    serve();
    const { container } = page(<ReviewQueue />);
    await screen.findAllByText("Updates 3 rows");
    await expectNoAxeViolations(container);
  });

  it("Review queue, empty", async () => {
    serve({ actions: [] });
    const { container } = page(<ReviewQueue />);
    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
    await expectNoAxeViolations(container);
  });

  it("Change request, pending, with the decision bar", async () => {
    serve();
    const { container } = page(<ChangeRequest id={ACTION_ID} />);
    await screen.findByRole("button", { name: "Reject…" });
    await expectNoAxeViolations(container);
  });

  it("Change request, reject dialog open", async () => {
    serve();
    page(<ChangeRequest id={ACTION_ID} />);
    fireEvent.click(await screen.findByRole("button", { name: "Reject…" }));
    await screen.findByRole("dialog");
    await expectNoAxeViolations(document.body);
  });

  it("Change request, decided", async () => {
    serve({ action: makeAction({ status: "committed", decision: "allow" }) });
    const { container } = page(<ChangeRequest id={ACTION_ID} />);
    await screen.findByText("Changes the plan of 48,213 users");
    await expectNoAxeViolations(container);
  });

  it("Change request, not found (another user's action)", async () => {
    serve({ missing: true });
    const { container } = page(<ChangeRequest id={ACTION_ID} />);
    await screen.findByText(/not found/i);
    await expectNoAxeViolations(container);
  });

  it("History with filters", async () => {
    serve();
    const { container } = page(<History />);
    await screen.findAllByText("Updates 3 rows");
    await expectNoAxeViolations(container);
  });

  it.each(["admin", "member"])("Knowledge as %s", async (role) => {
    serve({ role });
    const { container } = page(<Knowledge />);
    await screen.findByText("notes.md");
    await expectNoAxeViolations(container);
  });

  it("Agents, with the deactivate dialog open", async () => {
    serve({ role: "member" });
    const { container } = page(<Agents />);
    await screen.findByText("bot");
    await expectNoAxeViolations(container);
    fireEvent.click(await screen.findByRole("button", { name: /deactivate/i }));
    await screen.findByRole("dialog");
    await expectNoAxeViolations(document.body);
  });

  it("Rules", async () => {
    serve();
    const { container } = page(<RulesTable />);
    await screen.findByText("no-where");
    await expectNoAxeViolations(container);
  });

  it("Users as an admin, with the create form", async () => {
    serve({ role: "admin" });
    const { container } = page(<Users />);
    await screen.findByText("boss@x.io");
    await expectNoAxeViolations(container);
  });

  it("Users as a member", async () => {
    serve({ role: "member" });
    const { container } = page(<Users />);
    await waitFor(() => expect(screen.queryByText("boss@x.io")).toBeNull());
    await expectNoAxeViolations(container);
  });

  it("Login form, including its error state", async () => {
    vi.stubGlobal("fetch", vi.fn());
    const { container } = render(<LoginForm />);
    await expectNoAxeViolations(container);
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByText("Enter a valid email address.");
    await expectNoAxeViolations(container);
  });

  it("the user menu with the theme submenu open", async () => {
    serve();
    page(
      <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
        <TooltipProvider>
          <SidebarProvider>
            <UserMenu />
          </SidebarProvider>
        </TooltipProvider>
      </ThemeProvider>,
    );
    fireEvent.keyDown(await screen.findByRole("button", { name: /Demo One/ }), { key: "Enter" });
    fireEvent.keyDown(await screen.findByRole("menuitem", { name: "Theme" }), {
      key: "ArrowRight",
    });
    await screen.findByRole("menuitemradio", { name: "Dark" });
    await expectNoAxeViolations(document.body);
  });

  it("the 404 body", async () => {
    const { container } = render(<NotFoundMessage />);
    await expectNoAxeViolations(container);
  });

  it("the sidebar", async () => {
    serve();
    const { container } = page(
      <TooltipProvider>
        <SidebarProvider>
          <AppSidebar />
        </SidebarProvider>
      </TooltipProvider>,
    );
    await screen.findByText("Demo One");
    await expectNoAxeViolations(container);
  });
});
