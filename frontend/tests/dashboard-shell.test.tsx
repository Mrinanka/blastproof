// @vitest-environment jsdom
// F12 guards: FE-27 (keyboard: skip link to the main area).
vi.hoisted(() => {
  const NodeRequest = globalThis.Request;
  class BrowserRequest extends NodeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === "string" ? new URL(input, "http://localhost") : input, init);
    }
  }
  globalThis.Request = BrowserRequest;
});
vi.mock("next/server", () => ({ connection: async () => {} }));
vi.mock("next/headers", () => ({ cookies: async () => ({ get: () => undefined }) }));
vi.mock("next/navigation", async () => (await import("./router-stub")).navigation);
// A client render cannot await a server component; the provider is covered in theme-nonce.test.tsx.
vi.mock("../src/components/theme/nonced-theme-provider", () => ({
  NoncedThemeProvider: ({ children }: { children: React.ReactNode }) => children,
}));

import { QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DashboardLayout from "../src/app/dashboard/layout";
import { makeQueryClient } from "../src/lib/query-client";
import { expectNoAxeViolations } from "./a11y";
import { installDomStubs } from "./dom-stubs";

async function mountShell() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ id: "u", email: "a@x.io", display_name: "Demo One", role: "member" }),
    ),
  );
  // The layout is an async server component: resolve it, then render the element it returns.
  const element = await DashboardLayout({ children: <h1>Page body</h1> } as never);
  return render(
    <QueryClientProvider client={makeQueryClient(vi.fn())}>{element}</QueryClientProvider>,
  );
}

beforeEach(() => installDomStubs());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("dashboard shell", () => {
  it("makes the skip link the first tab stop, pointing at the main area", async () => {
    const { container } = await mountShell();
    const skip = screen.getByRole("link", { name: "Skip to content" });
    const tabbable = container.querySelector<HTMLElement>(
      'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    expect(tabbable).toBe(skip);

    const target = document.getElementById(skip.getAttribute("href")!.slice(1));
    expect(target).not.toBeNull();
    expect(target?.getAttribute("tabindex")).toBe("-1");
    expect(target?.tagName).toBe("MAIN");
    // What the browser does when the link is activated.
    target!.focus();
    expect(document.activeElement).toBe(target);
  });

  it("has no axe violations, with the skip link", async () => {
    const { container } = await mountShell();
    await screen.findByText("Demo One");
    await expectNoAxeViolations(container);
  });
});
