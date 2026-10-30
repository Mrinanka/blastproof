// F12 guards: SEC-29 (nonce on next-themes' inline script).
// Server-render environment: next-themes blanks the nonce attribute when `window` exists, as a
// browser would hide it, so this cannot run under jsdom.
const headerList = vi.hoisted(() => ({ current: new Headers() }));
vi.mock("next/headers", () => ({ headers: async () => headerList.current }));

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { NoncedThemeProvider } from "../src/components/theme/nonced-theme-provider";

describe("NoncedThemeProvider", () => {
  const html = async () =>
    renderToStaticMarkup(await NoncedThemeProvider({ children: <p>child</p> }));

  it("puts the proxy's nonce on the theme script", async () => {
    headerList.current = new Headers({ "x-nonce": "abc123" });
    expect(await html()).toMatch(/<script[^>]*nonce="abc123"/);
  });

  it("renders without a nonce, and does not crash, when the header is missing", async () => {
    headerList.current = new Headers();
    const out = await html();
    expect(out).toContain("<p>child</p>");
    expect(out).not.toContain("nonce=");
  });
});
