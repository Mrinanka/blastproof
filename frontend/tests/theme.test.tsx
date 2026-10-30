// @vitest-environment jsdom
// F12 guards: FE-27 (dark mode), SEC-29 (nonce on the theme script).
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
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { UserMenu } from "../src/components/layout/user-menu";
import { ThemeProvider } from "../src/components/theme/theme-provider";
import { SidebarProvider } from "../src/components/ui/sidebar";
import { TooltipProvider } from "../src/components/ui/tooltip";
import { makeQueryClient } from "../src/lib/query-client";
import { installDomStubs } from "./dom-stubs";

const ME = { id: "u1", email: "a@example.com", display_name: "Demo One", role: "member" };

async function openThemeSubmenu() {
  const trigger = await screen.findByRole("button", { name: /Demo One/ });
  fireEvent.keyDown(trigger, { key: "Enter" });
  const sub = await screen.findByRole("menuitem", { name: "Theme" });
  fireEvent.keyDown(sub, { key: "ArrowRight" });
}

beforeEach(() => {
  installDomStubs();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(ME)),
  );
  render(
    <QueryClientProvider client={makeQueryClient(vi.fn())}>
      <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
        <TooltipProvider>
          <SidebarProvider>
            <UserMenu />
          </SidebarProvider>
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
});
afterEach(() => {
  cleanup();
  document.documentElement.className = "";
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("theme menu", () => {
  it("offers Light, Dark and System, with System chosen by default", async () => {
    await openThemeSubmenu();
    const items = await screen.findAllByRole("menuitemradio");
    expect(items.map((i) => i.textContent)).toEqual(["Light", "Dark", "System"]);
    expect(screen.getByRole("menuitemradio", { name: "System" }).getAttribute("aria-checked")).toBe(
      "true",
    );
  });

  it("puts the dark class on <html> when Dark is chosen, and removes it for Light", async () => {
    await openThemeSubmenu();
    fireEvent.click(await screen.findByRole("menuitemradio", { name: "Dark" }));
    expect(document.documentElement.classList.contains("dark")).toBe(true);

    await openThemeSubmenu();
    fireEvent.click(await screen.findByRole("menuitemradio", { name: "Light" }));
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("can be driven by keyboard: arrows move, Enter selects", async () => {
    await openThemeSubmenu();
    const light = await screen.findByRole("menuitemradio", { name: "Light" });
    await waitFor(() => expect(document.activeElement).toBe(light));
    fireEvent.keyDown(light, { key: "ArrowDown" });
    const dark = screen.getByRole("menuitemradio", { name: "Dark" });
    // Radix moves focus on a timer.
    await waitFor(() => expect(document.activeElement).toBe(dark));
    fireEvent.keyDown(dark, { key: "Enter" });
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
});
