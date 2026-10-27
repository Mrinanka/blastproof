// @vitest-environment jsdom
// F4 guards: SEC-30.
vi.hoisted(() => {
  const NodeRequest = globalThis.Request;
  class BrowserRequest extends NodeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === "string" ? new URL(input, "http://localhost") : input, init);
    }
  }
  globalThis.Request = BrowserRequest;
});

const toastError = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: { error: toastError } }));

import { QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { UserMenu } from "../src/components/layout/user-menu";
import { TooltipProvider } from "../src/components/ui/tooltip";
import { SidebarProvider } from "../src/components/ui/sidebar";
import { makeQueryClient } from "../src/lib/query-client";
import { installDomStubs } from "./dom-stubs";

const assign = vi.fn();
const ME = { id: "u1", email: "a@example.com", display_name: "Demo One", role: "member" };

function stub(logoutStatus: number | "network") {
  vi.stubGlobal("location", { assign });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string) => {
      const url = typeof input === "string" ? input : input.url;
      if (url.includes("/api/auth/logout")) {
        if (logoutStatus === "network") throw new TypeError("offline");
        return new Response(null, { status: logoutStatus });
      }
      return Response.json(ME);
    }),
  );
}

function renderMenu() {
  const client = makeQueryClient(vi.fn());
  const clear = vi.spyOn(client, "clear");
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <SidebarProvider>
          <UserMenu />
        </SidebarProvider>
      </TooltipProvider>
    </QueryClientProvider>,
  );
  return { clear };
}

async function openAndLogout() {
  const trigger = await screen.findByRole("button", { name: /Demo One/ });
  fireEvent.keyDown(trigger, { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Log out" }));
}

beforeEach(() => installDomStubs());
afterEach(() => {
  cleanup();
  assign.mockReset();
  toastError.mockReset();
  vi.unstubAllGlobals();
});

describe("UserMenu", () => {
  it("shows the display name", async () => {
    stub(204);
    renderMenu();
    expect(await screen.findByText("Demo One")).toBeTruthy();
  });

  it("clears the cache and goes to /login after a 204", async () => {
    stub(204);
    const { clear } = renderMenu();
    await openAndLogout();
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/login"));
    expect(clear).toHaveBeenCalled();
  });

  it.each([403, 500, "network"] as const)(
    "stays put and shows an error toast on %s",
    async (status) => {
      stub(status);
      const { clear } = renderMenu();
      await openAndLogout();
      await waitFor(() => expect(toastError).toHaveBeenCalledWith("Could not log out. Try again."));
      expect(assign).not.toHaveBeenCalled();
      expect(clear).not.toHaveBeenCalled();
    },
  );
});
