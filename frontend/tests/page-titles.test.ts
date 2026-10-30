// F12 guards: FE-27 (page titles).
import { readdirSync, readFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/font/google", () => ({
  Geist: () => ({ variable: "" }),
  Geist_Mono: () => ({ variable: "" }),
}));

const APP = resolve(__dirname, "../src/app");

const EXPECTED: Record<string, string> = {
  "dashboard/page.tsx": "Overview",
  "dashboard/review/page.tsx": "Review queue",
  "dashboard/actions/[id]/page.tsx": "Change request",
  "dashboard/history/page.tsx": "History",
  "dashboard/knowledge/page.tsx": "Knowledge",
  "dashboard/settings/agents/page.tsx": "Agents",
  "dashboard/settings/rules/page.tsx": "Rules",
  "dashboard/settings/users/page.tsx": "Users",
  "login/page.tsx": "Sign in",
};

describe("page titles", () => {
  it("the root layout adds the product name as a template", async () => {
    vi.stubGlobal("React", undefined);
    const { metadata } = await import("../src/app/layout");
    expect(metadata.title).toEqual({ template: "%s · Blastproof", default: "Blastproof" });
  });

  // next-themes sets the class on <html> before hydration; without this React warns on every load.
  it("the root layout tells React the <html> class may differ on the client", () => {
    expect(readFileSync(join(APP, "layout.tsx"), "utf8")).toMatch(
      /<html[^>]*suppressHydrationWarning/,
    );
  });

  it.each(Object.entries(EXPECTED))("%s is titled %s", async (file, title) => {
    const source = readFileSync(join(APP, file), "utf8");
    // Plain titles only: the template adds the name, so a literal "· Blastproof" would double it.
    expect(source).toContain(`export const metadata = { title: "${title}" };`);
  });

  it("every dashboard page exports metadata, so a new page cannot forget its title", () => {
    const pages = readdirSync(join(APP, "dashboard"), { withFileTypes: true, recursive: true })
      .filter((e) => e.isFile() && e.name === "page.tsx")
      .map((e) => relative(APP, join(e.parentPath, e.name)).replaceAll("\\", "/"))
      // The catch-all only calls notFound(); not-found.tsx carries the 404 title.
      .filter((name) => !name.includes("[...missing]"));
    expect(pages.sort()).toEqual(
      Object.keys(EXPECTED)
        .filter((f) => f.startsWith("dashboard"))
        .sort(),
    );
    for (const page of pages) {
      expect(readFileSync(join(APP, page), "utf8"), page).toMatch(/export const metadata\b/);
    }
  });
});
