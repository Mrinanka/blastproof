// F3 guards: CONV-14, SEC-23.
import { readdirSync, readFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = resolve(__dirname, "../src");

const files = readdirSync(SRC, { withFileTypes: true, recursive: true })
  .filter((e) => e.isFile() && /\.(ts|tsx)$/.test(e.name) && !e.name.endsWith(".d.ts"))
  .map((e) => {
    const path = join(e.parentPath, e.name);
    return { name: relative(SRC, path).replaceAll("\\", "/"), text: readFileSync(path, "utf8") };
  });

describe("source rules", () => {
  it("reads BLASTPROOF_API_URL in exactly one file", () => {
    const readers = files.filter((f) => f.text.includes("BLASTPROOF_API_URL")).map((f) => f.name);
    expect(readers).toEqual(["lib/server/backend.ts"]);
  });

  it.each(["document.cookie", "sessionStorage", "localStorage", "NEXT_PUBLIC_"])(
    "never uses %s",
    (bad) => {
      // shadcn's sidebar keeps its own open/closed state in a cookie; it is not a credential.
      const found = files.filter(
        (f) => f.text.includes(bad) && f.name !== "components/ui/sidebar.tsx",
      );
      expect(found.map((f) => f.name)).toEqual([]);
    },
  );

  it("never uses dangerouslySetInnerHTML", () => {
    expect(files.filter((f) => f.text.includes("dangerouslySetInnerHTML"))).toEqual([]);
  });

  // F5 (CONV-17): number, money and date formatting lives in lib/format.ts.
  it("formats with Intl and toLocale* only in lib/format.ts", () => {
    const found = files.filter(
      (f) =>
        f.name !== "lib/format.ts" &&
        !f.name.startsWith("components/ui/") &&
        /toLocale(String|DateString|TimeString)|new Intl\./.test(f.text),
    );
    expect(found.map((f) => f.name)).toEqual([]);
  });

  // F4: shadcn's SidebarProvider no longer wraps a TooltipProvider; without one the sidebar crashes.
  it("wraps the dashboard layout in a TooltipProvider", () => {
    const layout = files.find((f) => f.name === "app/dashboard/layout.tsx");
    expect(layout?.text).toMatch(/<TooltipProvider>[\s\S]*<SidebarProvider/);
  });

  // F8: a flex child grows to fit its content; without min-w-0 a wide table widened the page.
  it("lets the dashboard's main area shrink below its content width", () => {
    const layout = files.find((f) => f.name === "app/dashboard/layout.tsx");
    expect(layout?.text).toMatch(/<SidebarInset className="[^"]*\bmin-w-0\b/);
  });

  // F11: the key dialog's copy field is the one place that writes to the clipboard.
  it("uses navigator.clipboard only in the agents copy field", () => {
    const found = files.filter((f) => f.text.includes("navigator.clipboard"));
    expect(found.map((f) => f.name)).toEqual(["components/agents/copy-field.tsx"]);
  });

  // F8: collapsed to icons, the name ran under the sidebar trigger.
  it("hides the product name when the sidebar is collapsed to icons", () => {
    const sidebar = files.find((f) => f.name === "components/layout/app-sidebar.tsx");
    expect(sidebar?.text).toMatch(/group-data-\[collapsible=icon\]:hidden">Blastproof</);
  });

  // F12: a raw palette color (text-red-600) has no dark-mode pair unless it is written with one.
  // tone.ts pairs each with a dark: class and is checked by contrast.test.ts. The landing page uses
  // its own tokens (F13).
  it("uses raw Tailwind palette colors only in tone.ts", () => {
    const palette =
      /\b(?:text|bg|border|ring|fill|stroke|from|to|via)-(?:red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|slate|gray|zinc|neutral|stone)-\d{2,3}\b/;
    const found = files.filter(
      (f) =>
        palette.test(f.text) &&
        f.name !== "components/actions/tone.ts" &&
        !f.name.startsWith("components/ui/"),
    );
    expect(found.map((f) => f.name)).toEqual([]);
    const tone = files.find((f) => f.name === "components/actions/tone.ts")!;
    const lightFills = tone.text
      .split(/\r?\n/)
      .filter((l) => /\bbg-(?:blue|amber|emerald|red)-/.test(l));
    for (const line of lightFills) {
      expect(line, "every light palette class has a dark: pair").toMatch(/dark:bg-/);
    }
  });

  // F13 (D-290): Motion and the landing components are for the public page only.
  it("imports motion and the landing components only from the landing page", () => {
    const users = files
      .filter((f) =>
        /(?:from|import)\s*\(?["'](?:motion(?:\/[\w-]+)?|framer-motion|@\/components\/landing\/[^"']+)["']/.test(
          f.text,
        ),
      )
      .map((f) => f.name)
      .filter((name) => !name.startsWith("components/landing/") && name !== "app/page.tsx");
    expect(users).toEqual([]);
  });

  // D-304: one component loads Vercel Analytics, with the redactor; the layout gates it.
  it("imports the Vercel analytics packages only in the analytics component", () => {
    const users = files.filter((f) => /["']@vercel\/(?:analytics|speed-insights)/.test(f.text));
    expect(users.map((f) => f.name)).toEqual(["components/analytics/vercel-analytics.tsx"]);
  });

  it("renders the analytics only behind the production check", () => {
    const layout = files.find((f) => f.name === "app/layout.tsx");
    expect(layout?.text).toMatch(
      /collectsAnalytics\(process\.env\.VERCEL_ENV\) && <VercelAnalytics \/>/,
    );
  });

  it("uses only the lazy `m` components from Motion (LazyMotion strict)", () => {
    const full = files.filter((f) => /\bmotion\.[a-z]+\b|<motion\./.test(f.text));
    expect(full.map((f) => f.name)).toEqual([]);
  });
});
