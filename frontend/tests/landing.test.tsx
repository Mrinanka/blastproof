// @vitest-environment jsdom
// F13 guards: FE-28 (the landing page's sections, links and text), FE-29 (measured numbers only).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, render, screen, within } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Home from "../src/app/page";
import { plain } from "../src/components/landing/mono-text";
import { FAQ, FLOW, INCIDENTS, REPLIT } from "../src/lib/landing/content";
import { evalStats } from "../src/lib/landing/eval-stats";
import { DEMO_HREF, GITHUB_URL, HEADLINE } from "../src/lib/landing/site";
import { expectNoAxeViolations } from "./a11y";
import { installDomStubs } from "./dom-stubs";

const SECTIONS = [
  "problem",
  "how-it-works",
  "change-request",
  "different",
  "results",
  "architecture",
  "security",
  "faq",
];

beforeEach(() => {
  installDomStubs();
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
      takeRecords() {
        return [];
      }
    },
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const renderPage = () => render(<Home />).container;
const text = (el: Element) => (el.textContent ?? "").replace(/\s+/g, " ").trim();

describe("landing page structure", () => {
  it("has exactly one h1, the headline", () => {
    const root = renderPage();
    const h1s = root.querySelectorAll("h1");
    expect(h1s).toHaveLength(1);
    expect(text(h1s[0]!)).toBe(HEADLINE);
  });

  it.each(SECTIONS)("section #%s is labelled by its h2", (id) => {
    const root = renderPage();
    const section = root.querySelector(`section#${id}`)!;
    expect(section, id).not.toBeNull();
    const heading = root.querySelector(`#${section.getAttribute("aria-labelledby")}`)!;
    expect(heading.tagName).toBe("H2");
    expect(text(heading)).not.toBe("");
  });

  it("never skips a heading level", () => {
    const root = renderPage();
    const levels = [...root.querySelectorAll("h1, h2, h3, h4")].map((h) => Number(h.tagName[1]));
    expect(levels[0]).toBe(1);
    for (let i = 1; i < levels.length; i++) {
      expect(levels[i]! - levels[i - 1]!, `heading ${i}`).toBeLessThanOrEqual(1);
    }
  });

  it("has a skip link to the main landmark, and labelled landmarks", () => {
    const root = renderPage();
    const skip = screen.getByRole("link", { name: "Skip to content" });
    expect(skip.getAttribute("href")).toBe("#main-content");
    expect(root.querySelector("main#main-content")).not.toBeNull();
    expect(screen.getByRole("navigation", { name: "Main" })).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "Footer" })).toBeTruthy();
    expect(screen.getByRole("contentinfo")).toBeTruthy();
  });

  it("has no axe violations", async () => {
    await expectNoAxeViolations(renderPage());
  });
});

describe("landing page links", () => {
  it("every Log in link goes to /login", () => {
    renderPage();
    const links = screen.getAllByRole("link", { name: "Log in" });
    expect(links.length).toBeGreaterThanOrEqual(2);
    for (const link of links) expect(link.getAttribute("href")).toBe("/login");
  });

  it("the GitHub links go to the repository", () => {
    renderPage();
    const links = screen.getAllByRole("link", { name: /GitHub/ });
    expect(links.length).toBeGreaterThanOrEqual(2);
    for (const link of links) expect(link.getAttribute("href")).toBe(GITHUB_URL);
    expect(GITHUB_URL).toBe("https://github.com/Mrinanka/blastproof");
  });

  it("Watch the demo points at a section that exists", () => {
    const root = renderPage();
    const demo = screen.getByRole("link", { name: "Watch the demo" });
    expect(demo.getAttribute("href")).toBe(DEMO_HREF);
    expect(root.querySelector(DEMO_HREF)).not.toBeNull();
  });

  it("every nav anchor points at a section that exists", () => {
    const root = renderPage();
    const nav = screen.getByRole("navigation", { name: "Main" });
    const anchors = [...nav.querySelectorAll('a[href^="#"]')];
    expect(anchors.length).toBe(4);
    for (const a of anchors) expect(root.querySelector(a.getAttribute("href")!)).not.toBeNull();
  });

  it.each(INCIDENTS.map((i) => [i.when, i]))("the %s incident links to its source", (_w, i) => {
    const root = renderPage();
    const link = root.querySelector(`a[href="${i.url}"]`)!;
    expect(link, i.url).not.toBeNull();
    expect(i.url).toMatch(/^https:\/\//);
    expect(text(link)).toContain(i.source);
  });

  it("every link that opens a new tab has rel noopener noreferrer", () => {
    const root = renderPage();
    const external = [...root.querySelectorAll('a[target="_blank"]')];
    expect(external.length).toBeGreaterThanOrEqual(INCIDENTS.length + 3);
    for (const a of external) {
      expect(a.getAttribute("rel"), a.getAttribute("href")!).toBe("noopener noreferrer");
    }
  });
});

describe("landing page content", () => {
  it("the hero replays the Replit delete with its exact SQL and counts", () => {
    const root = renderPage();
    const hero = root.querySelector('section[aria-labelledby="hero-title"]')!;
    expect(text(hero)).toContain(REPLIT.sql);
    expect(text(hero)).toContain("48,213 users");
    expect(text(hero)).toContain("12 paying customers");
    expect(text(hero)).toContain("Rejected by owner. Nothing changed.");
  });

  it("the FAQ answers are in the page while closed", () => {
    const root = renderPage();
    const faq = root.querySelector("section#faq")!;
    for (const item of FAQ) {
      expect(text(faq)).toContain(item.q);
      expect(text(faq)).toContain(item.a);
    }
    // Closed answers are present but hidden by CSS, not removed.
    const closed = faq.querySelectorAll('[data-slot="accordion-content"][data-state="closed"]');
    expect(closed).toHaveLength(FAQ.length);
    for (const el of closed) expect(el.className).toContain("data-[state=closed]:hidden");
  });

  // The server HTML must hold the exact final numbers (SEO, no-JS, reduced motion); the count-up
  // only rewinds a number that is below the fold once the page has hydrated.
  it("the server HTML shows the eval snapshot's final numbers and names the source", () => {
    const html = renderToString(<Home />);
    const results = text(
      Object.assign(document.createElement("div"), { innerHTML: html }).querySelector(
        "section#results",
      )!,
    );
    const s = evalStats();
    expect(results).toContain(`${s.incidentsCaught}/${s.incidents}`);
    expect(results).toContain(`${s.caught}/${s.dangerous}`);
    expect(results).toContain(`${s.falseBlockPercent}%`);
    expect(results).toContain(`rules-only eval, ${s.total} scenarios`);
    expect(results).not.toMatch(/\bms\b|latency of|p95/);
  });

  it("the architecture has every node of the README diagram, and a text version", () => {
    const root = renderPage();
    const arch = root.querySelector("section#architecture")!;
    const readme = readFileSync(resolve(__dirname, "../../README.md"), "utf8");
    const mermaid = readme.slice(
      readme.indexOf("```mermaid"),
      readme.indexOf("```", readme.indexOf("```mermaid") + 3),
    );
    const labels = [...mermaid.matchAll(/\[\(?"([^"<]+)/g)].map((m) => m[1]!.trim());
    expect(labels.length).toBe(9);
    for (const label of labels) expect(text(arch), label).toContain(label);
    const list = within(arch as HTMLElement)
      .getAllByRole("listitem")
      .map((li) => text(li));
    for (const line of FLOW)
      expect(
        list.some((l) => l.includes(line)),
        line,
      ).toBe(true);
  });

  it("renders backticked words in monospace without the backticks", () => {
    const root = renderPage();
    const steps = text(root.querySelector("section#how-it-works")!);
    expect(steps).not.toContain("`");
    expect(steps).toContain(plain("`PreToolUse`"));
    expect(root.querySelector("section#different code")?.textContent).toBe("MCP");
  });
});
