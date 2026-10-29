// @vitest-environment jsdom
// F4 guards: FE-09, FE-10, FE-11, DW-F04 (stand-in).
vi.hoisted(() => {
  const NodeRequest = globalThis.Request;
  class BrowserRequest extends NodeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === "string" ? new URL(input, "http://localhost") : input, init);
    }
  }
  globalThis.Request = BrowserRequest;
});

const pathname = vi.hoisted(() => ({ value: "/dashboard" }));
vi.mock("next/navigation", () => ({ usePathname: () => pathname.value }));

import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppSidebar } from "../src/components/layout/app-sidebar";
import { TooltipProvider } from "../src/components/ui/tooltip";
import { SidebarProvider } from "../src/components/ui/sidebar";
import { makeQueryClient } from "../src/lib/query-client";
import { installDomStubs } from "./dom-stubs";

type Role = "admin" | "member";
const me = (role: Role) => ({ id: "u1", email: "a@example.com", display_name: "Demo One", role });

interface Routes {
  me?: () => Response;
  totals?: () => number | Response;
}

function route({ me: meRoute, totals }: Routes) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string) => {
      const path = new URL(typeof input === "string" ? input : input.url, "http://x").pathname;
      if (path.endsWith("/v1/auth/me")) return meRoute!();
      if (path.endsWith("/v1/actions")) {
        const t = totals!();
        return typeof t === "number"
          ? Response.json({ items: [], total: t, limit: 1, offset: 0 })
          : t;
      }
      return new Response(null, { status: 404 });
    }),
  );
}

function renderSidebar() {
  const client = makeQueryClient(vi.fn());
  client.setDefaultOptions({ queries: { retry: false } });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <SidebarProvider>
          <AppSidebar />
        </SidebarProvider>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

const link = (name: string) => screen.queryByRole("link", { name });

beforeEach(() => {
  installDomStubs();
  pathname.value = "/dashboard";
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("AppSidebar", () => {
  it("shows Knowledge to a member and hides Users", async () => {
    route({ me: () => Response.json(me("member")), totals: () => 0 });
    renderSidebar();
    await screen.findByText("Demo One");
    expect(link("Review")).not.toBeNull();
    expect(link("Knowledge")).not.toBeNull();
    expect(link("Users")).toBeNull();
  });

  it("shows Knowledge and Users to an admin", async () => {
    route({ me: () => Response.json(me("admin")), totals: () => 0 });
    renderSidebar();
    // Users is the admin-only link, so it is the one that proves the role has loaded.
    expect(await screen.findByRole("link", { name: "Users" })).toBeTruthy();
    expect(link("Knowledge")).not.toBeNull();
  });

  it("shows no admin-only links while /me is pending or fails", async () => {
    route({ me: () => new Response(null, { status: 500 }), totals: () => 0 });
    renderSidebar();
    await screen.findByRole("link", { name: "Review" });
    await new Promise((r) => setTimeout(r, 20));
    expect(link("Users")).toBeNull();
  });

  it("shows the pending count on Review", async () => {
    route({ me: () => Response.json(me("member")), totals: () => 3 });
    renderSidebar();
    expect(await screen.findByText("3")).toBeTruthy();
  });

  it("shows no badge for zero or on a 500", async () => {
    route({ me: () => Response.json(me("member")), totals: () => 0 });
    const first = renderSidebar();
    await screen.findByText("Demo One");
    expect(document.querySelector('[data-slot="sidebar-menu-badge"]')).toBeNull();
    first.unmount();
    route({
      me: () => Response.json(me("member")),
      totals: () => new Response(null, { status: 500 }),
    });
    renderSidebar();
    await screen.findByText("Demo One");
    expect(document.querySelector('[data-slot="sidebar-menu-badge"]')).toBeNull();
  });

  it("marks the current page", async () => {
    pathname.value = "/dashboard/review";
    route({ me: () => Response.json(me("member")), totals: () => 0 });
    renderSidebar();
    await screen.findByText("Demo One");
    expect(link("Review")!.getAttribute("aria-current")).toBe("page");
    expect(link("Overview")!.getAttribute("aria-current")).toBeNull();
  });

  it("updates the badge after 5 s and not before (DW-F04)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    let total = 2;
    route({ me: () => Response.json(me("member")), totals: () => total });
    renderSidebar();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("2")).toBeTruthy();
    total = 3;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(screen.queryByText("3")).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(screen.getByText("3")).toBeTruthy();
  });
});
